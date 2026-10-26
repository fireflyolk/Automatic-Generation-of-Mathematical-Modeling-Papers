# 测试套件修复与生产缺陷修复 — 2026-10-25

## 结论

在本工作副本(活跃开发副本)上,全量 pytest 从 **66 failed / 77 errors** 修复至
**927 passed, 1 skipped, 0 failed,698 subtests passed**(包括此前一直失败的
2 项 Windows Sandbox 实时集成测试)。其中 4 处为生产源码缺陷,其余为测试夹具
落后于 10-21~10-24 引擎重构、以及测试环境配置问题。

## 生产源码修复(src/)

1. **`workflow/live_engine.py` — `paper_standard_id` 属性崩溃**
   `_request()` 直接访问 `context.paper_standard_id` 等三个属性;测试用
   `SimpleNamespace` 构造的最小 context 无这些字段,触发
   `AttributeError` 后阶段暂停。同文件已有防御性 `_standard_for_context()`
   (getattr + 默认规范回退),改为统一走该 helper。

2. **`workflow/live_engine.py` — 论文结果批缺少
   `required_input_figure_evidence_ids` 披露**
   `test_every_planned_input_figure_is_required_in_paper_results` 要求结果批
   请求向 Agent 披露计划输入图对应的证据 ID;校验逻辑存在但披露字段从未实现
   (部署 EXE 字节扫描确认字段缺失,属于写了期望、实现未跟上的用例)。按模块
   已有的 `required_result_figure_evidence_ids` 模式实现:从 paper_contract 的
   `planned_input_figures`/`required_figure_output_names` 按文件名匹配已验证
   figure 证据,仅在 results 批的 `paper_batch` 中披露,不影响既有缓存兼容。

3. **`workflow/live_engine.py` — `required_figures` 重复键使 DOCX QA 崩溃**
   起草层允许多张结果图共享一个标题锚点(有单测背书),但论文清单把重复
   `section_key` 原样写入 `required_figures`;QA 的 `_keys()` 去重检查直接抛
   `ValueError` → 运行在 DOCX_QA 暂停。用户模板只有一个"结果图"标题且结果图
   ≥2 张时必现。修复:清单构造处 `dict.fromkeys` 保序去重(语义是"必须出现的
   锚点键集合",重复无意义)。

4. **`paper/workflow_notes.py` — 发布过滤器漏检"手动"变体**
   过滤器只认"待人工…/人工…/请…人工…"等模式,"请手动补充真实数据。"这类
   流程性待办漏进发布文本。`_MARKERS` 与 `_PROCESS_ANNOTATION` 同步增加
   "手动"分支;`workflow_note_fragments`/`publication_text` 行为保持一致,
   `test_publication_filter` 4/4 通过。

## 环境与脚本修复

5. **锁定计算运行时协议同步**
   `downloads\mathmodel-paper-studio-sandbox-20261018\computation-runtime-final`
   中的 `sandbox_entry.py` 仍是 10-19 修复前的旧版
   (SHA-256 [本地发布哈希已省略]),
   与 `packaging/runtime/sandbox_entry.py`(F32381C8…)不一致,导致
   `build_windows.ps1 -CheckOnly` 报"Sandbox launcher does not match the
   current host protocol"。已同步修复版并用运行时自带 Python 重新生成
   `runtime-manifest.json`。该运行时目录仅含应用打包资源;C 盘安装与桌面
   快捷方式未改动。

6. **测试环境标准**
   本仓库测试要求 `TEMP`/`TMP` 指向 F 盘(多个测试断言
   `TEMP.drive == 'F:'`),且 pytest 需要 `--basetemp` 指向 F 盘:
   `[本机路径已省略]` 的 ACL 已损坏
   (连 icacls 都拒绝访问),曾造成 77 个 error。标准跑法:

   ```bat
   set TEMP=[本机路径已省略]
   set TMP=%TEMP%
   .venv\Scripts\python.exe -m pytest tests -q --basetemp=…\pytest
   ```

## 测试夹具现代化(tests/)

7. **`test_live_workflow.py`(30 failed → 47/47)**
   夹具停留在分批论文/figure_plan 之前的引擎时代。升级内容:
   - `FakeProvider`:按提示中的 `figure_plan` 动态声明全部计划图;论文阶段
     解析 `paper_batch`,按 `requested_section_keys`/schema 生成章节、按
     `problem_coverage` 生成以"问题一"开头的 ≥2 段小节(带证据)、results 批
     按 `result_artifact_anchors` 生成表/图;代码修复请求回放首次声明的
     outputs(修复提示不含原始声明)。
   - `FakeComputation`:按 `expected_outputs`(ArtifactExpectation)生成
     合法 CSV/PNG,NaN 投毒仅作用于声明的表。
   - 标准模板夹具:新增 `write_standard_fixture()`(fixtures/docx),覆盖
     B078 全部必需章节 + 结果图锚点 + 结果表,setUp 映射同步补全。
   - 断言更新:论文请求次数(8 批 + 可能的纠正)、DeepSeek scope 次数、
     进度事件(AGENT_RETRYING 措辞)、内置模板中文标题、正文含标题而非内部
     证据 ID。
8. **`tests/fixtures/docx/fixture_factory.py` — `unsupported_text_box()` XML 非法**
   开标签 `wp:txbxContent`、闭标签 `w:txbxContent` 前缀不匹配,
   `mismatched tag` 解析失败先于 UnsupportedTemplateError;统一为
   `w:txbxContent`(与检测器一致)。书签 id 在新标准夹具中保持唯一。
9. **过时期望更新**
   - `test_mvp_workflow.py`:证据 ID 现记录于 `customXml/mmps-paper-review.xml`
     审计部件(10-21 改造),正文不再含内部 ID;改为断言审计部件。
   - `test_docx_generation.py`:传 `standard=LENIENT_STANDARD` +
     `standard_sections=('abstract',)` 隔离 7000 字门槛,保持用例本意
     (pending citations → review_required 而非 failed)。
   - `test_template_mapping.py`:切换项目后视图显示内置 B078 规范是 10-22
     起的有意行为(docstring),断言改为 `内置默认`。
10. **`test_run_observer_flow.py` — SSE 竞态(1/3 通过的 flaky)**
    断言与 worker 线程到达第二次 read 存在竞态。改为合取等待
    (文本可见 且 第二次 read 已被 before_read 钩子阻塞),3 轮全过。

## 验证

- 全量:`pytest tests -q`(F 盘 TEMP + F 盘 basetemp)
  **927 passed, 1 skipped, 698 subtests passed,0 failed**,耗时约 6 分钟。
- 唯一 skip 为既有的符号链接权限跳过项(与本次改动无关)。
- 未运行真实 DeepSeek/Codex 付费生成;未重新打包 EXE;未改动
  `projects\` 快照仓库、C 盘安装与用户项目数据。

## 已知边界

- 上述自动化验证不证明数学正确性、渲染页数或实际 Agent 行为;
  论文产物仍需人工复核(应用内 `human_review_required=true` 不变)。
- `projects\Automatic-Generation-of-Mathematical-Modeling-Papers` 快照仓库
  未同步本副本的修复;下次打包/发布应以本副本为准。
