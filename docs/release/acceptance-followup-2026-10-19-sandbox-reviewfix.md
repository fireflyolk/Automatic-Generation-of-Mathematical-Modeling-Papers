# Windows Sandbox 计算后端续验收

日期：2026-10-19（Asia/Shanghai）

## 修复内容

- 计算运行时路径由统一解析器选择；冻结包使用 EXE 随附的 `_internal/computation-runtime`，源码模式使用经验证的 F: 运行时路径。
- Sandbox 启动前持久化恢复记录；执行中的会话由进程内注册表跟踪。并发运行不会清理仍活动的会话；当前应用中的已结束会话可以重试恢复。
- 旧版恢复记录只在 Sandbox 会话已不存在时清理。会话列表查询超时或记录无效时安全暂停并保留记录。
- 对暂停、取消和失败的计算结果保留有长度上限的后端诊断，避免 UI 隐藏“尚不能确认 Sandbox 已关闭”的警告。
- 输入快照继续校验已审核文件哈希；输入与运行时映射只读，输出单独映射且受文件名、结构和大小限制。

## 构建产物

- EXE：`F:\Codex另一个工作的地方\downloads\mathmodel-paper-studio-sandbox-20261019-reviewfix\release\MathModelPaperStudio\MathModelPaperStudio.exe`
- SHA-256：`71AFDD8CFFD1D07AEC46B1EC38929F91FCD69CD85CCB7678D459020E334217EC`
- 包目录：7,886 个文件，433,454,319 字节；内置运行时：7,565 个文件、20 个锁定依赖；EXE 归档：798 个成员。
- 构建依赖锁检查、运行时清单/锁检查、包路径与敏感内容扫描、EXE 归档审计均通过。
- 审计报告：同一 `release` 目录中的 `computation-runtime-files.json`、`package-files.json` 和 `archive-members.json`。
- 冻结启动报告：`F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-sandbox-20261019-reviewfix\packaged-startup-report.json`。结果为 `status=ok`、`frozen=true`、Python 3.13.14；应用数据库、缓存、项目与临时路径全部位于 F:。

## 验收结果

- 执行与工作流定向测试：51 passed，5 个子测试通过。
- 最新 Sandbox 单元测试（含 Windows 当前进程句柄探测）：19 passed。
- 执行、工作流、计算产物、MVP E2E 与 Sandbox 集成组合：112 passed，38 个子测试通过。
- 构建后使用新包内的计算运行时运行真实 Windows Sandbox 集成用例：1 passed，34.98 秒。
- Sandbox 生成的 CSV 与 PNG：
  - `F:\Codex另一个工作的地方\tmp\windows-sandbox-live-53799d1f706f\app\projects\sandbox47bf9c18\90_runs\run47bf9c18\40_results\calc-result.csv`
  - `F:\Codex另一个工作的地方\tmp\windows-sandbox-live-53799d1f706f\app\projects\sandbox47bf9c18\90_runs\run47bf9c18\50_figures\calc-plot.png`
- 结果记录：`execution-v0001.json` 显示 `stage_status=completed`、`execution_started=true`、`exit_code=0`、解释器 `sandbox:python.exe`；`validation-v0001.json` 中 CSV 与 PNG 均结构校验通过。
- 合成回归结果：斜率 2.0、截距 1.0；输入写入被只读映射阻止，宿主哨兵路径不可见，外网连接失败。依赖导入成功：NumPy 2.5.4、pandas 3.0.6、SciPy 1.18.1、Matplotlib 3.11.2、python-docx 1.2.0、openpyxl 3.1.5、pypdf 6.21.1。
- 最新增量只读代码复核未发现新的可操作问题。

## 验收边界

- 真实 Sandbox 证据来自合成数据；没有验证数学结论的正确性。
- 本次未发起 Codex CLI 或 DeepSeek API 的真实生成请求。
- 没有用用户最终 Word 模板做页面渲染验收。
- 新 EXE 位于上述独立 release 目录；本次没有改动桌面快捷方式。
