# ScopeX 受约束业务报告

更新：2026-09-14，主链已实现，真实模型表达质量待验收。

## 1. 职责边界

```text
OpenClaw：调查 / 决策 / 工具 / 业务动作 / 验证 / 停止
  -> Evidence
Fresh Structured Finalizer：依据当前目录和直接附加原图，生成Claims
  -> 结构、引用与分类校验
Constrained Report Composer：一次无工具模型请求组织中文
  -> 报告引用/分类校验
用户：结论 / 事实依据 / 可能性分析 / 下一步 / 数据限制
```

Report Composer 不是另一个 Agent，不读文件、不调工具、不增加Evidence、不重新诊断。`scopex/finalizer/answer.py` 的确定性renderer仅保留兼容/fallback，不能继续靠每个业务字段一条翻译规则建设通用产品。

## 2. 输入和输出

输入为用户问题、已通过当前校验的Claims及其引用的Evidence。优先提供相关结构化事实与少量具体事件；不发送全部工具过程。原始证据必须视为数据而非执行指令。

固定schema：

```json
{
  "version": 1,
  "conclusion": {"text": "直接回答问题", "claim_ids": ["C1"], "evidence_refs": ["E1"]},
  "facts": [{"text": "重要观察事实", "claim_ids": ["C1"], "evidence_refs": ["E1"]}],
  "possibilities": [],
  "next_steps": [],
  "limitations": []
}
```

区块可空，不为了填满模板而制造可能性/建议。facts最多6条、possibilities4、next_steps4、limitations3；条目文本和引用有界。

## 3. Prompt约束与代码实际保证

Prompt要求模型：保持数字/单位/时间/范围；事实与假设分开；不新增原因；未知不能变确定；输出自然中文而非JSON字段/源码/工具链；建议只能围绕已有未决问题，不把未执行动作写成已完成。

**Prompt目标不等于代码证明。** 当前validator保证的是schema、引用归属、条目长度以及对应Claim分类：facts仅fact+observed；可能性/下一步/限制须按非observed或未决Claim规则。conclusion可综合已有Claim，但代码尚不能完全判断其自由文本是否夸大确定性。

它并不能仅凭C/E编号正确，就证明任意中文句子被原始证据蕴含。数值与单位关系、遗漏关键信息、因果措辞、局部扩大全局、提示注入等都需要真实评测和专项约束。不得把UI的“已校验”解释为人工诊断正确率保证。

同一份structured_business_facts包含多个统计/事件，可支持多个不同Claim；相同scope+refs并不必然重复。完全相同聚合命题仍拒绝，旧line-evidence去重保留。

## 4. 真实接线和落盘

`OpenClawRuntimeFactory.coordinator()` 注入 `report_composer()`；`InvestigationCoordinator.finish_fresh_finalization()` 在合法Claims生成后调用；`RuntimeAudit.persist_report_result()`保存：

```text
report.json / report-meta.json       成功
report-error.json                   表达失败
result.json: report / report_meta    页面读取
answer.json / final.txt             确定性fallback与审计
```

报告失败不把原本合法的业务调查标记为算法失败；保留明确降级状态和fallback。Finalizer失败则不能跳过Claims校验直接发布报告。历史任务不会因为升级自动重新调用模型或被改写。

## 5. 用户事实不是Evidence列表

主报告显示Composer生成的人话；C/E引用在原始依据/技术详情中保留。源码、Skill、目录定位、工具错误和scratch过程不应变成用户主事实。

working_derived只作为内部派生材料兼容Step6，不因为“存在于Evidence目录”就和原始观察等价。事实页面要人工验收，包括fallback情况下是否仍泄漏大JSON、内部编号或不相干信息。

## 6. 业务校验重点

图片：数值指标只筛选，最终看原图；抽样未覆盖不能说整小时正常；可见雾化不等于确定凝露。

编码器：数值下降、毛刺候选、连续回退、采样缺口与硬件根因分开；事件个数、负增量个数、脉冲幅度不是同一单位。正向大增量还要检查dt，恢复腿不要双计异常。

乳头：最终采用帧2D框，最多4，命名牛周期分母；缺最终结果不是观察到0；统计率不是人工标注精确率。

系统：当前host快照不是历史资源；负载高不是业务根因；缺字段不可从Sandbox补值。

## 7. 验收

测试验证引用越界、fact/inference分类、无工具请求、错误JSON与降级。真实任务另外验证：直接回答问题、事实人话、数字单位一致、未知保留、限制清楚、原始证据可追溯。不能只检查report.json存在就宣布完成。
