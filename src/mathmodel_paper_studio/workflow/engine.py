import hashlib
import os
from pathlib import Path
import threading

from mathmodel_paper_studio.bootstrap.paths import AppPaths, validate_managed_path
from mathmodel_paper_studio.domain.models import Project, Run, RunStatus, Stage, StageStatus
from mathmodel_paper_studio.execution.python_runner import RunArtifactLayout
from mathmodel_paper_studio.storage.repositories import ProjectRepository, RecordConflictError, RunRepository
from mathmodel_paper_studio.workflow.checkpoint import (
    CheckpointRecoveryError, CheckpointStore,
)
from mathmodel_paper_studio.workflow.context_builder import ContextBuilder, ContextInputError, StageContext
from mathmodel_paper_studio.workflow.retry_policy import RetryPolicy
from mathmodel_paper_studio.workflow.progress import ProgressKind, RunProgressReporter
from mathmodel_paper_studio.workflow.stages import (
    ArtifactDigest, PauseReason, StageOutput, StageResult, WorkflowState,
)

_MAX_OUTPUTS_PER_STAGE = 32
_MAX_TOTAL_OUTPUT_BYTES = 16 * 1024 * 1024


class WorkflowDeliveryConflictError(RuntimeError):
    pass


class WorkflowEngine:
    """Runs one ordered flow; the base production adapter always fails closed."""

    def __init__(self, paths, project_repository: ProjectRepository, run_repository: RunRepository,
                 *, retry_policy: RetryPolicy = RetryPolicy()):
        if not isinstance(paths, AppPaths):
            raise TypeError('工作流路径必须来自经过验证的 AppPaths。')
        if not isinstance(project_repository, ProjectRepository) or not isinstance(run_repository, RunRepository):
            raise TypeError('工作流需要项目和运行仓储。')
        if (paths.root != project_repository.database.paths.root
                or paths.root != run_repository.database.paths.root):
            raise ValueError('工作流、SQLite 仓储必须使用相同 AppPaths。')
        if not isinstance(retry_policy, RetryPolicy):
            raise TypeError('修复策略类型无效。')
        self.paths = paths
        self.projects = project_repository
        self.runs = run_repository
        self.retry_policy = retry_policy

    def start(self, project_id: str, run_id: str, sources, selected_source_ids,
              *, cancel_event: threading.Event | None = None,
              progress: RunProgressReporter | None = None) -> WorkflowState:
        self._validate_cancel_event(cancel_event)
        self._validate_progress(progress, run_id)
        project, run = self._records(project_id, run_id)
        if run.status is RunStatus.CREATED:
            project_root = validate_managed_path(self.paths.root / project.relative_path)
            run_root = validate_managed_path(project_root / run.relative_path)
            if run_root.exists():
                raise CheckpointRecoveryError('新建运行目录已存在但 SQLite 尚无已提交状态，拒绝重放。')
            layout = RunArtifactLayout.create(self.paths, project, run)
            state = WorkflowState.initial(run).start()
            store = CheckpointStore(layout, self.runs)
            store.persist(state, run)
        else:
            layout = RunArtifactLayout(self.paths, project, run)
            store = CheckpointStore(layout, self.runs)
            state = self._recover_or_pause(run, store)
            if run.status in (RunStatus.PAUSED, RunStatus.COMPLETED, RunStatus.CANCELLED, RunStatus.FAILED):
                return state
        self._emit_progress(progress, ProgressKind.RUN_STARTED, '运行已启动')
        return self._drive(project, run_id, sources, selected_source_ids,
                           layout, store, state, cancel_event, progress)

    def resume(self, run_id: str, sources, selected_source_ids,
               *, cancel_event: threading.Event | None = None,
               progress: RunProgressReporter | None = None) -> WorkflowState:
        self._validate_cancel_event(cancel_event)
        run = self.runs.get(run_id)
        if run is None:
            raise ValueError('运行记录不存在。')
        self._validate_progress(progress, run_id)
        project = self.projects.get(run.project_id)
        if project is None:
            raise ValueError('运行所属项目不存在。')
        layout = RunArtifactLayout(self.paths, project, run)
        store = CheckpointStore(layout, self.runs)
        state = self._recover_or_pause(run, store)
        if state.status in (RunStatus.COMPLETED, RunStatus.CANCELLED, RunStatus.FAILED):
            return state
        if state.status is RunStatus.PAUSED:
            state = state.resume()
            self._persist(store, state)
        self._emit_progress(progress, ProgressKind.RUN_STARTED, '运行已恢复')
        return self._drive(project, run_id, sources, selected_source_ids,
                           layout, store, state, cancel_event, progress)

    def cancel(self, run_id: str, *, progress: RunProgressReporter | None = None) -> WorkflowState | None:
        run = self.runs.get(run_id)
        if run is None:
            raise ValueError('运行记录不存在。')
        self._validate_progress(progress, run_id)
        if run.status is RunStatus.CREATED:
            self.runs.update(run.transition(RunStatus.CANCELLED), expected_status=RunStatus.CREATED)
            self._emit_progress(progress, ProgressKind.RUN_CANCELLED, '运行已取消')
            return None
        project = self.projects.get(run.project_id)
        if project is None:
            raise ValueError('运行所属项目不存在。')
        layout = RunArtifactLayout(self.paths, project, run)
        store = CheckpointStore(layout, self.runs)
        try:
            state = store.recover(run)
        except CheckpointRecoveryError:
            if run.status in (RunStatus.RUNNING, RunStatus.PAUSED):
                self.runs.update(run.transition(RunStatus.CANCELLED), expected_status=run.status)
                self._emit_progress(progress, ProgressKind.RUN_CANCELLED, '运行已取消')
            return None
        if state.status in (RunStatus.COMPLETED, RunStatus.CANCELLED, RunStatus.FAILED):
            return state
        cancelled = state.cancel()
        self._persist(store, cancelled)
        self._emit_progress(progress, ProgressKind.RUN_CANCELLED, '运行已取消')
        return cancelled

    def reconcile_duplicate_delivery(self, run_id: str, result: StageResult) -> WorkflowState:
        """Accept an exact replay of a committed stage event without writing outputs again."""
        if not isinstance(result, StageResult):
            raise TypeError('階段结果类型无效。')
        run = self.runs.get(run_id)
        if run is None:
            raise ValueError('运行记录不存在。')
        project = self.projects.get(run.project_id)
        if project is None:
            raise ValueError('运行所属项目不存在。')
        layout = RunArtifactLayout(self.paths, project, run)
        state = CheckpointStore(layout, self.runs).recover(run)
        matching = [record for record in state.stage_records
                    if record.stage is result.stage and record.attempt == result.attempt
                    and record.status is result.status]
        if not matching:
            raise WorkflowDeliveryConflictError('该阶段投递尚未提交，不能作为重复事件确认。')
        record = matching[-1]
        evidence = {item.evidence_id: item for item in state.artifacts}
        committed = [evidence[item] for item in record.evidence_ids if item in evidence]
        expected = sorted((Path(item.relative_path).name, item.kind, item.sha256) for item in committed)
        delivered = sorted((item.name, item.kind, hashlib.sha256(item.content).hexdigest())
                           for item in result.outputs)
        if expected != delivered:
            raise WorkflowDeliveryConflictError('重复阶段投递的内容与已提交工件不一致；未覆盖任何文件。')
        return state
    @staticmethod
    def _validate_cancel_event(cancel_event):
        if cancel_event is not None and not isinstance(cancel_event, threading.Event):
            raise TypeError('取消信号类型无效。')

    @staticmethod
    def _validate_progress(progress, run_id):
        if progress is not None and (not isinstance(progress, RunProgressReporter)
                                     or progress.run_id != run_id):
            raise TypeError('运行进度接收器与运行标识不匹配。')

    @staticmethod
    def _emit_progress(progress, kind, title, **kwargs):
        if progress is None:
            return
        try:
            progress.emit(kind, title, **kwargs)
        except Exception:
            # Rendering is observational and must never affect the workflow transaction.
            return

    def _emit_stage_started(self, progress, stage, attempt):
        self._emit_progress(progress, ProgressKind.STAGE_STARTED, f'{stage.value} 阶段开始',
                            stage=stage, attempt=attempt)
        if stage in (Stage.RESULT_VALIDATION, Stage.DOCX_QA):
            self._emit_progress(progress, ProgressKind.VALIDATION_STARTED, '开始校验阶段结果',
                                stage=stage, attempt=attempt)

    def _emit_stage_result(self, progress, result, artifacts):
        status_event = {
            StageStatus.COMPLETED: (ProgressKind.STAGE_COMPLETED, '阶段已完成'),
            StageStatus.PAUSED: (ProgressKind.STAGE_PAUSED, '阶段已暂停'),
            StageStatus.FAILED: (ProgressKind.STAGE_FAILED, '阶段执行失败'),
            StageStatus.CANCELLED: (ProgressKind.STAGE_CANCELLED, '阶段已取消'),
        }
        kind, title = status_event[result.status]
        detail = result.pause_reason.value if result.pause_reason is not None else ''
        self._emit_progress(progress, kind, title, stage=result.stage,
                            attempt=result.attempt, detail=detail)
        if result.stage in (Stage.RESULT_VALIDATION, Stage.DOCX_QA):
            if result.status is StageStatus.COMPLETED:
                validation_kind, validation_title = ProgressKind.VALIDATION_PASSED, '校验通过'
            elif result.status is StageStatus.CANCELLED:
                validation_kind, validation_title = None, ''
            else:
                validation_kind, validation_title = ProgressKind.VALIDATION_FAILED, '校验未通过'
            if validation_kind is not None:
                self._emit_progress(progress, validation_kind, validation_title,
                                    stage=result.stage, attempt=result.attempt)
        for artifact in artifacts:
            self._emit_progress(progress, ProgressKind.ARTIFACT_CREATED, '已提交阶段产物',
                                stage=result.stage, attempt=result.attempt,
                                detail=artifact.kind, artifact=artifact)

    def _emit_run_terminal(self, progress, state):
        terminal = {
            RunStatus.PAUSED: (ProgressKind.RUN_PAUSED, '运行已暂停'),
            RunStatus.CANCELLED: (ProgressKind.RUN_CANCELLED, '运行已取消'),
            RunStatus.FAILED: (ProgressKind.RUN_FAILED, '运行失败'),
            RunStatus.COMPLETED: (ProgressKind.RUN_COMPLETED, '运行已完成'),
        }.get(state.status)
        if terminal is not None:
            kind, title = terminal
            self._emit_progress(progress, kind, title, detail=(
                state.pause_reason.value if state.pause_reason is not None else ''))

    def _records(self, project_id: str, run_id: str) -> tuple[Project, Run]:
        project = self.projects.get(project_id)
        run = self.runs.get(run_id)
        if project is None or run is None or run.project_id != project.project_id:
            raise ValueError('项目和运行记录不匹配。')
        return project, run

    def _recover_or_pause(self, run: Run, store: CheckpointStore) -> WorkflowState:
        try:
            return store.recover(run)
        except CheckpointRecoveryError:
            if run.status is RunStatus.RUNNING:
                self.runs.update(run.transition(RunStatus.PAUSED), expected_status=RunStatus.RUNNING)
            raise

    def _persist(self, store: CheckpointStore, state: WorkflowState) -> None:
        current = self.runs.get(state.run_id)
        if current is None:
            raise RecordConflictError('运行记录已删除。')
        store.persist(state, current)

    def _drive(self, project: Project, run_id: str, sources, selected_source_ids,
               layout: RunArtifactLayout, store: CheckpointStore, state: WorkflowState,
               cancel_event: threading.Event | None,
               progress: RunProgressReporter | None = None) -> WorkflowState:
        if cancel_event is not None and not isinstance(cancel_event, threading.Event):
            raise TypeError('取消信号类型无效。')
        try:
            builder = ContextBuilder(
                project, layout.run, layout.project_root, sources,
                selected_source_ids=selected_source_ids,
            )
        except ContextInputError as exc:
            if state.next_stage is not None:
                self._emit_stage_started(progress, state.next_stage, state.next_attempt)
            result = StageResult(state.next_stage, state.next_attempt, StageStatus.PAUSED,
                                 diagnostic=str(exc), pause_reason=exc.reason)
            state = self._commit_result(state, result, (), layout, store)
            self._emit_stage_result(progress, result, ())
            self._emit_run_terminal(progress, state)
            return state

        while state.status is RunStatus.RUNNING and state.next_stage is not None:
            if cancel_event is not None and cancel_event.is_set():
                state = state.cancel()
                self._persist(store, state)
                self._emit_run_terminal(progress, state)
                return state
            stage, attempt = state.next_stage, state.next_attempt
            self._emit_stage_started(progress, stage, attempt)
            delivery = state.generation + 1
            relative_directory = f'stages/{stage.value}/attempt-{attempt:02d}-delivery-{delivery:06d}'
            try:
                context = builder.build(stage, attempt, upstream_artifacts=state.artifacts,
                                        output_directory=layout.path_for(relative_directory))
            except ContextInputError as exc:
                result = StageResult(stage, attempt, StageStatus.PAUSED,
                                     diagnostic=str(exc), pause_reason=exc.reason)
                state = self._commit_result(state, result, (), layout, store)
                self._emit_stage_result(progress, result, ())
                self._after_checkpoint(state)
                self._emit_run_terminal(progress, state)
                return state

            artifact_directory = layout.path_for(relative_directory)
            try:
                parent = validate_managed_path(artifact_directory.parent)
                parent.mkdir(parents=True, exist_ok=True)
                validate_managed_path(parent)
                validate_managed_path(artifact_directory)
                artifact_directory.mkdir(exist_ok=False)
                validate_managed_path(artifact_directory)
            except (OSError, ValueError):
                result = StageResult(stage, attempt, StageStatus.PAUSED,
                                     diagnostic='阶段产物目录已存在，状态不确定，未重放阶段。',
                                     pause_reason=PauseReason.OUTPUT_CONFLICT)
                state = self._commit_result(state, result, (), layout, store)
                self._emit_stage_result(progress, result, ())
                self._after_checkpoint(state)
                self._emit_run_terminal(progress, state)
                return state

            if cancel_event is not None and cancel_event.is_set():
                result = StageResult(stage, attempt, StageStatus.CANCELLED,
                                     diagnostic='用户在阶段启动前取消。')
            else:
                try:
                    result = self._execute_stage_with_progress(
                        context, artifact_directory, cancel_event, progress)
                    if (not isinstance(result, StageResult) or result.stage is not stage
                            or result.attempt != attempt):
                        raise ValueError('adapter returned mismatched stage delivery')
                except Exception as exc:
                    result = StageResult(stage, attempt, StageStatus.FAILED,
                                         diagnostic=f'阶段适配器异常：{type(exc).__name__}')
                if cancel_event is not None and cancel_event.is_set():
                    result = StageResult(stage, attempt, StageStatus.CANCELLED,
                                         diagnostic='用户取消了当前阶段。')

            if result.status is StageStatus.CANCELLED:
                state = self._commit_result(state, result, (), layout, store)
                self._emit_stage_result(progress, result, ())
                self._after_checkpoint(state)
                self._emit_run_terminal(progress, state)
                return state
            try:
                artifacts = self._write_outputs(layout, artifact_directory, state, result, delivery)
            except (OSError, ValueError):
                result = StageResult(stage, attempt, StageStatus.PAUSED,
                                     diagnostic='阶段产物冲突或写入边界无法确认，已停止且未覆盖文件。',
                                     pause_reason=PauseReason.OUTPUT_CONFLICT)
                artifacts = ()
            state = self._commit_result(state, result, artifacts, layout, store)
            self._emit_stage_result(progress, result, artifacts)
            self._after_checkpoint(state)
        self._emit_run_terminal(progress, state)
        return state

    def _write_outputs(self, layout: RunArtifactLayout, directory: Path, state: WorkflowState,
                       result: StageResult, delivery: int) -> tuple[ArtifactDigest, ...]:
        if len(result.outputs) > _MAX_OUTPUTS_PER_STAGE:
            raise ValueError('阶段产物数量超过上限。')
        if sum(len(item.content) for item in result.outputs) > _MAX_TOTAL_OUTPUT_BYTES:
            raise ValueError('阶段产物总字节数超过上限。')
        artifacts = []
        for output in result.outputs:
            path = validate_managed_path(directory / output.name)
            with path.open('xb') as stream:
                stream.write(output.content)
                stream.flush()
                os.fsync(stream.fileno())
            validate_managed_path(path)
            digest = hashlib.sha256(output.content).hexdigest()
            run_relative = path.relative_to(layout.root).as_posix()
            project_relative = f'{layout.run.relative_path}/{run_relative}'
            evidence_id = 'ev_' + hashlib.sha256(
                f'{layout.run.run_id}\0{project_relative}\0{digest}'.encode('utf-8')
            ).hexdigest()[:24]
            artifacts.append(ArtifactDigest(
                project_relative, digest, evidence_id, output.kind, result.stage,
                result.attempt, delivery, len(output.content),
            ))
        return tuple(artifacts)

    def _commit_result(self, state, result, artifacts, layout, store):
        new_state = state.record(result, artifacts, self.retry_policy)
        self._persist(store, new_state)
        return new_state

    def _execute_stage(self, context: StageContext, artifact_directory: Path,
                       cancel_event: threading.Event | None = None) -> StageResult:
        # No production provider, sandbox, or generated-code executor is configured in this version.
        return StageResult(
            context.stage, context.attempt, StageStatus.PAUSED,
            diagnostic='没有通过本次可信 preflight 的生产阶段适配器，运行已暂停。',
            pause_reason=(PauseReason.PREFLIGHT_FAILED if context.stage is Stage.PREFLIGHT
                          else PauseReason.ADAPTER_UNAVAILABLE),
        )

    def _execute_stage_with_progress(self, context, artifact_directory, cancel_event, progress):
        return self._execute_stage(context, artifact_directory, cancel_event)

    def _after_checkpoint(self, state: WorkflowState) -> None:
        """Test seam for abrupt stage-boundary termination; production does nothing."""
        return None
