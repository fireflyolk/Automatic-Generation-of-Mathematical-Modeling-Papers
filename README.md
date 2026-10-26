# MathModel Paper Studio

面向个人 Windows 电脑的数模论文工作台。当前交付为预览版：支持 CSV/XLSX/DOCX 数据导入、DOCX/文字版 PDF 题目导入、Word 模板映射、Codex 本机 CLI 文本连接、DeepSeek API 和分阶段工作流界面。新建项目时点击“从 DOCX/PDF 导入题目”，核对提取文字后创建项目；扫描版 PDF 暂不支持 OCR。

## 当前 Agent 接入

- **Codex**：使用固定版本本机 CLI。设置页提供“验证 Codex 文本连接”按钮；验证发送固定提示，不包含题目、数据文件或模板。成功只证明收到固定文本回复，不证明有效工具清单、项目文件隔离或代码执行边界；自动运行仍关闭。
- **DeepSeek API**：从 `DEEPSEEK_API_KEY` 进程环境变量读取密钥。应用不保存密钥；开始运行前显示外发数据范围，并要求本次运行的费用授权。修改环境变量后应完全退出并重新启动应用。
- Claude Code 已按用户决定从活动选项移除。

## 本机运行

```powershell
$exe = Read-Host '输入 MathModelPaperStudio.exe 的完整路径'
Start-Process -FilePath $exe
```

项目、日志、缓存、临时文件和运行产物位于应用配置的数据根目录。请保持配置的数据盘可用；程序不会在 F: 不可用时回退到 C:。

## 发布验收记录

功能和验收状态以 `docs/release/` 中的记录为准。此公开副本已省略机器相关的绝对路径、运行/项目标识和发布物哈希。