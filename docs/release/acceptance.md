# Windows 预览包验收记录

日期：2026-10-16（Asia/Shanghai）；应用版本：0.1.0。

当前可交付的是可启动的 Windows 桌面预览包。真实 Codex/Claude 自动生成、受约束的生成代码执行和 Word 页面渲染尚未通过验收，因此正式 MVP 发布门仍未关闭。

## 启动和使用

打开 [MathModelPaperStudio.exe](<F:/Codex另一个工作的地方/downloads/mathmodel-paper-studio-release-20261016-v4/MathModelPaperStudio/MathModelPaperStudio.exe>)。这是 onedir 目录包，请保留 EXE 同目录的 _internal 文件夹。程序与数据目录均需位于批准的 F: 工作根目录下。

默认数据目录：F:\Codex另一个工作的地方\mathmodel-paper-studio-data。也可指定新的 F: 数据目录：

```powershell
& 'F:\Codex另一个工作的地方\downloads\mathmodel-paper-studio-release-20261016-v4\MathModelPaperStudio\MathModelPaperStudio.exe' --root 'F:\Codex另一个工作的地方\my-mathmodel-projects'
```

新建项目后粘贴题目，选择 F: 工作目录内的 CSV/XLSX 和 DOCX 模板。项目页显示导入副本、哈希和数据摘要；Word 模板页支持检查和映射。运行前会显示服务商、输入范围、可写目录和处理说明。当前两个真实 Agent 均缺少本次可信预检，因此开始按钮会被阻止。

源码桌面入口是 scripts/build_windows.ps1 -LaunchSource。原来的 python -m mathmodel_paper_studio 控制台入口仍是早期存储检查器。

## 环境与可复现构建

- 实测 Python 3.13.14 / Windows amd64；PySide6 6.8.2.1；PyInstaller 6.22.3；hooks 2026.8。
- pytest 9.0.2；coverage.py 7.16.2；pytest-cov 7.0.0。
- requirements.lock 固定 25 个运行、测试和构建依赖及本机 wheel 的 SHA-256。当前环境逐项版本检查和 pip check 通过。Python 3.11 未运行验证；此锁定中的 lxml wheel 为 CPython 3.13。
- 下载目录：F:\Codex另一个工作的地方\downloads\mathmodel-paper-studio-task10-tools-20261016。已有运行依赖 wheel 来自同一 downloads 根下的 mathmodel-paper-studio-task9-pyside6-20261016、mathmodel-paper-studio-task2-20261015 和 mathmodel-paper-studio-task8-python-docx-20261016。
- 最终构建目录：F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-20261015\task10\build-v4。
- 最终输出目录：F:\Codex另一个工作的地方\downloads\mathmodel-paper-studio-release-20261016-v4。
- 包内有 290 个文件，共 127998133 字节；EXE SHA-256：b7a13b1dbae9371962fedccececc25b214cec614e12ed0d5b8972235b9b0f6f0。
- 包含 25 个依赖的 58 份许可证/通知文件及 CPython LICENSE；详见 _internal/licenses/THIRD_PARTY_NOTICES.json。

重新构建时请指定新的 F: 构建和输出目录，脚本会拒绝覆盖已有包：

```powershell
& 'F:\Codex另一个工作的地方\worktrees\mathmodel-paper-studio-impl-20261015\scripts\build_windows.ps1' -BuildRoot 'F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-20261015\task10\build-next' -OutputRoot 'F:\Codex另一个工作的地方\downloads\mathmodel-paper-studio-release-next'
```

构建使用 PyInstaller 的 [spec/onedir 机制](<https://pyinstaller.org/en/stable/spec-files.html>)，在自定义运行时 hook 中先验证 F: 路径并配置缓存，再加载 Qt；onedir 运行时文件保留在 F: 包目录内。所有明确管理的构建、输出、数据、日志和缓存路径均经过校验。

## 验收结果

证据根目录：F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-20261015\task10。

| 验收项 | 结果 | 证据及边界 |
|---|---|---|
| 九阶段假 Provider E2E | PASS，最终 9/9 | e2e-final-coverage-v2.log；真实导入、检查点、结果结构校验、DOCX 写入/QA；计算值来自固定夹具，没有执行生成代码 |
| 项目回归 | PASS，32 模块、263 项采集，262 通过、1 跳过 | verified-suite-summary.json；跳过项为既有项目服务符号链接权限测试 |
| 行覆盖率 | PASS，整个应用包 83.62% | coverage.json / coverage-report.txt；6068 条语句，994 条未覆盖；非 UI 核心合计 87.88%，核心单文件最低 80.17% |
| UI 单文件覆盖率 | PARTIAL | 主窗口 60.21%、模板映射页 34.72%；不宣称所有 UI 文件达到 80% |
| 最终 EXE F: 启动检查 | PASS | frozen-check-results-v4.json：合法目录退出 0，C: 数据根目录退出 1；未物理拔除 F: 测试 |
| 最终 EXE 可见创建/导入 | PASS | packaged-visible-v4/05-imported-project.png；题目、CSV、XLSX、DOCX 四项输入，两份数据摘要 |
| 最终 EXE 预检与取消 | PASS | 06-provider-blocked-review.png；开始被阻止，取消后 SQLite 运行记录为 0 |
| 最终 EXE 关闭与重启 | PASS | interaction.json、08-project-restored-after-restart.png；项目恢复，两次正常退出 0 |
| 最终 EXE 真实运行/运行中取消 | BLOCKED | 真实 Provider 可信预检未通过，未启动真实 Agent；源码假夹具取消/恢复测试另有通过证据 |
| Codex CLI smoke | BLOCKED | 未取得本次版本和范围预检；Task 0 旧快照为 Codex 0.159.3 不可用，不能当作新授权 |
| Claude Code CLI smoke | BLOCKED | Task 0 旧快照为 2.1.291、仅 restricted_read_write；没有本次完整能力/范围证据，未调用真实服务 |
| 任意生成 Python 执行 | BLOCKED | 没有满足文件、网络、凭据和进程边界的可信执行环境；未降低安全门 |
| Word 页面渲染审查 | NOT PERFORMED | DOCX 结构检查/重开/打开 Word 不替代页面渲染；本轮未导出或检查 Word 渲染页 |
| 包清单、哈希、许可证、凭据模式扫描 | PASS | package-files.json、archive-members.json、final-path-hash-audit-v4.json；可见运行后包字节未变，原始夹具和导入副本哈希一致 |
| 应用管理路径审计 | PASS | 声明的包、数据、构建、日志、缓存目录均在批准的 F: 根内；不是对操作系统后台写入的全盘审计 |

覆盖率按模块隔离采集。一次 sys.monitoring + pytest 的 Qt 运行控制模块在收尾时退出 0xC0000409，该失败记录保留在 full-suite-coverage.log。最终 Qt 模块使用 unittest，等待线程池结束并在保存覆盖率后显式退出测试进程；其余模块使用 pytest。通过结果及最终 E2E 重跑合并后形成上述覆盖率，不宣称整套测试在一个进程中干净退出，也不宣称分支覆盖率。Qt 原生回调中未被标准跟踪器观察的行按未覆盖计算。

## 审查与交付状态

任务限定不使用子代理，本轮进行了本地审查。修复并保存了许可证缓存误收、PEM 格式头误报、构建输出目录联接未提前拦截、LICENCE 通知漏收四项红绿回归证据。生产设置没有测试 Provider 注入入口。未读取或复制任何原生 CLI 登录存储。

.gitignore 已把 projects/ 改为 /projects/，源码中的 projects 模块和相应测试不再被该规则隐藏。工作树保留既有未跟踪文件；未暂存、提交、推送或发布。Task 10 简报的明确“不提交”执行约束优先于其末尾复制的提交步骤。

正式发布仍需：真实 Codex/Claude 的本次能力和范围预检、可信生成代码执行环境、Word 页面渲染审查，以及真实运行条件下的 EXE 运行/取消验收。当前包只能称为桌面预览包。

## 2026-10-17 收尾跟进

本节补充供应商变更和新验收证据；上方 2026-10-16 的记录保留为当时快照。

### 供应商变更与 DeepSeek 边界

- 用户要求停用 Claude Code，改用 DeepSeek API。活动供应商选项为 Codex CLI 与 DeepSeek API；未继续 Claude 登录或调用。
- DeepSeek 客户端使用固定地址 `https://api.deepseek.com/chat/completions` 和模型 `deepseek-flash`。DeepSeek 官方文档当前列有该模型与 Chat Completions 路径，并说明按输入/输出 token 用量计费；价格会变动，运行前应以[官方模型和价格页](https://api-docs.deepseek.com/quick_start/pricing/)核对。客户端实现与[官方 Chat Completions 文档](https://api-docs.deepseek.com/api/create-chat-completion/)相符。
- API Key 仅从 `DEEPSEEK_API_KEY` 环境变量读取；本次检查只记录 Key 是否存在，进程中未发现 Key，也没有真实 API 请求或费用产生。
- 运行审核页说明文本外发类别、文件不作为附件、无工具/文件访问/代码执行，并要求每次运行单独同意按量计费。授权只保存在当前进程内；暂停运行保留同一运行授权，完成/失败/取消/关闭窗口会撤销，暂停期间不能被新运行覆盖。
- 本地假传输测试覆盖 DNS 等待、TLS 握手、阻塞发送撤销、响应取消和费用授权生命周期。它们证明代码路径，不证明 Windows 实际网络栈或 DeepSeek 服务端行为。
- **DeepSeek 尚不能从桌面 UI 发起完整工作流。** 默认 `MainWindow` 仍装配基础 `WorkflowEngine`，未连接 DeepSeek 客户端；`LiveWorkflowEngine` 还要求可信 Provider 状态和受控计算服务。状态预检不会连接远端，运行审核保持关闭。客户端与 UI 选择项已经进入源码，但不能把它们描述为已上线可用接入。
- 2026-10-16 的 v4 EXE 未包含本次源代码更新，也没有重打包。

### 本轮新增验收证据

证据根目录：`F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-20261016-closure`。

| 验收项 | 结果 | 证据及边界 |
|---|---|---|
| Codex 本机登录状态 | PASS（仅登录布尔值） | `cli\auth-status-summary.json`：F: 原生认证目录下状态命令退出 0、`logged_in=true`；没有启动模型请求，零工具、精确范围、取消和生产授权仍未验证 |
| Claude Code | RETIRED | 按用户要求不再作为活动选项，不再执行登录/状态/请求 |
| DeepSeek 客户端与费用授权 | CONTRACT TESTS ONLY | `tests\unit\agents\providers\test_deepseek_api.py`：假传输，无 Key、无真实网络；本机 Key/远端/额度未验证，默认桌面工作流未装配客户端 |
| 生成代码执行 | BLOCKED | `sandbox\profile-run-ec8febc00b1143c2bee42f54545c6266\evidence.json`：AppContainer/LPAC 边界和清理记录存在，但 Python smoke load 返回 `0xC0000022 STATUS_ACCESS_DENIED`；没有运行脚本或行为 canary，代码执行门保持关闭 |
| Word 页面渲染 | PASS（合成夹具） | `word\acceptance.json` 和 `word\fixture-v3\visual-review.json`：Word 2024 输出 PDF 共 6 页，PyMuPDF 1.28.2 逐页查看通过；合成模板源与输出 DOCX 哈希已记录 |
| 用户最终模板渲染 | NOT VERIFIED | 六页证据是固定科学示例，不代表用户真实模板或模型内容；Word 用户配置目录的后台写入没有审计，后续未再启动 Word |
| DeepSeek 更新版 EXE | NOT BUILT | 现有 `mathmodel-paper-studio-release-20261016-v4` 是旧预览包；未用新源码重建或运行验收 |

本次分项源码回归：DeepSeek API 假传输 **20 项 + 9 个子用例**；MainWindow 授权生命周期 **5 项**；DeepSeek 选择器 **3 项**；运行审核 UI **5 项 + 2 个子用例**；Provider 预检 **8 项 + 22 个子用例**；LiveWorkflowEngine 契约 **25 项 + 5 个子用例**；数据库迁移 **16 项 + 14 个子用例**；Qt 运行控制 **7 项**。日志均在证据根目录的 `tests\` 下：`deepseek-api-final.log`、`deepseek-ui-final.log`、`main-window-consent-final.log`、`deepseek-selection-final.log`、`run-review-final.log`、`provider-preflight-final.log`、`live-workflow-final.log`、`database-final.log`、`run-controls-final.log`、`run-controls-unittest-final.log`。除 `deepseek-ui-final.log` 和 `run-controls-final.log` 中记录的 pytest 进程级失败外，分项命令退出码为 0。

两个 pytest Qt 进程分别在全部可见断言通过后以 Windows 原生状态 `0xC0000374` 与 `0xC0000409` 退出；各 Qt 模块随后分开运行，运行控制模块通过项目原有 unittest 入口重跑，摘要完整且退出码为 0。失败的 pytest 进程不报告为通过，也不声称真实可见桌面会话已验收。这些假传输/契约测试仍不能替代真实 API、受控代码运行、用户最终模板页面渲染或更新版 EXE 验收。当前状态仍是预览源码与旧版预览包，不是正式 MVP 发布。

## 2026-10-17 DeepSeek API 桌面工作流接线

- 默认 `MainWindow` 现在装配 `LiveWorkflowEngine`、`DeepSeekApiClient` 与 fail-closed `PythonRunner`。DeepSeek 运行前会检查所选输入及计划输出是否仍在 F: 项目目录；模型无文件附件、工具、本机文件访问或代码执行权限。
- 环境变量预检只确认本机 `DEEPSEEK_API_KEY` 是否存在，不冒充远端验证。运行审核会明确说明远端连接和账户额度在用户同意后的首次请求中确认，并保留逐运行费用授权。
- 真实生成 Python 的执行门仍未通过验收。API 阶段可以产生分析、模型方案及代码草稿；运行将在计算阶段安全暂停，暂不生成最终 Word。未降低该安全门。
- 新版包：`F:\Codex另一个工作的地方\downloads\mathmodel-paper-studio-deepseek-api-20261017\MathModelPaperStudio\MathModelPaperStudio.exe`。
- 构建使用 Python 3.13.14 / PyInstaller 6.22.3；依赖锁、包清单、EXE 内部成员与敏感模式审计均通过。EXE SHA-256：`FB60759274BFF61BEB1E207E275F2895A7DE01AE6EE8F161076761C997FBA7BE`。
- 已更新桌面快捷方式至新版；旧快捷方式备份于 `F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-deepseek-api-20261017\DesktopShortcut-before-update.lnk`。本轮未运行测试套件，也未从桌面工作流发起付费 API 请求。
## 2026-10-17 DeepSeek 工作流接口修复和 v2 包

- 修复 `LiveWorkflowEngine` 与 `PythonRunner.run()` 的参数契约：运行器接收并校验 `input_files` 和 `attempt`，确保输入仍位于当前 F: 项目目录；它只保存 `generated.py` 和明确标记 `execution_started=false` 的 `execution-v0001.json`，随后安全暂停，不执行生成代码。
- DeepSeek 预检失败提示现在针对 API 密钥和服务状态，不再误称缺少 CLI 状态。
- 新版 EXE：`F:\Codex另一个工作的地方\downloads\mathmodel-paper-studio-deepseek-api-20261017-v2\MathModelPaperStudio\MathModelPaperStudio.exe`。SHA-256：`E5357D5C1A34F6030C48C071C4C562FEF2952157B15D6A7DF7EFEB314664688E`。构建命令成功结束；依赖锁及包文件清单、归档成员审计返回 `ok`。
- 桌面快捷方式现指向 v2；原快捷方式备份：`F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-deepseek-api-20261017-v2\DesktopShortcut-before-update-v2.lnk`。
- 本轮对 5 个修改的 Python 文件做了 AST 语法解析（通过）；未运行测试套件，也未从应用发起真实计费 API 请求。检查时旧 v1 仍有可见窗口，因此未强制关闭或并行启动 v2；关闭旧窗口后从桌面快捷方式启动即可进入 v2。新版可视桌面验收、生成代码执行和最终 Word 生成仍未验收。
## 2026-10-17 Codex / DeepSeek 双 Provider 收尾复核

### 接入状态

- 默认 `MainWindow` 已装配 `LiveWorkflowEngine` 和按运行所选 Provider 路由的 `ProviderClientRegistry`：Codex 使用 `CodexCliAdapter`，DeepSeek 使用 `DeepSeekApiClient`；无跨 Provider 回退。
- 假 Provider 工作流回归 **25/25**；Provider 路由与包审计单测复跑 **10 passed、6 subtests passed**，日志：`F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-gates-20261017\final-audit\provider-and-package-tests.log`。MainWindow DeepSeek 客户端装配测试此前 **6/6** 通过。上述均未访问真实模型服务。
- Codex 本机 CLI 版本为 `0.159.3`，本机登录状态检查成功；能力预检仍为 `available=false / capabilities_unverified`。CLI 原生 exec 未提供可核验的完整工具清单，静态模型目录亦未固定，所以自动运行仍关闭；没有发起 Codex 模型请求。
- DeepSeek 客户端已进入默认工作流，读取 `DEEPSEEK_API_KEY`。本次进程、用户级和系统级环境均未检测到该变量；未发送 API 请求，因此远端连通性和账户额度未验证。设置该变量后需完全退出并重新启动应用，Key 不要发到聊天。

### 仍未通过的运行门禁

- **受控 Python 执行：BLOCKED。** 先前获准的一次性 AppContainer 试验中，Python smoke load 返回 `0xC0000022 STATUS_ACCESS_DENIED`；配置已清理，未运行生成代码或行为 canary。为遵守 F 盘限定，本轮未再次创建写入 C: Packages/注册表的配置；重试该方案需要新的明确批准。生成代码仍会保存为草稿并安全暂停。
- **用户最终 Word 模板渲染：NOT VERIFIED。** Word 2024 的 6 页合成夹具已逐页检查通过，但 F: 应用数据目录未发现用户最终模板，故不能据合成夹具宣称最终模板验收完成。
- **打包启动检查退出码：ANOMALY。** v4 的 `--startup-check` 报告为 `status=ok`、`frozen=true`、Python `3.13.14`，管理路径均位于 F:；同次进程退出码为 `1`，原因尚未定位。另已观察到 v4 主窗口可见启动，标题为“数模论文工作台”，并关闭本轮新启动的窗口。启动检查不报告为完全通过。
- 先前两个 pytest Qt 进程在断言通过后分别以 `0xC0000374`、`0xC0000409` 退出的记录仍保留；本次 10 项复跑是非 Qt 路由及包审计测试，不能覆盖该历史进程级问题。

### v4 双 Provider 预览包

- EXE：`F:\Codex另一个工作的地方\downloads\mathmodel-paper-studio-dual-provider-20261017-v4\MathModelPaperStudio\MathModelPaperStudio.exe`
- SHA-256：`69B64C55DB67C804259FEF4AFF36CDC4C3FA7A706EB4A3F064E4D870F63D3A3D`
- 包内 **317** 个文件，共 **131183857** 字节；归档有 **726** 个成员，包含 Codex Provider 模块。依赖锁、包清单和项目归档审计均为 `ok`。包审计清单位于同目录的 `package-files.json` 与 `archive-members.json`。
- 启动检查报告：`F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-gates-20261017\packaged-v4-startup.json`。本包仍是桌面预览包；未验证真实 API 工作流、受控 Python 执行或用户最终模板。

最终状态：**Codex 与 DeepSeek 已接入源码和 v4 预览 UI 的 Provider 路由，但两者均未完成真实模型请求验收；完整自动工作流仍被受控代码执行门禁阻止。**
## 2026-10-17 AppContainer 执行门禁重试

- 用户明确允许本次临时创建并清理配置。新验收脚本、runtime 副本、canary、临时目录和日志均放在 `F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-gates-20261017\lpac-retry-20261017`。
- `CreateAppContainerProfile` 成功；启动前的子进程 token 核验通过：AppContainer、LPAC、零 capability、预期 SID、低完整性 RID 4096、目标 Job 成员均符合预期。
- Python smoke load 再次以 `3221225506 / 0xC0000022 STATUS_ACCESS_DENIED` 退出，stdout 为空；脚本因此停止，没有运行边界、子进程、内存、超时、CPU、输出限制等 canary。
- 同一份复制到 F: 的 CPython runtime 在普通当前 token 下运行固定 `LPAC_SMOKE_OK` 成功，退出码 0；runtime 文件 ACL 中记录了本次 package SID 的只读/执行权限。由此推断失败集中在 LPAC 的启动或资源访问条件，尚未定位具体对象，不能将其记作 sandbox 通过。Microsoft 文档也说明 LPAC 访问部分普通 AppContainer 可用资源需要显式 capability；这提供了诊断方向，不足以单独确认本机失败根因：[Microsoft Learn: Launch an AppContainer](https://learn.microsoft.com/en-us/windows/win32/secauthz/implementing-an-appcontainer)。
- `DeleteAppContainerProfile` 返回 0。随后独立只读检查确认本次 Packages 目录及 `Mappings/<SID>`、`Storage/<SID>`、`Storage/<profile-name>` 三项均不存在；旧 profile 未触碰。
- 完整证据：`F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-gates-20261017\lpac-retry-20261017\profile-run-c5267b86594e447e86a3d08ce55c0042\evidence.json`；运行摘要日志：`F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-gates-20261017\lpac-retry-20261017\runner.log`。
- **结论仍为 BLOCKED。** Python 未开始执行，未运行模型生成代码，代码执行门保持 fail-closed。此次授权范围已完成并清理；不据此扩大到其他 AppContainer capability、系统 ACL 或长期配置。
## 2026-10-17 Codex 设置页接入状态说明

- 根因：设置页只显示自动运行预检状态，没有单独说明 Provider 工作流路由与实际 CLI 调用状态，导致 Codex 看起来像未接入。
- 设置页现在分别显示接入状态和自动运行状态。Codex 文案明确：工作流路由已配置，但当前适配器不会启动本机 CLI；安装、登录和真实模型请求未验证。完整能力及隔离预检未完成时，自动运行保持关闭。
- Codex 仍未通过真实 CLI/模型请求验收；本次只调整状态说明，没有发起 Codex 或 DeepSeek 请求，也没有放开运行门禁。
- 设置页回归：`tests/unit/ui/test_deepseek_selection.py`，4 passed；日志：`F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-codex-status-20261017\settings-ui-tests.log`。
- 新预览包构建、文件清单和 EXE 归档审计通过；冻结启动检查 `status=ok`、`frozen=true`，管理路径均在 F:。启动报告：`F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-codex-status-20261017\startup-check.json`。
- EXE：`F:\Codex另一个工作的地方\downloads\mathmodel-paper-studio-codex-status-20261017\MathModelPaperStudio\MathModelPaperStudio.exe`；SHA-256：`473B05AEC663DC1E2AD1ACC6AFD76C5AEFDCE5EADD99C1FEF5F312E3BCF76A9A`。
## 2026-10-17 Codex 本机 CLI 登录状态与设置页 v2

- 此轮截图仍显示上一版 codex-status-20261017 的静态提示，不包含启动时本机 CLI 登录探测结果。设置页当前接入状态改为由实际探测更新；Agent 下拉框的 Provider 值传递也已修复。
- 真实本机探测发现 Codex CLI 0.159.3，应用独立 CLI 环境登录检查通过。探测未发起模型请求。
- 设置页会明确显示：CLI 已安装、应用独立环境登录检查通过；模型请求未发起，应用适配器尚未调用 Codex CLI。能力预检仍为 available=false / capabilities_unverified，自动运行继续关闭。登录检查通过不代表 Codex 模型工作流已接通。
- MainWindow 集成 smoke 显示上述状态，工作流门仍关闭；model_request_sent=false。设置页和选择逻辑回归 16 passed，有 24 条第三方 Qt 弃用警告，退出码 0。日志与 smoke 报告见 F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-codex-local-login-20261017\。
- 重建预览 EXE：F:\Codex另一个工作的地方\downloads\mathmodel-paper-studio-codex-local-login-20261017-v2\MathModelPaperStudio\MathModelPaperStudio.exe；SHA-256：DC9475DBD1EC6E1D1A8A80F21C7F0B8AA7C29B4F3F5AC0E50F922AF0742666BD。依赖与包文件清单、EXE 归档成员审计通过；冻结启动报告 status=ok、frozen=true，受管路径均在 F:。
- 本轮没有调用 Codex 或 DeepSeek 模型 API，也没有放开受控代码执行门禁。完整 Codex 模型工作流仍未完成验收。

## 2026-10-17 Codex CLI 隔离环境真实模型 smoke

- 固定 CLI 0.159.3 的 SHA-256 与应用 pin 一致，F: 应用隔离登录目录的 codex login status 退出码为 0；临时目录和工作目录均为 F:\Codex另一个工作的地方\tmp\codex-cli-connection-verify-20261017。
- 使用一条不含项目资料的提示，在空白 F: 工作目录中执行一次真实 Codex 模型请求；参数启用 read-only 沙箱、--ephemeral、忽略用户配置和规则，并禁用 shell_tool、view_image。退出码 0，收到完整 turn.completed，回复与预期短语一致；用量为输入 13,421、输出 8 tokens。
- 原始 JSONL 和 stderr：F:\Codex另一个工作的地方\tmp\codex-cli-connection-verify-20261017\codex-smoke.jsonl、F:\Codex另一个工作的地方\tmp\codex-cli-connection-verify-20261017\codex-smoke.stderr.log。
- 这是对应用隔离 CLI 凭据和远端模型服务的验证，不是应用适配器调用验证。当前 CodexCliAdapter.stream() 仍 fail-closed，不启动 CLI；自动工作流仍未接通，能力和隔离门禁仍关闭。

## 2026-10-17 Codex / DeepSeek 实际连接与 v5 包复验

本节更新前文较早时点的“尚未发真实请求”“v4/旧包”和连接状态记录；旧记录保留其当时结果。当前预览包仍未通过 Python 执行和用户最终模板渲染门禁。

### Codex

- **更正：** 上一版把 `codex-ui-canary.json` 记作桌面接入通过，这是错误结论。该 smoke 给 `MainWindow` 单独注入了另一个 `LiveProviderService`，并把固定文本回复错误提升为 `available=true` 和隔离证据；这既不验证发布启动器的默认接线，也不证明项目文件边界。该报告保留为历史记录，但不再作为接入或运行通过证据。
- 另一次真实 CLI smoke 的 JSONL 结构显示，Codex 在完整回合中返回过固定文本 canary；它不能替代桌面按钮路径验收。详见本节后面的 2026-10-17 复核记录。

### DeepSeek API

- 进程级变量未继承到本次 Codex 命令环境；只读检查确认用户级 `DEEPSEEK_API_KEY` 已配置，未显示或记录密钥值。将用户级值仅临时传给 F: 隔离 Python 子进程后，通过应用的 `DeepSeekApiClient` 发出一条固定请求，返回内容与 canary 完全匹配。请求范围是 51 字节固定提示、0 个输入文件、0 个上游文件、无工具；无题目、数据或模板内容。密钥没有写入报告或项目文件。证据：`F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-gates-20261017\deepseek-live-verify-20261017-userenv\deepseek-canary.json`。
- 这证明当时的 Key 和远端 API 可用，不代表后续账户额度不变。客户端只读进程环境；若桌面应用进程启动时没有继承用户级变量，完全退出并重新打开应用后再使用 DeepSeek。

### 回归测试

- Codex profile/canary、DeepSeek API 与费用授权、设置页选择逻辑、MainWindow 路由与显式验证按钮共 **71 tests passed**，命令退出码 0。日志：`F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-gates-20261017\scoped-tests-20261017-v5\provider-and-ui-tests.log`。
- 日志含 QDomDocument 第三方弃用警告和进程退出时的 PySide GC ResourceWarning；本次 unittest 进程以 0 退出。这不覆盖历史两个 pytest Qt 进程的 Windows 原生异常，也不等于完整测试套件或 80% 覆盖率验收。

### v5 预览包

- EXE：`F:\Codex另一个工作的地方\downloads\mathmodel-paper-studio-codex-deepseek-20261017-v5\MathModelPaperStudio\MathModelPaperStudio.exe`
- SHA-256：`1B512A1D3A4D7279968BC33E58F5A60EE20E1641A99BD2F412C7197ED1A8E73F`。PyInstaller 6.22.3 / Python 3.13.14；包清单 317 个文件、131272588 字节，归档审计 731 个成员；依赖锁、路径/敏感项清单和归档成员审计均为 `status=ok`，报告在 EXE 同目录的 `package-files.json`、`archive-members.json`。
- 冻结启动检查退出码 0，`status=ok`、`frozen=true`，Python 3.13.14；使用的数据库根目录在 F: 临时证据目录。报告：`F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-gates-20261017\startup-v5-retry\frozen-startup.json`。
- 实际启动 v5 GUI 后窗口标题为“数模论文工作台”，随后只关闭本次测试实例；隔离数据根目录和报告：`F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-gates-20261017\gui-smoke-v5\gui-startup.json`。

### 仍然阻止完整工作流的门禁

- **受控 Python 执行：BLOCKED。** LPAC Python smoke load 仍为 `0xC0000022 STATUS_ACCESS_DENIED`；未运行生成代码或行为 canary，安全门保持关闭。
- **用户最终 Word 模板：NOT VERIFIED。** 已验收的 6 页 Word 2024 渲染是合成夹具，不代表用户模板；本轮没有生成最终论文。
- **当前结论：** Codex 文本连接与 DeepSeek API 固定请求均已通过，v5 预览包构建及 GUI 启动通过；完整自动计算和最终 Word 仍未完成，不能作为正式 MVP 发布。

### 本轮命令环境备注

- 一条早期 PowerShell CLI 功能查询把 `$home` 当作普通变量赋值，PowerShell 因 `$HOME` 保留名称拒绝该赋值；同一命令序列随后仍调用了 `codex features list`，因此可能使用了默认 `CODEX_HOME`。该命令没有发送模型请求或用户提示；本轮无法确认 CLI 是否访问过默认 profile，也没有检查 C:。此后的真实验证使用 F: 固定 CLI、F: 隔离认证目录和 F: 临时路径。

## 2026-10-17 Codex 接入复核（v6 源码）

- 固定 Codex 0.159.3 的 `features list` 在命令传入 `--disable` 后仍列出 `unified_exec` 和 `unified_exec_zsh_fork` 等 enabled 项（152 条目，退出码 0）。该目录不能证明本次运行的有效工具集合，已从运行安全证据中移除。输出：`F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-gates-20261017\codex-feature-check-20261017\features-list.txt`。
- 修正 v5 启动接线：启动器不再注入独立的 `NativeLiveProbe`，由 `MainWindow(paths)` 创建的同一 `LiveProviderService` 供设置页和工作流共用。运行连接按钮现在发送固定 canary；不会发送题目、数据或模板。
- canary 接受完整成功回合中的固定文本回复，允许协议中的 reasoning 和非致命 error 事件，遇到命令/文件/MCP 等执行事件、失败或不完整回合则拒绝。它仅设置 `connection_verified=true`，不设置 `available` 或 `evidence_verified`，不生成 `LiveEvidence`。
- 默认 `MainWindow` 按钮的真实固定提示复验通过：`connection_verified=true`、`available=false`、`evidence_verified=false`；状态明确写明“仅验证文本连通”且自动运行关闭；`source_workflow_shared_service=true`，`project_data_sent=false`。报告：`F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-gates-20261017\codex-source-ui-live-retry2-20261017\default-button-live-result.json`。前两次固定 canary 因检查器未接受 reasoning/非致命错误事件而没有记为通过；修正后再验证成功。
- 移除运行时伪造全通过的 `ProbeResult`。`LiveProviderService` 默认没有独立精确范围、范围外访问拒绝和输入只读探测实现，因此即使连接 canary 成功也会在启动 CLI 前阻止生成。
- Codex、DeepSeek 客户端/路由、费用授权、实时运行器、设置页及桌面启动接线的针对性 pytest：**79 passed，0 failed**，退出码 0；日志：`F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-gates-20261017\scoped-tests-v6\codex-deepseek-ui-tests.log`。有 27 条 Qt 第三方弃用警告。
- 此源码复核尚未替代冻结包复验。受控 Python 执行仍被 `0xC0000022 STATUS_ACCESS_DENIED` 阻止；用户最终 Word 模板仍未验收。

## 2026-10-17 Codex 状态修正与 v7 冻结版复验

- Codex 启动配置额外显式关闭 `code_mode` 与 `code_mode_only`。固定 CLI 的 `features list` 仍把 `unified_exec` 报为 enabled，因此该清单不作为有效工具集证明；连接回声也不代表工具清单或沙箱边界已验证。
- 再次通过默认 `MainWindow` 的“验证 Codex 文本连接”按钮发出固定提示。报告：`F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-gates-20261017\codex-ui-canary-v7\default-window-button.json`。结果 `connection_verified=true`、`available=false`、`evidence_verified=false`；界面写明只证明文本连通、没有发送题目或文件、自动运行保持关闭。请求使用空白临时应用数据目录，没有项目文件。
- `SettingsView.set_codex_probe_status` 现在拒绝任何可用或证据已通过的状态，不能把仅连接检查显示成完整预检通过；CLI 范围预检已把取消事件传给 Provider probe。Codex 启动参数、状态边界和取消传递的回归测试均通过。
- 为保证全量测试稳定，给本地 `tests` 包增加显式包标记；三个隔离 Qt 子进程显式使用 F: 项目与 `src` 搜索路径；PowerShell 输出按 UTF-8 解码，避免当前系统代码页导致的未处理线程警告。
- 完整 pytest：**396 passed，1 skipped，635 subtests passed**，退出码 0；跳过项是当前 Windows 账户无权创建符号链接的测试。日志：`F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-gates-20261017\full-suite-v8\pytest-full.log`。39 条 Qt 第三方弃用警告不影响退出码。
- v7 EXE：`F:\Codex另一个工作的地方\downloads\mathmodel-paper-studio-codex-deepseek-20261017-v7\MathModelPaperStudio\MathModelPaperStudio.exe`；SHA-256：`FB2C71109259AC1269CEADEAFE19253251CEF2EA15C8046A8F9990AC70C424A1`。包清单 **317 files**，归档 **731 members**，依赖锁、敏感内容扫描和归档审计通过；报告分别为同目录的 `package-files.json` 与 `archive-members.json`。
- 冻结启动检查退出码 0、`status=ok`、`frozen=true`、Python 3.13.14，所有管理路径均在 F:。报告：`F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-gates-20261017\frozen-startup-v7-wait\startup.json`。另以空白 F: 数据根启动冻结 GUI，观察到窗口标题“数模论文工作台”并正常关闭；数据根：`F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-gates-20261017\frozen-gui-v7\data`。
- DeepSeek 固定 API canary 的上次实测结果仍见上文 `deepseek-live-verify-20261017-userenv\deepseek-canary.json`；没有为了重复验证而再次发起计费请求。
- **尚未通过：**受控 Python smoke load 曾返回 `0xC0000022 STATUS_ACCESS_DENIED`，生成代码仍不会执行；Word 6 页验收仍是合成夹具，用户最终模板页面尚未验证。v7 是预览版，不能完成最终 Word 工作流。

## 2026-10-17 Fluent UI 重设计包验收（部分通过）

### 自动化回归与源码 UI 证据

- Fluent 相关源码 UI 回归：`43 passed, 39 warnings`，耗时 141.66 秒；完整 pytest：`396 passed, 1 skipped, 39 warnings`，耗时 320.56 秒。跳过项为当前 Windows 账户不允许创建符号链接的 `tests/unit/projects/test_project_service.py:182`。日志：`F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-fluent-20261017\task7-scoped-ui-final.log`、`F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-fluent-20261017\task7-full-suite.log`。没有生成覆盖率报告，不据此声称达到 80% 覆盖率。
- Task 6 源码可视界面记录：`.superpowers/sdd/2026-10-17-fluent-ui-redesign/task-6-report.md`。它和本节的冻结包证据分开；源码层视觉检查不能替代冻结包完整交互验收。

### 构建与冻结包清单

- 构建脚本：`scripts/build_windows.ps1`，指定 F: 隔离解释器 `F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-fluent-20261017\venv-task1\Scripts\python.exe`（新 `-PythonPath` 参数；BuildRoot/OutputRoot 使用脚本的本任务 F: 默认值）。`-CheckOnly` 依赖锁检查返回 `status=ok`；另以 `F:\outside-approved-root\python.exe` 检查拒绝越界解释器路径，输出根目录在拒绝前未创建。构建日志：`F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-fluent-20261017\task7-buildscript-checkonly.log`、`task7-buildscript-invalid-pythonpath.log`、`task7-package-build.log`。
- 新预览包 EXE：`F:\Codex另一个工作的地方\downloads\mathmodel-paper-studio-fluent-20261017\release\MathModelPaperStudio\MathModelPaperStudio.exe`。
- EXE SHA-256：`B46BF49F834277CE041558065F8269EB849C12B932BBE0B78DC48DEF7F0D878D`。清单为 317 个文件、131271756 字节；归档审计列出 731 个成员。报告：`F:\Codex另一个工作的地方\downloads\mathmodel-paper-studio-fluent-20261017\release\package-files.json`、`archive-members.json`。
- 包含 1.11.3 版 PySide6-Fluent-Widgets 及其 GPLv3 LICENSE、Qt/CPython 等许可文件；包清单没有 PyQt 路径。
- 冻结启动检查退出码为 0，`status=ok`、`frozen=true`、Python 3.13.14；检查所列数据库、项目、日志、缓存、临时和构建路径均位于本任务 F: 临时数据目录。报告：`F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-fluent-20261017\packaged-startup-report.json`。

### 冻结包可见界面与未验证项

- 新包窗口以标题“数模论文工作台”启动，窗口响应正常；截图 `F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-fluent-20261017\packaged-visible-main-window.png` 显示暗色“项目与数据”空状态及本任务 F: 数据目录。
- UI Automation 调用和一次定向点击没有让侧栏切换到设置页；原生文件选择器也未能完成交互。`packaged-visible-settings.png` 和 `packaged-visible-settings-after-message.png` 两个文件名容易误导：实际画面仍是“项目与数据”页，不能作为设置页证据。尝试随后停止，没有创建项目或数据库内容。
- **冻结包的设置页、项目创建/导入、模板选择与映射保存、Provider/数据范围披露、运行/取消/恢复、产物打开均 NOT VERIFIED。** 因而无法仅凭当前截图确认这些包内按钮能否操作；后续需在真实桌面上手动复核或使用可确认目标控件的自动化。
- 冻结包的系统浅色主题和真实高 DPI 屏幕验收也未完成。Task 6 的模拟浅色调色板和逻辑缩放截图不等于原生系统主题/多显示器验证。
- 本节验收的是 Fluent UI 改版预览包，不解除项目既有安全门：生成代码执行仍为 fail-closed；真实用户 Word 模板的最终页面渲染仍未验证。

## 2026-10-17 LPAC profile-free feasibility follow-up

- To avoid consuming another C: AppContainer-profile exception, ran the existing profile-free LPAC prototype with all generated files under F:. It reports `profile_created=false` and `original_runtime_modified=false`; the child was not created because `CreateProcessW` returned Windows error 2. No smoke or behavior canaries ran. Evidence: `F:\Codex另一个工作的地方\tmp\lpac-prototype-20261017-v2\lpac-prototype-result.json`; SHA-256 `60FA73C7998ED5FDF28FFE5864B85D95AF9A48159FB7EAA5A0285C342341DB45`.
- This does not clear the controlled-execution gate. The earlier profile-backed attempt remains the only launch evidence: token/Job attestation passed, but Python exited with `0xC0000022 STATUS_ACCESS_DENIED` before output; the temporary profile and registry entries were verified absent after cleanup. Another profile-backed diagnostic attempt needs a new explicit C: exception.
- The F: app-data directory contains no `.docx` or `.dotx`; neither the v7 package nor the current Fluent UI preview package contains a Word template. The current preview EXE SHA-256 is `B46BF49F834277CE041558065F8269EB849C12B932BBE0B78DC48DEF7F0D878D`. The 6-page synthetic-fixture rendering remains the only page-render evidence. Final-template acceptance is pending an uploaded or F: template.
## 2026-10-17 GitHub 模板检索与 LPAC 重试

### GitHub 论文模板

- 按用户提供的 zhanwen/MathModel 仓库检索，找到最新的 2025 年“华为杯”第二十二届研究生数学建模论文模板，源格式是旧版 Word .doc。源码：[GitHub 模板](https://github.com/zhanwen/MathModel/blob/master/2025%E5%B9%B4%E6%95%B0%E6%A8%A1%E6%82%89%E7%9F%A5%26%E8%AE%BA%E6%96%87%E6%A8%A1%E7%89%88/%E9%99%84%E4%BB%B63%EF%BC%9A%E2%80%9C%E5%8D%8E%E4%B8%BA%E6%9D%AF%E2%80%9D%E7%AC%AC%E4%BA%8C%E5%8D%81%E4%BA%8C%E5%B1%8A%E4%B8%AD%E5%9B%BD%E7%A0%94%E7%A9%B6%E7%94%9F%E6%95%B0%E5%AD%A6%E5%BB%BA%E6%A8%A1%E7%AB%9E%E8%B5%9B%E8%AE%BA%E6%96%87%E6%A8%A1%E6%9D%BF.doc)。
- 下载副本：F:\Codex另一个工作的地方\downloads\mathmodel-github-template-20261017\2025-huawei-cup-paper-template.doc；659,456 字节；SHA-256 2FE99E580833602E49A714BDA1883AB56EBFCEBFC48F77AA74053E23A6185FB0。原仓库文件未改动。
- Word 页面验收仍 NOT VERIFIED。仓库找到的是 .doc，F: 环境没有 Word/LibreOffice 渲染器；唯一发现的 Word 2024 安装位于 C:，依据 F: 文件策略没有启动它，也没有尝试转换。下载的 .doc 是仓库候选模板，不等同于用户最终模板；6 页合成夹具仍是唯一已通过的渲染验收。

### 第二次且唯一获批的 Profile-backed LPAC 重试

- 本次在 F: 临时目录构建无 CRT 原生 DLL-load 探针，并在同一个临时 AppContainer/LPAC/Job 中先尝试启动探针，再尝试 Python smoke。CreateProcessW 在探针启动前返回 Windows 错误 623；探针与 Python smoke 均未执行，未产生行为 canary。
- Microsoft 将 623 定义为 ERROR_ILLEGAL_DLL_RELOCATION：系统 DLL 重定位到预留地址范围；本机错误消息确认该定义。精确冲突 DLL 未能从本次错误中识别。[Microsoft System Error Codes (500–999)](https://learn.microsoft.com/en-us/windows/win32/debug/system-error-codes--500-999-)
- 清理通过：DeleteAppContainerProfile 返回 0；harness 与独立只读检查均确认临时 Packages 目录及 Mapping、Storage SID、Storage name 三个注册表项不存在。证据目录：F:\Codex另一个工作的地方\tmp\lpac-profile-retry-20261017-v2\。
- 受控 Python 执行仍 BLOCKED，安全门继续关闭。用户批准的第二次 Profile 重试已用完；没有再创建 Profile。此前 CodexCliAdapter.stream() 也仍是 fail-closed，Codex 文本 canary 只证明文本连通，不证明自动工作流或隔离；DeepSeek 的真实固定 API canary沿用前次证据，本轮没有重复发起计费请求。

## 2026-10-17 LPAC 常用映像基址重试

- 按获批范围在 F: 临时目录构建并测试 64 位 DLL-load 探针：PE ImageBase=`0x140000000`，带 DIR64 base-relocation 项，DllCharacteristics=`0x160`；SHA-256 `30EA52F2F7E579396A0B54A766A98F583294E9A8044ED5576A8BAC0447740B99`。同一探针在普通启动下加载 Python DLL 成功，输出 `PYTHON_DLL_LOAD_OK`，退出码 0。
- 在临时 LPAC + Job 中，`CreateProcessW` 对该探针仍返回 Windows 错误 623（`ERROR_ILLEGAL_DLL_RELOCATION`）。失败早于子进程 token 核验、Python smoke 和任何行为 canary；本次未启动生成代码。探针与 Python smoke 均未执行。
- 这次尝试的证据：`F:\Codex另一个工作的地方\tmp\lpac-imagebase-profile-retry-20261017-v1\profile-run-38242fa741f44dd3a3931393eda95da3\evidence.json`。Profile 创建成功；清理 `DeleteAppContainerProfile` 返回 0，临时 Packages 目录、Mapping SID、Storage SID、Storage name 均核验不存在。
- **受控执行仍 BLOCKED。** 采用可重定位探针和常用映像基址没有改变 LPAC 的 623。基于连续失败，不再盲目更换映像参数；需要更强的 Windows Loader 诊断或改用经审查的隔离架构。临时 Profile 例外已用完，后续再次创建需新的明确授权。
## 2026-10-17 DeepSeek 状态展示、设置导航与 v9 冻结包复核

### 本轮源码修正

- 设置页的 DeepSeek 连接说明改为读取当前 ProviderStatus.message，能显示本机环境变量中是否检测到 API Key，并明确区分“本机发现 Key”和“远端连接未验证”；相应 UI 回归断言通过。
- 设置页导航项改放在 Fluent 主导航的顶部列表。隔离 Qt 导航测试验证四个页面均可见，并断言“设置”位于顶部导航。
- Windows PowerShell 5.1 构建脚本不再依赖含中文的硬编码 F: 根目录字面值；它从 F: worktrees/repo 结构推导已批准根目录。v9 构建与 F: 路径门禁检查通过。

### 测试结果

- 设置导航改动前，完整 pytest 通过：396 passed、1 skipped、635 subtests passed、39 warnings。日志：F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-ui-status-20261017\pytest-full-after-fixes.log。跳过项是当前 Windows 账户无权创建符号链接的测试。
- 设置导航改动后的隔离 Qt 导航 harness 通过：1 passed。DeepSeek 状态显示断言已包含在上面的完整测试结果中。
- 最终源码重跑 DeepSeek 状态与设置导航两组回归：9 passed in 2.96s，退出码 0。日志：pytest-provider-navigation-final.log，位于上述同一 F: 目录。
- 对导航改动后的完整套件重跑两次，均在项目向导 worker 的 Path.stat()/realpath() 路径中遇到 Windows fatal exception 0x8001010d，进程随后挂起并被中断；日志：pytest-full-after-navigation-fix.log、pytest-full-after-navigation-fix-retry.log，位于上述同一 F: 目录。因此不能宣称当前最终源码树的完整套件通过。
- 单独重跑失败重试用例通过：1 passed，38.40 秒；模块级重跑超过两分钟未完成并中断，未得到模块级通过结论。失败重试用例日志为 pytest-project-wizard-isolated-retry.log，位于同一 F: 目录；模块级复跑中断时未产出可用汇总。

### v9 冻结预览包

- EXE：F:\Codex另一个工作的地方\downloads\mathmodel-paper-studio-codex-deepseek-20261017-v9\release\MathModelPaperStudio\MathModelPaperStudio.exe
- SHA-256：F01746B7C49F253F4A606301296D99F1D9F4E8BA8CE64B1E84D9D493774A9913。清单 317 个文件、131,271,573 字节；归档 731 个成员；包清单与归档审计状态均为 ok。证据：该 release 目录的 package-files.json、archive-members.json。
- 冻结启动检查通过：status=ok、frozen=true、Python 3.13.14，所列管理路径均位于 F:。报告：F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-codex-deepseek-20261017-v9\startup\frozen-startup.json。
- 冻结包截图 gui-v9-maximized.png 显示主导航中的齿轮设置入口；gui-settings-click.png 显示能进入设置页。它们证明页面可见，不证明下拉框已切到 DeepSeek 或实时 GUI 里的 API 状态；DeepSeek 标签文字由源码回归测试覆盖。测试实例随后已结束。

### 尚未通过的完整工作流门禁

- **Codex：**前次默认 MainWindow 的固定文本 canary 只验证过文本往返，结果仍为 available=false、evidence_verified=false；这次没有重复发起 CLI 请求。项目范围、完整能力和执行隔离仍未通过，因此自动运行关闭。
- **DeepSeek：**前次应用客户端的固定 API canary 返回匹配文本；本轮未重复计费请求。设置页现在能如实显示本地 Key 与远端验证状态，但这不能替代新一次真实请求或额度检查。
- **受控 Python 执行：BLOCKED。**最后一次获批的 LPAC 探针在普通启动下可加载 Python DLL，但在 LPAC + Job 中 CreateProcessW 仍返回 623 / ERROR_ILLEGAL_DLL_RELOCATION。失败发生在子进程启动前，没有运行 Python smoke 或行为 canary。临时 AppContainer Profile、Packages 目录和相关注册表项均已核验清理；本次授权重试已用完。
- **Word 页面验收：NOT VERIFIED。**GitHub 仓库找到的候选模板是旧格式 .doc；F: 环境没有可用渲染器。用户最终 .docx/.dotx 模板尚未取得。已通过的 6 页渲染仍是合成夹具。
- **最终结论：**v9 是可启动的冻结预览包，设置导航和状态文案已改进；当前完整 pytest、LPAC 受控执行和用户最终模板页面验收仍未通过。它尚不能验收为完整自动生成论文的正式版本。
## 2026-10-17 完整回归续验与剩余门禁

本节更新此前“完整 pytest 未通过”的阶段性记录。此次在最终测试清理顺序调整后，完整测试套件通过；历史日志仍保留其当时失败结果。

### Qt 项目向导线程清理与完整测试

- `tests/integration/ui/test_project_wizard.py` 使用该测试类专属的 `QThreadPool`，并追踪测试创建的向导。统一清理回调先等待工作线程结束，再处理 Qt 事件、关闭向导、再次处理事件，最后删除临时目录。若 15 秒等待超时，会先继续等待线程完成再清理，同时让测试以超时失败，避免线程访问已删除目录。
- 只读代码复核未发现剩余的临时目录或窗口清理竞态。取舍是 worker 永久卡住时清理也会等待；这可避免并发删除其仍在访问的目录。
- 最新完整 pytest：**396 passed，1 skipped，39 warnings，635 subtests passed**，退出码 0，耗时 255.50 秒。日志：`F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-ui-status-20261017\pytest-full-after-final-cleanup.log`。唯一跳过项是当前 Windows 账户不允许创建符号链接；39 条 Qt 第三方弃用警告未导致失败。
- 此修正只涉及集成测试清理；本轮没有修改产品运行时代码。当前冻结预览包仍为 v9，本轮没有重建 EXE。

### 仍未通过的完整工作流门禁

- **受控 Python 执行：BLOCKED。** 最近获批的 LPAC `CreateProcessW` 探针仍在创建子进程前返回 Windows 错误 623；Python smoke、token attestation 和行为 canary 均未运行。此前临时 Profile、Packages 目录和相关注册表项已核验清理。本轮没有再次创建 Profile，也没有运行生成代码；自动执行继续 fail-closed。
- **最终 Word 模板页面验收：NOT VERIFIED。** 已找到的 GitHub 候选模板为旧格式 `.doc`，不是用户最终 `.docx/.dotx`。指定 F: 环境未发现可用的 Word/LibreOffice 渲染器，用户指定输出目录中也没有最终 `.docx/.dotx`；合成夹具的 6 页渲染不代表该模板。本轮没有生成最终论文。
- **Agent 范围：部分通过。** Codex 最近的应用内固定文本 canary只验证文本往返，仍不证明完整工具能力、项目范围或执行隔离；DeepSeek 的固定 API canary沿用先前成功记录，本轮没有再次发起可能计费的请求。两者都不能解除上述执行与最终文档验收门禁。
- **结论：** 完整 pytest 现已通过，但受控计算执行和最终模板渲染仍阻止完整工作流验收；v9 仍是预览包，不能标记为正式完整版本。

### 本轮证据位置

- 完整 pytest 日志：`F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-ui-status-20261017\pytest-full-after-final-cleanup.log`
- 最终模板候选 `.doc`：`F:\Codex另一个工作的地方\downloads\mathmodel-github-template-20261017\2025-huawei-cup-paper-template.doc`