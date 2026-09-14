# ScopeX 可信业务报告 Composer

状态：**2026-09-14 设计冻结，进入实现与 Spark 验收**。

## 1. 为什么调整

此前产品结果主要依赖 deterministic `ProductAnswer` 把 Claims/Evidence 逐项映射成人类可读文本。该方案适合作为可信 fallback，但不适合作为长期主表达层：每增加一个业务字段都需要维护 Python label/if-else，最终会把“通用 Agent”重新写成大量业务展示规则。

新的边界是：

```text
OpenClaw investigation
    ↓
Evidence
    ↓
Fresh Structured Finalizer
    ↓
Validated Claims
    ↓
Constrained Report Composer
    ↓
Report Validator
    ↓
用户报告
```

**OpenClaw 仍拥有调查/执行；Fresh Finalizer 仍拥有 Claims 可信边界；Report Composer 只负责表达，不重新诊断。**

## 2. Report Composer 不是第二个 Agent

Report Composer：

- 只进行一次无工具模型请求；
- 不允许 read/exec/view_image 等工具；
- 不允许新增 Evidence；
- 不允许重新调查；
- 只能使用已经验证的 Claim 与这些 Claim 已引用的 Evidence；
- 失败时不使业务任务失败，退回 deterministic renderer。

因此它不是 Workflow/Agent Loop，而是受约束的产品表达层。

## 3. 固定用户报告结构

第一版统一为：

```text
结论
事实依据
可能性分析
下一步
数据限制（可选）
```

示例：

```text
结论
12:00–13:00 编码器数据存在异常，发现 2 次明显回退恢复毛刺和 1 段连续回退；未发现明显采样缺口。

事实依据
- 12:13:21.120：812345 → 812291，下降 54 pulse，随后约 40 ms 内恢复。
- 12:42:09.xxx：连续 3 个采样下降，共回退 73 pulse。
- 全时段采样中位间隔约 21 ms，没有明显采样缺口。

可能性分析
- 第一类事件符合短时读数毛刺形态。
- 连续回退可能是真实反转、reset 或采集链路异常；当前证据不足以区分。

下一步
- 围绕异常点检查 ±2 秒 raw/filtered、reset 和通信错误日志。
```

## 4. 报告 schema

```json
{
  "version": 1,
  "conclusion": {
    "text": "...",
    "claim_ids": ["C1"],
    "evidence_refs": ["E1"]
  },
  "facts": [
    {"text": "...", "claim_ids": ["C1"], "evidence_refs": ["E1"]}
  ],
  "possibilities": [
    {"text": "...", "claim_ids": ["C2"], "evidence_refs": ["E2"]}
  ],
  "next_steps": [
    {"text": "...", "claim_ids": ["C2"], "evidence_refs": []}
  ],
  "limitations": []
}
```

## 5. Validator 规则

代码负责校验引用和认识论边界，而不是负责写中文：

- 所有 `claim_ids` 必须真实存在；
- `evidence_refs` 必须属于对应 Claim 已验证过的 Evidence；
- `facts` 只能引用 `fact + observed`；
- `possibilities` 只能引用 inference / causal_hypothesis / temporal_association / unknown，不得把 observed fact 改写成原因；
- `next_steps` 必须围绕已存在的 unresolved/inference Claim，不能凭空增加故障事实；
- `limitations` 用于 unknown / 证据边界；
- 文本长度和条目数量有界；
- Composer 结果解析/校验失败时使用 deterministic `answer` fallback。

## 6. Prompt 原则

System prompt 必须强调：

1. 你是业务结果编辑器，不是诊断 Agent；
2. 调查已经结束，没有工具；
3. 不增加新的事实、时间、数字、原因；
4. `observed fact` 才能进入事实依据；
5. inference/temporal 只能写“相关/可能”；
6. causal hypothesis 必须明确待验证；
7. unknown 不得包装成确定结论；
8. 不显示 JSON 字段名、Skill、Python、工具调用或模型工作过程；
9. 优先回答用户问题，再给最重要依据；
10. 输出固定 JSON schema，不输出 Markdown 报告。

## 7. Evidence 与用户事实分层

```text
Trace
  Skill / Tool / Model / Script

Working Data
  /task-scratch

Internal Evidence
  working_derived

Claim-grade Evidence
  原始业务行 / 原图 / host structured fact / business_facts

Validated Claims
  可信语义层

User Facts
  Report Composer 基于 observed Claims 生成的人类可读事实
```

UI 不再把 Evidence Catalog 本身当成“事实依据”。原始 Evidence 仅在折叠的“查看原始依据/技术记录”中提供审计。

## 8. 三个首批业务 Skill 的结果边界

### 图片质量

- Laplacian/亮度/对比度/clip ratio 只用于多图筛选/分区，不可作为“无起雾/无脏污”的决定证据；
- 最终判断必须直接查看代表性原图；
- 每次 `view_image` 保持小批量，若工具返回 omitted/truncated，该调用不得升级为 claim-grade image Evidence；
- 多图综合判断脏污、模糊、起雾/雾化、水珠、运动模糊、失焦；
- “没有起雾”的负结论要求跨不同时间/场景的视觉覆盖。

### 编码器

沿用现场参考分析思路：

- sampling gap；
- signed increment；
- 相对局部正常窗口明显异常的 positive spike；
- isolated negative + near-term catch-up → reverse-glitch candidate；
- consecutive negative increments → reverse interval；
- 小幅负增量不直接视为异常；
- 应用 `EncoderVal` 作为主要业务序列，raw/filtered 用于确认异常是否在底层出现/是否被滤波；
- 数据异常与硬件根因必须分开。

### 乳头识别率

```text
请求时间窗
→ data-locator
→ CowDisinfect logs
→ 建立牛检测周期
→ LastImgTimeStamp 对应最终采用帧
→ 2D NippleNum（每牛最多 4）
→ KPI
```

主指标：总牛数、4/3/2/1/0/无最终结果分布、完整四乳头率、总体乳头识别率。JPG/JSON 只做辅助核对，不作为总牛数分母。

## 9. 验收

1. 新任务 `result.report` 存在且通过引用校验；
2. 用户页面不再依赖 `_LABELS` 才能正常阅读；
3. `facts` 只显示人类可读事实，不展示 raw JSON/Evidence 行；
4. Composer 故意返回不存在的 C/E 时被拒绝并 fallback；
5. Composer transport/JSON 失败时 Task 仍 COMPLETED，deterministic fallback 可用；
6. 图片、编码器、乳头识别率三个真实任务验证报告表达与业务含义。
