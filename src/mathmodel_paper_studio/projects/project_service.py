"""Stage a complete new project, rename without replacement, then index it."""
from pathlib import Path
from contextlib import contextmanager
import hashlib
import json
import re
import shutil
import tempfile
import threading
from uuid import uuid4

from mathmodel_paper_studio.bootstrap.paths import AppPaths, validate_managed_path
from mathmodel_paper_studio.domain.models import Project, SourceFile
from mathmodel_paper_studio.inputs.import_service import ImportService
from mathmodel_paper_studio.inputs.profiling import ImportLimits, read_bounded
from mathmodel_paper_studio.projects.layout import create_layout
from mathmodel_paper_studio.projects.manifest import latest_manifest
from mathmodel_paper_studio.storage.repositories import ProjectRepository

_PROJECT_EDIT_LOCKS = {}
_PROJECT_EDIT_LOCKS_GUARD = threading.Lock()


def _project_edit_lock(root: Path):
    key = str(root).casefold()
    with _PROJECT_EDIT_LOCKS_GUARD:
        lock = _PROJECT_EDIT_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _PROJECT_EDIT_LOCKS[key] = lock
        return lock


@contextmanager
def _project_named_mutex(root: Path):
    """Serialize edits to one project across all desktop application processes."""
    import ctypes
    from ctypes import wintypes

    key = str(root).casefold()
    digest = hashlib.sha256(key.encode('utf-8')).hexdigest()
    name = f'Local\\MathModelPaperStudio.ProjectEdit.{digest}'
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel32.ReleaseMutex.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    acquired = False
    try:
        result = kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)
        if result not in (0x00000000, 0x00000080):
            raise ctypes.WinError(ctypes.get_last_error())
        acquired = True
        yield
    finally:
        try:
            if acquired:
                kernel32.ReleaseMutex(handle)
        finally:
            kernel32.CloseHandle(handle)


class ProjectService:
    def __init__(self, paths: AppPaths, repository: ProjectRepository, *, limits: ImportLimits = ImportLimits()):
        if not isinstance(paths, AppPaths) or not isinstance(repository, ProjectRepository):
            raise TypeError('项目服务需要 AppPaths 和 ProjectRepository。')
        if paths.root != repository.database.paths.root:
            raise ValueError('项目与数据库必须使用同一 AppPaths。')
        self.paths = paths
        self.repository = repository
        self.imports = ImportService(limits)

    def create(self, title: str, problem: str, sources, *, template=None, sheets=None,
               problem_document=None) -> Project:
        self.paths.ensure_directories()
        project = self._project(title)
        destination = validate_managed_path(self.paths.root / project.relative_path)
        if destination.exists():
            raise ValueError('项目目标已存在，请创建新项目。')
        selected = self.imports.validate_sources(sources, template)
        stage = Path(tempfile.mkdtemp(prefix='project-', dir=self.paths.temp))
        finalized = False
        try:
            create_layout(stage)
            self.imports.populate(stage, project.project_id, problem, selected, sheets=sheets,
                                  problem_document=problem_document)
            self.paths.validate()
            validate_managed_path(stage)
            validate_managed_path(destination)
            if destination.exists():
                raise ValueError('项目目标已存在，请创建新项目。')
            # Windows rename refuses an existing destination, including a race.
            stage.rename(destination)
            finalized = True
            self.repository.add(project)
            return project
        except BaseException:
            owned = destination if finalized else stage
            validate_managed_path(owned)
            if owned.exists():
                shutil.rmtree(owned)
            raise

    def delete(self, project_id: str) -> Project:
        """Delete a project and its run history, restoring files if the index rejects it."""
        project = self.repository.get(project_id)
        if project is None:
            raise ValueError('项目不存在或已被删除。')
        root = self.paths.root / project.relative_path
        if root.parent != self.paths.projects or root.name != project.project_id:
            raise ValueError('项目路径与项目记录不匹配，已拒绝删除。')

        temp_root = self.paths.temp
        with _project_edit_lock(root):
            with _project_named_mutex(root):
                root = validate_managed_path(root)
                temp_root = validate_managed_path(temp_root)
                if not temp_root.is_dir():
                    temp_root.mkdir(parents=True, exist_ok=True)
                    temp_root = validate_managed_path(temp_root)
                if root.exists() and not root.is_dir():
                    raise ValueError('项目目录路径被非目录文件占用，已拒绝删除。')
                staged = temp_root / f'project-delete-{project.project_id}-{uuid4().hex}'
                if staged.exists():
                    raise ValueError('临时删除目录发生冲突，已拒绝删除。')
                moved = False

                def stage_project_files(_project):
                    nonlocal moved
                    if root.exists():
                        root.rename(staged)
                        moved = True

                try:
                    deleted = self.repository.delete(project_id, before_delete=stage_project_files)
                    if deleted is None:
                        raise ValueError('项目记录已变化，请刷新列表后重试。')
                except BaseException:
                    if moved and staged.exists():
                        try:
                            staged.rename(root)
                        except OSError as restore_error:
                            raise OSError(
                                f'项目记录仍保留，但项目副本暂存于 {staged}，自动恢复失败。',
                            ) from restore_error
                    raise
                if moved:
                    try:
                        shutil.rmtree(staged)
                    except OSError as exc:
                        raise OSError(
                            f'项目已从列表移除，但临时文件清理失败：{staged}',
                        ) from exc
                return deleted

    def update_inputs(self, project: Project, problem: str, sources, *, template=None,
                      expected_revision: int, progress=None) -> int:
        if not isinstance(project, Project):
            raise TypeError('编辑项目需要 Project 记录。')
        root = validate_managed_path(self.paths.root / project.relative_path)
        with _project_edit_lock(root):
            with _project_named_mutex(root):
                return self._update_inputs_locked(
                    project, root, problem, sources, template=template,
                    expected_revision=expected_revision, progress=progress,
                )

    def _update_inputs_locked(self, project: Project, root: Path, problem: str, sources, *,
                              template=None, expected_revision: int, progress=None) -> int:
        revision, _manifest_path, manifest = latest_manifest(root)
        if type(expected_revision) is not int or expected_revision != revision:
            raise ValueError('项目输入版本已变化，请重新读取后再编辑。')

        self._progress(progress, '正在校验当前项目输入…')
        self._verify_current_sources(root, project.project_id, manifest, progress=progress)
        problem_document_metadata = self._verify_problem_document(root, manifest)
        selected = self.imports.validate_sources(sources, template)
        data_count = sum(kind != 'docx' for _path, kind in selected)
        if not 1 <= data_count <= 32:
            raise ValueError('项目至少保留一个数据文件，最多 32 个。')
        sheets = self._preserved_xlsx_sheets(root, manifest, selected)

        next_revision = self._next_revision(root, revision)
        stage = Path(tempfile.mkdtemp(prefix='project-edit-', dir=self.paths.temp))
        committed_files = []
        committed = False
        try:
            create_layout(stage)
            self._progress(progress, '正在复制并分析新版本数据文件…')
            self.imports.populate(stage, project.project_id, problem, selected,
                                  sheets=sheets, revision=next_revision, progress=progress,
                                  problem_document_metadata=problem_document_metadata)
            self.paths.validate()
            validate_managed_path(stage)
            self._progress(progress, '正在提交新版本文件…')
            for directory in ('00_inputs', '10_analysis'):
                for source_path in sorted((stage / directory).iterdir()):
                    if directory == '00_inputs' and source_path.name.startswith('manifest-v'):
                        continue
                    target = validate_managed_path(root / directory / source_path.name)
                    if target.exists():
                        raise ValueError('新版本输入文件已存在，请重新读取项目后再编辑。')
                    source_path.rename(target)
                    committed_files.append(target)

            staged_manifest = stage / '00_inputs' / f'manifest-v{next_revision:04d}.json'
            target_manifest = validate_managed_path(root / '00_inputs' / staged_manifest.name)
            if target_manifest.exists():
                raise ValueError('新版本清单已存在，请重新读取项目后再编辑。')
            staged_manifest.rename(target_manifest)
            committed = True
            self._progress(progress, '新版本输入已保存。')
            return next_revision
        except BaseException:
            if not committed:
                for path in reversed(committed_files):
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
            raise
        finally:
            validate_managed_path(stage)
            if stage.exists():
                shutil.rmtree(stage)

    def _preserved_xlsx_sheets(self, root: Path, manifest: dict, selected) -> dict[str, str]:
        selected_by_path = {str(path).casefold(): (path, kind) for path, kind in selected}
        sheets = {}
        version = manifest.get('version')
        if type(version) is not int:
            raise ValueError('项目输入版本无效，无法恢复 XLSX 工作表选择。')
        analysis_version = '' if version == 1 else f'-v{version:04d}'
        source_name = re.compile(r'^00_inputs/source-([0-9]{4})-v[0-9]{4}\.xlsx$', re.I)
        for item in manifest['sources']:
            record = item.get('record') if isinstance(item, dict) else None
            if not isinstance(record, dict) or record.get('kind') != 'xlsx':
                continue
            relative = record.get('relative_path')
            match = source_name.fullmatch(relative) if isinstance(relative, str) else None
            if match is None:
                raise ValueError('项目 XLSX 输入路径无效，无法恢复工作表选择。')
            original = validate_managed_path(root / relative)
            if not original.is_relative_to(root):
                raise ValueError('项目 XLSX 输入超出项目目录。')
            selected_item = selected_by_path.get(str(original).casefold())
            if selected_item is None:
                continue
            selected_path, selected_kind = selected_item
            if selected_kind != 'xlsx':
                continue
            profile = validate_managed_path(
                root / '10_analysis' /
                f'source-{match.group(1)}{analysis_version}-profile.json')
            if not profile.is_relative_to(root) or not profile.is_file() or profile.stat().st_size > 1024 * 1024:
                raise ValueError('项目 XLSX 工作表摘要缺失或无效，无法保存修改。')
            value = json.loads(profile.read_text(encoding='utf-8'))
            sheet = value.get('sheet_name') if isinstance(value, dict) else None
            if not isinstance(sheet, str) or not sheet:
                raise ValueError('项目 XLSX 工作表选择缺失，无法保存修改。')
            sheets[selected_path.name] = sheet
        return sheets

    @staticmethod
    def _progress(callback, message):
        if callback is not None:
            if not callable(callback):
                raise TypeError('项目编辑进度回调无效。')
            callback(message)

    def _verify_current_sources(self, root: Path, project_id: str, manifest: dict, *, progress=None) -> None:
        for index, item in enumerate(manifest['sources'], start=1):
            if not isinstance(item, dict) or not isinstance(item.get('record'), dict):
                raise ValueError('项目当前输入记录无效，未保存修改。')
            source = SourceFile(**item['record'])
            if source.project_id != project_id:
                raise ValueError('项目当前输入不属于此项目，未保存修改。')
            self._progress(progress, f'正在校验现有输入 {index}/{len(manifest["sources"])}…')
            path = validate_managed_path(root / source.relative_path)
            self._progress(progress, f'正在读取现有输入 {index}/{len(manifest["sources"])}…')
            content = read_bounded(path, self.imports.limits)
            if len(content) != source.size_bytes or hashlib.sha256(content).hexdigest() != source.sha256:
                raise ValueError('项目现有输入发生变化，未保存修改。请重新读取项目。')

    def _verify_problem_document(self, root: Path, manifest: dict) -> dict | None:
        metadata = manifest.get('problem_document')
        if metadata is None:
            return None
        validated = self.imports._validate_problem_document_metadata(metadata)
        path = validate_managed_path(root / validated['relative_path'])
        if not path.is_relative_to(root):
            raise ValueError('题目原件超出项目目录，未保存修改。')
        content = read_bounded(path, self.imports.limits)
        if (len(content) != validated['size_bytes']
                or hashlib.sha256(content).hexdigest() != validated['sha256']):
            raise ValueError('题目原件已变化，未保存修改。请重新读取项目。')
        return validated

    @staticmethod
    def _next_revision(root: Path, current_revision: int) -> int:
        revisions = [current_revision]
        pattern = re.compile(r'-v([0-9]{4})(?:-|\.|$)')
        for directory in ('00_inputs', '10_analysis'):
            for path in (root / directory).iterdir():
                match = pattern.search(path.name)
                if match is not None:
                    revisions.append(int(match.group(1)))
        next_revision = max(revisions) + 1
        if next_revision > 9999:
            raise ValueError('项目输入版本号已达到上限。')
        return next_revision

    @staticmethod
    def _project(title: str) -> Project:
        project_id = uuid4().hex
        return Project(project_id, title, f'projects/{project_id}')
