# 2026-10-17 验收继续记录

本记录追加本轮冻结包检查结果，不覆盖已有的 `acceptance.md` 或 Fluent UI 验收记录。所有本轮测试、截图和应用数据均位于 F:。

## 冻结 Fluent 包设置页

- 验收包：`F:\Codex另一个工作的地方\downloads\mathmodel-paper-studio-fluent-20261017\release\MathModelPaperStudio\MathModelPaperStudio.exe`
- SHA-256：`B46BF49F834277CE041558065F8269EB849C12B932BBE0B78DC48DEF7F0D878D`。
- 用隔离数据根目录 `F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-fluent-20261017\elevated-package-acceptance\data` 启动。应用窗口标题为“数模论文工作台”，设置页可见。
- 真实下拉菜单只有 `Codex` 和 `DeepSeek API` 两项，没有 `Claude`。截图：`F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-fluent-20261017\elevated-package-acceptance\fluent-provider-dropdown.png`。
- 临时选择 DeepSeek 时，界面状态为“DeepSeek API 客户端已装配到工作流；本次未发送 API 请求，远端连通性未验证。”运行预检显示“未检测到有效的 DEEPSEEK_API_KEY 环境变量。”启动环境中只检查了 Key 是否存在，结果为 false；未读取、显示或记录密钥值。
- 未点击连接测试，也未发起 DeepSeek 请求。界面状态是包内接线声明，不等同于实时 API 连通或完整 Agent 工作流验收。先前固定提示 canary 的证据仍在 `F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-gates-20261017\deepseek-live-verify-20261017-userenv\deepseek-canary.json`，本轮没有重复计费请求。
- 检查结束前把 Provider 选择恢复为 Codex，并关闭本轮启动的验收进程。没有启动工作流或模型生成。

## 受控 Python 执行

- `tests.unit.execution.test_python_runner`：**8 passed，0 failed**。日志：`F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-fluent-20261017\elevated-package-acceptance\python-runner-fail-closed.log`。
- 这些用例确认在未获可信沙箱证明时脚本写入仍被暂停，且不会创建/启动生成脚本的子进程。它们没有运行生成代码，也没有证明 OS 沙箱隔离成功。
- 可信执行门仍为 **BLOCKED**：已有 profile-backed LPAC smoke load 返回 `0xC0000022 STATUS_ACCESS_DENIED`；profile-free 原型在创建子进程时返回 Windows error 2。用户先前批准的临时 AppContainer Profile 例外已用于清理和重试，不能据此推定新的 Profile 操作也获准。未创建新的 Profile、注册表项或 C: 文件。

## Word 页面渲染

- 先前 6 页 Word 2024 合成夹具渲染继续作为合成样例证据；它不代表用户最终模板或最终论文。本轮未启动 Word，也没有新的页面渲染。
- 用户最终 Word 模板仍未提供。当前可用 Word 安装在 C:；按项目的 F: 文件政策，本轮没有启动它或让它写入 C: 用户配置。因此最终模板渲染仍为 **NOT VERIFIED**。

## 本轮结论

本轮新增通过：冻结包设置页和 Provider 菜单可见复核；PythonRunner fail-closed 单元测试 8/8。Claude 未出现在冻结包 Agent 列表中。DeepSeek 包内 UI 声称客户端已装配，但本轮没有真实请求；先前 canary 不能代替完整运行。受控代码执行与用户最终 Word 页面仍未通过，应用仍是预览状态，完整 MVP 验收未完成。