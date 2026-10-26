# 论文图表数量提升 — 2026-10-25

## 背景与根因

用户反馈生成论文中流程图/结构图/建模图和数据图数量偏少。逐层检查发现数量
由以下硬性约束卡住:

1. **流程图整篇只有 4 个请求槽位**:`_draft_paper_in_batches` 只在
   analysis / model(推导) / solution / validation 四个批次请求流程图;
   model_core(模型结构)与 closing(技术路线/结论)批次从不请求。
2. **流程图交付完全没有强制**:模型返回空 `flowcharts` 列表时
   `_validate_paper_batch` 只拒绝"多于请求数",空交付直接通过,
   不触发纠正重试,也不产生待办记录(单张"尝试过但超限"的图才有
   FlowchartOmission 宽松路径)。
3. **请求文案主动教模型偷懒**:纠错指导与
   `flowchart_request_constraints()['optional']` 均写明
   "无法满足时返回空 flowcharts 数组"。
4. **数据图配额**:输入数据图计划上限 `_MAX_INPUT_PROFILE_FIGURES = 8`,
   代码产物总数上限 16(`_specifications`),定量结果表强制配图上限
   `min(8, slots)`;三个上限相互挤占,多列数据场景下图更少。

## 修改(src/)

- `workflow/live_engine.py`
  - 流程图槽位 4 → 6:model_core 批新增"模型结构"流程图(锚定 model),
    closing 批新增"技术路线/结论"流程图(锚定 conclusion,
    依次回退 verification / solution)。
  - 空交付强制:批次请求了 N 张流程图、且模型没有提交任何一张
    (过滤后为空且无 FlowchartOmission,即"未尝试")→ 判为契约错误,
    走既有的一次针对性纠正重试;纠正仍失败则暂停,
    诊断文案固定为"流程图数量不足:本批请求 N 张,收到 M 张。"
    ("尝试过但单图超限"仍走既有的宽松遗漏路径,保留正文与人工补图提示,
    与 2026-10-22 的验收设计一致。)
  - `_validate_paper_batch` / `supported_optional_flowcharts`
    单响应流程图上限 4 → 12(整稿合并后可达 6 张)。
  - 数据图:`_MAX_INPUT_PROFILE_FIGURES` 8 → 12;
    代码产物总数上限 16 → 24(校验器上限 128 不变);
    定量结果表强制配图上限 min(8, slots) → min(12, slots),
    slots 按 24 - 表数 - 计划输入图数计算。
  - 纠错指导('flowchart'):改为"本批请求的每张流程图都必须交付;
    初版超限时精简节点/层级后重新提交,不要返回空列表"。
- `workflow/paper_flowcharts.py`:`flowchart_request_constraints()` 的
  `optional` 键改为 `required`,要求必须交付、超限时精简而非省略。

## 兼容性说明

- model_core 与 closing 批的请求哈希因新增流程图字段而变化:老项目恢复
  运行时这两批缓存失效并重新请求(其余批次缓存不受影响)——这是获得
  新增图的必要代价。
- 新的强制可能使"模型连续两次不画图"的运行在论文阶段暂停;用户点击
  继续运行即重新请求,与其它校验暂停的恢复路径一致。

## 测试

- 夹具同步:`draft_with_evidence_field` 与 `FakeProvider._paper_response`
  按批交付合法 4 节点流程图;三处测试文件 mock
  `render_flowchart_png`(无 QApplication 环境不执行真实 Qt 绘制);
  `test_figure_plan_..._is_bounded` 边界 8 → 12;
  两处 Word media 计数断言改为 1/3 + 流程图数。
- 聚焦:workflow/paper 相关单元 102 passed;live 集成 48 passed。
- 全量(F 盘 TEMP + F 盘 basetemp):
  **927 passed, 1 skipped, 698 subtests passed, 0 failed**。

## 边界

自动化验证不证明渲染页数、视觉质量或数学正确性;实际效果以下一次真实
运行(需用户确认费用/范围)为准。本轮未重新打包 EXE,未写任何 C 盘文件。
