# Fluent 预览包交互验收补充（2026-10-17）

## 验收对象与边界

- 冻结 EXE：`F:\Codex另一个工作的地方\downloads\mathmodel-paper-studio-fluent-20261017\release\MathModelPaperStudio\MathModelPaperStudio.exe`
- SHA-256：`B46BF49F834277CE041558065F8269EB849C12B932BBE0B78DC48DEF7F0D878D`
- 使用合成项目和夹具，数据、日志、截图均位于 F:；没有发送外部 API 请求或触碰用户项目。

## 已通过的包级验收

- 冻结 EXE 启动并保持响应，窗口标题为“数模论文工作台”。实际桌面鼠标依次点击了项目、Word 模板、运行与审核、设置四个导航项；每次点击后均观察到对应页面内容。测试后恢复原光标位置。
- 在项目页重新载入隔离项目，并看到题目、CSV、DOCX 和运行输入列表。
- 模板页“选择模板”打开 Windows 文件选择器，地址定位到当前项目的 `00_inputs`。选择导入的 `source-0002-v0001.docx` 后，页面显示检查完成：2 个标题锚点、1 个表格锚点。
- 章节映射下拉框提供“不映射”“摘要（Heading1）”“结果（Heading1）”等候选。将“摘要”映射到标题锚点 `heading-0000-2a7a96416f` 后，保存按钮启用且成功写出映射文件：`F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-fluent-20261017\elevated-package-acceptance\data\projects\a43f5c3db0ea432f813c4de9cf6e39de\70_review\template-mapping-v0001.json`。
- 映射 JSON 可解析，包含 `abstract` 到上述标题锚点的唯一绑定；其中的模板 SHA-256 与项目内 DOCX 实测哈希一致：`55154B181D4CD1A35BA70167FA59D379B7EB9A4E93E779A3BE1183AA11D35227`。映射文件 SHA-256：`2E86D1C94554709D88FE679D211024D381A4DCA6945649FA019FC1162DA62B83`。
- 页面截图：`F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-fluent-20261017\elevated-package-acceptance\artifacts\fluent-template-mapping-package.png`；SHA-256：`1F54BD94DCF64D44889835186838053456C9EF46E17F34EBF00548EB6803E7C0`。截图可见模板检查成功状态和已选章节映射。
- 当前 Windows DPI 为 120（125%），单显示器 2560×1440；在此实际显示环境下检查了导航和模板映射页布局。测试 EXE（PID 66544）已正常关闭，原有 PID 2664 未触碰。
- 合成项目创建、CSV/DOCX 导入副本哈希和 SQLite `integrity_check=ok` 见本目录先前记录；本轮未改动该项目数据库。

## 尚未通过或未覆盖

- 本轮没有运行 Agent 生成完整论文；真实用户 Word 模板的最终页面渲染仍未验收。模板检查和映射保存通过不代表最终 DOCX 版面通过。
- 只检查了当前深色主题；切换 Windows 系统主题未验收。
- 当前只有一台显示器；多显示器布局未验收。
- 此报告只覆盖 Fluent 冻结包的 UI 与模板映射交互，不替代 `acceptance.md` 中受控代码执行和真实 Agent 工作流的门禁结果。

## 结论

冻结包的四页鼠标导航、项目重新载入、模板文件选择、DOCX 结构检查、章节锚点选择及映射保存均有实际 EXE 交互和文件证据，以上 UI 验收项通过。完整数模论文工作流仍未验收：真实 Agent 生成、安全代码执行和最终用户 Word 页面渲染不在本轮通过范围内。
