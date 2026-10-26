# Agent 运行过程侧栏实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 在“运行与审核”页实时显示工作流阶段、Codex/DeepSeek 可观察事件及阶段产物摘要。

**Architecture:** 用不可变的 `RunProgressEvent` 和逐运行的 `RunProgressReporter` 将工作流及 Provider 事件变成安全的结构化数据。工作流负责阶段/校验/产物事件，Provider 负责 Agent 请求与可见消息；`RunPanel` 将它们经 Qt queued signal 交给会话内存时间线，并以可调分隔栏呈现。历史只保留在应用内存，不加入检查点或项目日志。

**Tech Stack:** Python 3.11+、PySide6、PySide6-Fluent-Widgets、现有 Codex App Server / `CodexTextService`、现有固定 HTTPS `DeepSeekApiClient`、pytest。

**Spec:** `docs/superpowers/specs/2026-10-18-agent-run-sidebar-design.md`

## Global Constraints

- 每条事件文本 UTF-8 大小不超过 64 KiB，单次运行最多保留 2,048 条。
- 事件详情仅保存在当前应用会话的内存中，不写入运行数据库、检查点、项目日志或生成产物。
- 事件不得包含 API 密钥、认证头、完整提示词或题目/数据全文；显示 Agent 可见消息前脱敏凭据样式文本。
- 只显示 Agent 对外可见消息；reasoning、内部思维链和隐藏分析不得作为事件显示。
- DeepSeek 当前是非流式 HTTPS 请求；显示发送/等待/收到响应状态，不显示逐 token 进度。
- 未经批准的工具事件显示为拦截/停止；侧栏不得改变 Agent 工具权限、费用授权、代码执行隔离或 Word 生成规则。
- 运行事件接收或渲染失败不能改变工作流结果；检查点和阶段产物提交顺序保持现状。
- 所有新增文件、临时文件、测试缓存和构建产物放在 F: 盘；本计划实施时将临时文件放在 `[本机路径已省略]`。
- 每条测试命令均从 F: worktree 根目录运行；先将 `TEMP`、`TMP`、`TMPDIR`、`PYTHONPYCACHEPREFIX`、`APPDATA`、`LOCALAPPDATA`、`XDG_CACHE_HOME` 和 `XDG_CONFIG_HOME` 指向上述 F: 临时目录，并设置 `QT_QPA_PLATFORM=offscreen`。
- 当前 worktree 有大量未提交且未跟踪的项目文件，包含本计划涉及的源码与测试；实现前逐个检查这些文件的现状，只做增量修改，绝不清理或还原。
- 因计划涉及文件已有未提交基线且部分代码尚未进入 Git，实施任务完成时不暂存、不提交这些文件；待用户审阅整体改动后再决定提交方式，避免把先前工作混入本功能提交。

## Review Focus

1. Agent 可见消息意外包含密钥或认证头：显示前脱敏，并测试 `test_reporter_redacts_secret_patterns`。
2. Provider 取消、超时或响应解析失败：不得显示完整/成功事件或留下部分结果，并测试 `test_deepseek_progress_drops_cancelled_or_invalid_responses`。
3. Codex reasoning、userMessage 或越权工具事件：不能泄漏到时间线；仅允许的 Agent 消息可见，并测试 `test_codex_progress_forwards_agent_message_only_and_blocks_tools`。
4. 错误运行标识、超限事件或接收回调异常：丢弃有问题的显示事件而不改变工作流状态，并测试 `test_timeline_rejects_wrong_run_event`、`test_reporter_isolates_sink_failure`。
5. 用户查看旧事件时收到新事件、或窄窗口下侧栏折叠：保持滚动位置且运行控制仍可操作，并测试 `test_timeline_preserves_scroll_when_not_at_bottom` 与 `test_sidebar_collapses_below_minimum_width_without_hiding_actions`。

---

### Task 1: 定义结构化运行事件与安全 Reporter

**Files:**
- Create: `src/mathmodel_paper_studio/workflow/progress.py`
- Test: `tests/unit/workflow/test_progress_events.py`

**Interfaces:**
- Produces: `ProgressKind`、不可变 `RunProgressEvent`、`RunProgressReporter(run_id, sink)`。
- `ProgressKind` 至少包含 `RUN_STARTED`、`RUN_PAUSED`、`RUN_CANCELLED`、`RUN_FAILED`、`RUN_COMPLETED`、`STAGE_STARTED`、`STAGE_PROGRESS`、`STAGE_COMPLETED`、`STAGE_PAUSED`、`STAGE_FAILED`、`STAGE_CANCELLED`、`AGENT_REQUEST_STARTED`、`AGENT_WAITING`、`AGENT_MESSAGE`、`AGENT_COMPLETED`、`AGENT_FAILED`、`AGENT_BLOCKED`、`VALIDATION_STARTED`、`VALIDATION_PASSED`、`VALIDATION_FAILED`、`ARTIFACT_CREATED`、`ARTIFACT_REVIEWED` 和 `EVENTS_OMITTED`。
- `RunProgressEvent` 包含 `run_id`、逐运行递增的 `sequence`、UTC 时间、`kind`、可选 `stage` / `attempt`、可选 Provider `item_id`、标题、详情及可选 `ArtifactDigest`。
- `RunProgressReporter.emit(kind, title, *, stage=None, attempt=None, item_id=None, detail="", artifact=None)` 创建事件、脱敏并调用 sink；sink 异常不得向工作流抛出。下一次成功投递时最多附带一条不含原始内容的 `EVENTS_OMITTED` 通用提示。

- [x] **Step 1: 写失败测试**：覆盖事件字段不可变/合法性、序号递增、item id 校验、`test_reporter_redacts_secret_patterns`、UTF-8 边界截断标记、`test_reporter_isolates_sink_failure` 和接收恢复后的通用丢失提示。
- [x] **Step 2: 运行测试确认失败**：在 F 盘项目根目录运行 `& .\.venv\Scripts\python.exe -m pytest tests/unit/workflow/test_progress_events.py -q`；预期因事件模块或目标接口尚不存在而失败。
- [x] **Step 3: 实现事件和 Reporter**：在 `workflow/progress.py` 添加事件类型、字段验证、凭据样式脱敏及 sink 异常隔离；事件详情超过 64 KiB 时在 UTF-8 边界截断并添加明确标记。
- [x] **Step 4: 运行测试确认通过**：使用 Step 2 的命令重跑同一测试文件；预期全部通过，原始秘密值不得出现在事件详情或异常文案。
- [x] **Step 5: 检查任务结果**：复核两个新文件和目标测试结果；保持文件未暂存、未提交，不改动其他未提交工作。

---

### Task 2: 从工作流实时发出阶段、校验和产物事件

**Files:**
- Modify: `src/mathmodel_paper_studio/workflow/engine.py`
- Modify: `src/mathmodel_paper_studio/workflow/live_engine.py`
- Test: `tests/integration/test_live_workflow.py`
- Test: `tests/unit/workflow/test_engine_progress.py`

**Interfaces:**
- Consumes: Task 1 的 `RunProgressReporter`。
- Produces: `WorkflowEngine.start(..., *, cancel_event=None, progress=None)`、`resume(..., *, cancel_event=None, progress=None)`、`cancel(run_id, *, progress=None)`；缺省 `progress` 保持现有调用兼容。
- `LiveWorkflowEngine._request(..., cancel_event=None, progress=None)` 向支持进度接口的 Provider 转发 Agent 事件；旧 Fake Provider 仍可通过原 `generate(context, prompt, cancel_event=None)` 路径工作。

- [x] **Step 1: 写失败测试**：覆盖阶段启动/完成/暂停/取消、校验完成、产物提交事件顺序；断言取消和产物事件只在对应状态检查点提交后发出，并验证 `test_observer_failure_does_not_change_checkpoint_or_stage_result`。
- [x] **Step 2: 运行测试确认失败**：运行 `& .\.venv\Scripts\python.exe -m pytest tests/unit/workflow/test_engine_progress.py tests/integration/test_live_workflow.py -q`；预期缺少 progress 参数或阶段事件断言失败。
- [x] **Step 3: 添加可选 Reporter 参数**：在 `start`、`resume`、`cancel`、`_drive` 传递 Reporter；在阶段执行前报告阶段/尝试，在状态提交后报告结果与 `ArtifactDigest` 元数据。
- [x] **Step 4: 接入请求事件映射**：在 `LiveWorkflowEngine._request` 映射 Agent 开始、等待、可见文本、完成、失败和拦截事件；未知 Provider 事件只生成通用诊断，不能改变 `StageResult`。
- [x] **Step 5: 运行测试并检查改动**：重跑 Step 2 的命令；复核工作流变更只增加观察回调，保持所有文件未暂存、未提交。

---

### Task 3: 为 Codex 与 DeepSeek Provider 增加安全进度回调

**Files:**
- Modify: `src/mathmodel_paper_studio/agents/codex_app_server.py`
- Modify: `src/mathmodel_paper_studio/agents/codex_text_service.py`
- Modify: `src/mathmodel_paper_studio/agents/providers/deepseek_api.py`
- Test: `tests/unit/agents/test_codex_app_server.py`
- Test: `tests/unit/agents/test_codex_text_service.py`
- Test: `tests/unit/agents/providers/test_deepseek_api.py`

**Interfaces:**
- Produces: Codex 与 DeepSeek 均保留现有 `generate(context, prompt, cancel_event=None)` 行为，并新增 `generate_with_progress(context, prompt, cancel_event=None, *, event_callback)`。
- `event_callback` 接收现有 `AgentEvent`；只允许 `STARTED`、`TEXT`、`COMPLETED`、`FAILED`、`CANCELLED`、`BLOCKED` 等既有协议事件。
- Codex App Server 的 `generate(..., *, on_agent_text=None)` observer 只转发经过 allowlist 校验的 `(item_id, text_delta)`；不转发 reasoning、userMessage 或原始通知。
- DeepSeek 发出请求开始、已发送/等待、收到且校验通过的完整回复；仍使用非流式请求，取消/超时/无效响应不发成功回复事件。

- [x] **Step 1: 写失败测试**：新增 `test_codex_progress_forwards_agent_message_only_and_blocks_tools`、Codex 服务事件转发测试，以及 `test_deepseek_progress_drops_cancelled_or_invalid_responses`。
- [x] **Step 2: 运行 Provider 测试确认失败**：运行 `& .\.venv\Scripts\python.exe -m pytest tests/unit/agents/test_codex_app_server.py tests/unit/agents/test_codex_text_service.py tests/unit/agents/providers/test_deepseek_api.py -q`；预期事件回调接口或过滤断言失败。
- [x] **Step 3: 实现 Codex 可见消息流**：在 App Server 等待 turn 的过程中保留现有缓冲与校验，同时对 `agentMessage` 更新计算文本增量并回调；禁止动作仍立即阻止，stderr 和推理内容不转发。
- [x] **Step 4: 实现 DeepSeek 状态事件**：在现有固定 HTTPS 请求中仅于请求已发送后发等待事件，在响应结构、取消状态和密钥回显检查都通过后发完整可见回复；不暴露头部、原始网络错误或部分响应。
- [x] **Step 5: 保持旧接口兼容并检查改动**：确认现有 `generate` 仍返回原结果且不要求回调；重跑 Step 2 命令，复核禁止工具和密钥边界，保持文件未暂存、未提交。

---

### Task 4: 实现运行过程侧栏并接入主窗口

**Files:**
- Create: `src/mathmodel_paper_studio/ui/run_timeline.py`
- Modify: `src/mathmodel_paper_studio/ui/run_panel.py`
- Modify: `src/mathmodel_paper_studio/ui/main_window.py`
- Test: `tests/unit/ui/test_run_timeline.py`
- Test: `tests/integration/ui/test_run_controls.py`
- Test: `tests/integration/ui/test_fluent_accessibility_responsive.py`
- Test: `tests/unit/ui/test_main_window_deepseek_consent.py`

**Interfaces:**
- `RunTimelineWidget.reset_for_run(run_id)` 清空新运行的会话历史；`append_event(RunProgressEvent)` 追加事件；`set_artifact_review_status(evidence_id, status, summary="")` 更新产物审核状态。
- `RunPanel.begin_progress_run(run_id) -> RunProgressReporter` 为新运行创建 Reporter，同一运行恢复时继续使用相同 Reporter 和时间线。
- RunPanel 用独立的 Qt object signal 接收结构化事件；既有字符串 worker 事件保持兼容。主窗口把同一个 Reporter 传给 start/resume 工作流。

- [x] **Step 1: 写失败测试**：覆盖 `test_run_panel_receives_live_progress_and_preserves_legacy_string_events`、消息详情/事件筛选、2,048 条保留上限、会话内存清理、审核状态更新、`test_timeline_rejects_wrong_run_event`、`test_timeline_preserves_scroll_when_not_at_bottom` 及 `test_sidebar_collapses_below_minimum_width_without_hiding_actions`。
- [x] **Step 2: 运行 Qt 测试确认失败**：运行 `& .\.venv\Scripts\python.exe -m pytest tests/unit/ui/test_run_timeline.py tests/integration/ui/test_run_controls.py tests/integration/ui/test_fluent_accessibility_responsive.py -q`；预期侧栏 widget 和实时状态断言失败。
- [x] **Step 3: 创建时间线 widget**：在 `ui/run_timeline.py` 实现带详情区、筛选、产物清单、审核状态和 2,048 条上限的纯内存视图；不持久化 Agent 事件。
- [x] **Step 4: 改造 RunPanel 布局**：用水平 `QSplitter` 保留现有阶段与控制区，在右侧接入时间线；面板宽度低于 900 px 时折叠侧栏并提供重新打开入口，运行控制留在固定底部。
- [x] **Step 5: 接入 start/resume/cancel 与审核结果**：在 `MainWindow._start_run`、恢复和取消操作中传递同一 Reporter；移除完成后重复批量追加的阶段字符串；将已有产物审核结论更新到侧栏，不改变 DeepSeek 授权逻辑。
- [x] **Step 6: 运行测试并检查改动**：运行 Step 2 命令及 `& .\.venv\Scripts\python.exe -m pytest tests/unit/ui/test_main_window_deepseek_consent.py -q`；确认原取消/继续和费用授权行为不变，复核侧栏只存内存数据，保持文件未暂存、未提交。

---

### Task 5: 完成跨层模拟工作流验收

**Files:**
- Create: `tests/integration/ui/test_run_progress_flow.py`
- Modify: `tests/integration/test_live_workflow.py`
- Modify: `tests/integration/test_workflow_resume.py`

**Interfaces:**
- Consumes: Task 1–4 的 Reporter、Provider 回调、阶段事件和侧栏公开接口。
- Produces: 用 Fake Provider / Fake computation runner 驱动完整工作流并检验时间线，无真实 API 请求、不运行生成代码、不改变生产项目数据。

- [x] **Step 1: 写端到端失败测试**：新增 `test_fake_live_run_renders_stage_agent_validation_and_artifact_events_in_order`、`test_resume_appends_to_same_timeline_and_new_run_clears_it`、`test_progress_events_are_not_serialized_to_checkpoint`。
- [x] **Step 2: 运行跨层测试确认失败**：运行 `& .\.venv\Scripts\python.exe -m pytest tests/integration/ui/test_run_progress_flow.py tests/integration/test_live_workflow.py tests/integration/test_workflow_resume.py -q`；预期因工作流到 UI 的事件接线或排序断言失败。
- [x] **Step 3: 完成端到端测试夹具**：使用 fake Provider、fake computation runner、真实 `LiveWorkflowEngine` 与 `RunPanel` 连接；测试中不发送真实 API 请求、不执行生成代码、不改生产项目数据。
- [x] **Step 4: 运行跨层与回归测试**：重跑 Step 2 命令，并运行 `& .\.venv\Scripts\python.exe -m pytest tests/unit/workflow tests/unit/agents tests/unit/ui tests/integration/ui -q`；预期全部通过。
- [x] **Step 5: 最终全量验证并复核**：在 F: 临时目录环境中运行 `& .\.venv\Scripts\python.exe -m pytest tests -q --cov=mathmodel_paper_studio --cov-report=term-missing`；目标为全套测试通过且覆盖率不低于项目配置的 80%。若 Qt 子进程有原生退出码，单独记录进程结果与 Python 断言结果；复核测试与工作区差异后，保留实现改动为未暂存、未提交状态供用户审阅。

## 实施边界

当前 Codex 或 DeepSeek 的真实连接、计费、策略隔离及执行器验收由各自既有门禁决定。模拟工作流通过只证明事件管线和界面行为；不能据此宣称真实 API 或打包 EXE 已通过验收。
