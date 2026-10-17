# Fluent UI 交互复核（2026-10-17）

## 结论

源码中真实 Fluent 导航按钮及主要模板/项目向导动作的 Qt 信号路径通过合成数据复核。冻结 EXE 的桌面级鼠标/可见控件复核仍 **NOT VERIFIED**：本次执行环境没有可用的可见窗口输入通道，不能把源代码点击测试冒充成 EXE 桌面验收，也没有证明包内按钮失效。

## 新鲜复核证据

- `F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-fluent-20261017\navigation-click-probe\navigation_click_probe.py` 使用 PySide6 `QTest.mouseClick` 点击每个实际 `NavigationTreeItem` 子按钮，检查 `stackedWidget.currentWidget()`。四个目的页均切换到预期页面：项目与数据 → Word 模板 → 运行与审核 → 设置。报告：`F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-fluent-20261017\navigation-click-probe\navigation-click-report.json`。Qt 平台为 offscreen；未创建项目、未调用网络/API。
- 探针的第一版误点在 `NavigationTreeWidget` 父容器上，三个目的页未切换；检查 QFluent 源码后改点其 `itemWidget`（真正接收鼠标事件的 `NavigationTreeItem`），四页全通过。该初始失败是探针目标选错，不能当作产品导航故障。
- `tests/integration/ui/test_project_wizard.py`：`6 passed in 117.53s`。使用临时 CSV/DOCX，检查导入、取消/重试状态、源文件哈希与窄窗口焦点顺序；不访问 Agent/API。
- `tests/integration/ui/test_template_mapping.py`：pytest 外层 `1 passed in 1.75s`；该包装器还要求隔离 Qt 子进程内的 7 项 unittest 成功退出。模板选择通过 mock 文件对话框验证按钮事件、项目范围保护和源模板哈希不变。
- 前一轮完整 pytest 的结果仍为 `396 passed, 1 skipped`，详见 `F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-fluent-20261017\task7-full-suite.log`。

## 冻结包桌面验收限制

- 本轮尝试用短命 `Start-Process` 启动冻结 EXE 时，受限子进程不能创建新的 F: 临时数据目录，启动钩子只显示通用的 F: 路径错误。经授权的 F: 初始化检查返回 `status=ok`，说明该测试根本身可通过应用路径策略。
- 在当前命令会话中分别启动冻结 EXE 和源码桌面入口后，Win32/UIA 都没有枚举到对应应用的顶层窗口或控件；物理鼠标位置设置调用也被环境拒绝。两种启动都受此限制，故不能据此归因于 Fluent 导航或产品代码。
- 没有更改 UI 源码、没有重建 EXE，也没有改动桌面快捷方式。新 EXE 仍是 `F:\Codex另一个工作的地方\downloads\mathmodel-paper-studio-fluent-20261017\release\MathModelPaperStudio\MathModelPaperStudio.exe`，SHA-256 `B46BF49F834277CE041558065F8269EB849C12B932BBE0B78DC48DEF7F0D878D`。

## 仍需的最终验收

要关闭包级视觉验收，需要在真实交互式 Windows 桌面启动上述 EXE，手工点击侧栏和文件选择按钮，或提供能向该桌面发送输入并返回截图的工具。未验证项仍包括冻结包内项目创建/导入、模板映射、设置页、运行/取消/恢复、产物打开及原生浅色/高 DPI。