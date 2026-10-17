# MathModel Paper Studio

面向个人 Windows 电脑的数模论文工作台。当前交付的是可运行的 v7 预览版：支持项目与 CSV/XLSX 导入、Word 模板映射、Codex 本机 CLI 文本连接、DeepSeek API 和分阶段工作流界面。

## 当前 Agent 接入

- **Codex**：使用固定版本本机 CLI。设置页提供“验证 Codex 文本连接”按钮；验证发送固定提示，不包含题目、数据文件或模板。成功只证明收到固定文本回复，不证明有效工具清单、项目文件隔离或代码执行边界；自动运行仍关闭。
- **DeepSeek API**：从 `DEEPSEEK_API_KEY` 进程环境变量读取密钥。应用不保存密钥；开始运行前显示外发数据范围，并要求本次运行的费用授权。修改环境变量后应完全退出并重新启动应用。
- Claude Code 已按用户决定从活动选项移除。

## 运行 v7 预览版

```powershell
$exe = 'F:\Codex另一个工作的地方\downloads\mathmodel-paper-studio-codex-deepseek-20261017-v7\MathModelPaperStudio\MathModelPaperStudio.exe'
Start-Process -FilePath $exe
```

应用数据默认保存在 `F:\Codex另一个工作的地方\mathmodel-paper-studio-data`；日志、缓存、临时文件和运行结果也受 F: 路径策略约束。F: 不可用时应用不会回退到 C:。

## 尚未通过的运行门禁

- 受控 Python 运行器仍因 Windows `STATUS_ACCESS_DENIED` 保持 fail-closed。工作流可生成代码草稿，但会在计算阶段暂停，不会生成最终 Word。
- Word 的 6 页合成夹具已通过页面检查；用户最终模板尚未渲染验收。
- v7 是预览包，不是正式 MVP 发布。完整 pytest 为 396 passed、1 skipped；受控 Python 执行与用户最终 Word 模板仍未验收，详细证据见[发布验收记录](docs/release/acceptance.md)。

## v7 预览包

- EXE：`F:\Codex另一个工作的地方\downloads\mathmodel-paper-studio-codex-deepseek-20261017-v7\MathModelPaperStudio\MathModelPaperStudio.exe`
- SHA-256：`FB2C71109259AC1269CEADEAFE19253251CEF2EA15C8046A8F9990AC70C424A1`
