# Complex Task Validation and Next Plan

状态：**2026-09-14 当前有效**。

## 1. Frozen boundary

> **OpenClaw owns execution. ScopeX owns product control and trust.**

不增加第二套 Workflow / Decision / Action Engine。

## 2. Step 6 — frozen

| Step | Result |
|---|---|
| 6A Context / Compaction | **PASS** |
| 6B Large Data / Multi-Image | **PASS** |
| 6C Hard Budget | **PASS** |
| 6D Native Loop Convergence | **PASS** |
| 6E Complex Task Capability | **CAPABILITY PASS** |
| 6F 600s / 16-request Product Gate | **PASS** |

既定综合任务约 `371.1 s / 14 requests`。该结论只证明冻结基线。

## 3. 当前主阶段 — Business + Product V1

```text
Unified Manual Run / Schedule
        ↓
TaskService
        ↓
OpenClaw + local model
        ↓
Data Catalog + Business Skills
        ↓
Trace / Working Data / Internal Evidence / Claim-grade Evidence
        ↓
Fresh Structured Finalizer
        ↓
Validated Claims
        ↓
Constrained Report Composer（无工具）
        ↓
Report Validator
        ↓
结论 / 事实依据 / 可能性分析 / 下一步 / 数据限制
```

普通问答仍走同一 OpenClaw Runtime；不进入业务数据调查时允许 conversation result，不强制 Evidence。

### 3.1 产品表达边界调整

旧主路径 `Claims -> Python label/if-else ProductAnswer` 只保留为 deterministic fallback，不再作为长期主产品表达层。

新原则：

> **模型负责把已经验证的语义写成人话；代码负责 Claims/Evidence 引用和认识论边界校验。**

Report Composer：

- 一次无工具模型请求；
- 不能继续调查；
- 不能新增 Evidence；
- 不能新增事实、数字、时间、根因；
- `facts` 只允许 `fact + observed`；
- inference / temporal / causal / unknown 必须保持不确定性语义；
- 引用不存在的 Claim/Evidence 或越权引用会被 Validator 拒绝；
- Composer 失败不使业务 Task 失败，退回 deterministic answer。

详细设计：`docs/architecture/08-trusted-report-composer.md`。

当前代码已实现、待本轮验收：

- 用户单一 `/runs` 入口；
- auto → conversation/task 内部分类，不调用 Router Model；
- 业务调查失败不能降级为普通对话；
- current system-health；
- image-quality 多图视觉流程；
- nipple 2D KPI；
- encoder event-oriented window analysis；
- bounded log-context；
- Data Catalog + data-locator；
- Catalog Summary 全局注入 Runtime message；
- Evidence Trace/Working/Internal/Claim 分层；
- Validated Claims -> Constrained Report Composer；
- interval/daily/once；
- offline missed skip/no replay；
- run timing；
- 月历/day list；
- terminal task safe delete；
- feedback + review ZIP；
- report-first UI + raw Evidence audit drill-down。

## 4. Real data baseline

```text
/opt/ScalingRobotics/CowDisinfect/Log
  -> /agent-data/logs
  CowDisinfect-YYYYMMDD-HHMMSS.log[.N]

/opt/ScalingRobotics/CowDisinfect/GrabbedImages/LeftCamera
  -> /agent-data/left-camera
  YYYYMMDD/HH/YYYYMMDD-HHMMSSmmm.jpg|json|pcd
```

统一定义在 `config/data-catalog.json`。Runtime：

1. 复制 host/operator Catalog；
2. 若 `data-locator` 启用，将机器可读副本复制到 `/workspace/skills/data-locator/references/data-catalog.json` 对应 workspace 目录；
3. 自动挂载存在的 host path；
4. 将有界语义摘要注入每个 OpenClaw turn。

摘要只包含 source/path/layout/access limits，不包含实际文件清单，也不是业务 Evidence。

### Log selection

日志文件组起始时间可能不是自然小时。Locator 依据：

```text
[group_start, next_group_start)
```

和请求窗口是否重叠决定是否选择该组及其 `.1/.2/...`。

### Multimodal selection

LeftCamera 直接进入目标 `YYYYMMDD/HH`，再按 filename timestamp 过滤，不递归历史树。

## 5. Evidence architecture

真实编码器失败包曾包含约 990 Evidence，主要来自 Skill.md、脚本源码和多个大 stdout。当前分层：

```text
Trace
  Skill / script / locator / shell / model request

Working Data
  /task-scratch

Internal Evidence
  working_derived
  有界 scratch 派生材料，可供 Finalizer / 审计兼容
  不直接进入用户报告

Claim-grade Evidence
  business source / original image / host fact / structured business_facts

Validated Claims
  Fresh Finalizer 后的可信语义层

User Report
  Report Composer 基于 Validated Claims 写成的人话
```

`business_facts` 一次稳定分析只形成一条结构化 Evidence，不再按 stdout 每行拆分。

### Visual Evidence additional gate

- claim-grade `view_image` 每次最多 2 张原图；
- Tool 结果出现 `omitted/truncated` 时，该调用**零提升**为 image Evidence；
- 需要重新以更小批次直接查看原图；
- scratch contact sheet 不能替代最终原图证据。

## 6. Business Skill corrections from real tasks

### 6.1 Image quality

Laplacian / brightness / contrast / clipping 等只能用于多图筛选和分区，不能作为“无起雾/无脏污”的决定依据。

正式时间窗路径：

```text
requested window
 -> data-locator
 -> bounded representative set
 -> optional metrics for partitioning/reference only
 -> originals sampled across time/partitions
 -> repeated view_image <=2 originals per call
 -> cross-image visual judgement
```

视觉判断关注 fog/veil、water droplets、fixed-coordinate contamination、defocus、motion blur 等。负结论“无起雾”需要多个时间/场景的直接视觉覆盖。

### 6.2 Encoder health

目标从“统计所有 negative delta”改为“识别真正相对附近数值异常的事件”。

正式路径：

```text
requested window
 -> data-locator
 -> explicit rotated log list
 -> encoder_health.py once
 -> compact business_facts + top_candidates
 -> bounded log-context only when cause/impact is requested
```

主要事件：

- sampling gap；
- isolated significant negative + catch-up -> reverse-glitch candidate；
- consecutive negative increments -> reverse interval；
- significant single backstep without recovery -> reverse step；
- positive spike relative to local normal increments and real dt；
- flat interval candidate。

优先 application `EncoderVal` 作为业务累计值；raw/filtered 用于判断低层异常是否被滤波或传播。小幅负增量不逐项当异常。

### 6.3 Nipple recognition

```text
window
 -> locator
 -> named cow cycles from logs
 -> LastImgTimeStamp
 -> matching final 2D NippleNum
 -> cap 4/cow
 -> KPI
```

总牛数来自日志牛周期，不来自 JPG/JSON 文件数；3D validity 不参与识别率。JPG/JSON 仅作为辅助核对。

## 7. Scheduler offline semantics

ScopeX 启动时 reconciliation：

- overdue interval/daily trigger 只增加 `missed_count`；
- next_run 推到下一个未来时点；
- once 过期 → `MISSED_OFFLINE` + disabled；
- 不创建历史补跑 Task。

在线单槽位 busy 当前仍 `SKIPPED_BUSY`。不要在本轮同时引入 queue/concurrency。

## 8. Product history / delete

首页：单一 Agent 输入 + 月历。

```text
month calendar
 -> select day
 -> that day's runs
 -> run detail
```

terminal Run 可删除 ScopeX audit/work/review export；原始 `/agent-data` 永不删除。

## 9. Remaining acceptance order

### Gate 1 — focused backend

```bash
python3 -m unittest \
  tests.test_report_composer \
  tests.test_business_skill_tools \
  tests.test_image_evidence_qualification \
  tests.test_evidence_projector \
  tests.test_data_catalog \
  tests.test_structured_finalizer \
  tests.test_product_answer \
  tests.test_runtime_api_factory \
  tests.test_runtime_message_contract \
  tests.test_schedules \
  tests.test_conversation_mode \
  tests.test_conversation_continuation \
  tests.test_conversation_api \
  tests.test_task_calendar_delete \
  tests.test_task_feedback_export \
  tests.test_fastapi_app \
  tests.test_skill_provisioning -v
```

`tests.test_product_answer_readability` 仅用于 deterministic fallback 回归，不再定义主产品文案。

### Gate 2 — full backend

```bash
python3 -m unittest discover -s tests -v
```

### Gate 3 — frontend

```bash
cd frontend
npm run build
```

### Gate 4 — ARM64 sandbox

Build/import `scopex-sandbox-analysis:step7`。

### Gate 5 — real data wiring

- Runtime startup automatically binds real Log / LeftCamera sources；
- `.local/workspace/skills/data-locator/references/data-catalog.json` exists；
- first-turn Runtime message contains bounded Catalog Summary；
- Locator query around non-round log start returns correct file group；
- LeftCamera query only visits target hour dir。

### Gate 6 — unified entry

- “当前有哪些能力” → normal answer, internal conversation；
- “检查12点编码器” → audited task；
- business attempt without facts cannot downgrade to chat。

### Gate 7 — encoder real acceptance

Repeat a one-hour encoder task：

- near `locator -> one encoder_health -> optional small log-context`；
- no script-source inspection / unrelated image listing / inline Python preflight failures；
- output answers real glitch/backstep/spike events rather than negative-step totals；
- report gives concrete event time, before/current count, signed delta, recovery/local context when supported；
- raw/filtered correlation used only when relevant；
- manual spot-check agrees with raw lines。

### Gate 8 — nipple KPI

Full one-hour manual reconciliation of named cow cycles / final `LastImgTimeStamp` / 2D `NippleNum` / KPI；Report displays readable KPI/distribution/quality facts。

### Gate 9 — image

- strict single-image regression；
- bounded time-window image lookup；
- representative multi-image direct visual inspection；
- metrics never decide “no fog”；
- omitted visual batch creates no image Evidence；
- obvious field-labeled fog samples must be recognized as visible fog/haze features before this capability passes。

### Gate 10 — Report Composer

- `result.report` / `report.json` present；
- facts only observed Claims；
- invalid C/E reference rejected；
- Composer parse/transport failure falls back without failing Task；
- UI displays report facts, not raw Evidence Catalog；
- `result.report_meta` records Composer outcome for review。

### Gate 11 — scheduler/history/delete

- offline missed not replayed；
- run-now cadence stable；
- calendar/day aggregation correct；
- terminal deletion removes only ScopeX-owned data。

### Gate 12 — product integration

Timing / feedback / review ZIP / Stop-Resume-Steer / refresh。

### Gate 13 — offline

Device Base + ScopeX Update install, self-recovery, rollback。

### Gate 14 — concurrency discussion

Only after the above data/output layer is stable, move from single active slot to bounded concurrency. Initial direction remains max ~2 running tasks, schedule queue/coalesce semantics and future high-risk device resource locks. Do not implement unlimited parallelism.

## 10. Non-blocking gaps

- log root locator currently does non-recursive filename scanning; add lightweight index only if real file count proves this becomes slow；
- representative image sampling strategy may need further tuning from real hourly sets；
- PCD workload may need capability-specific Sandbox resource profile after real measurements；
- completely missed cows need external ground truth；
- encoder firmware/site calibration/profile is not frozen；
- network topology unknown；
- current model repo/revision not recorded；
- frontend npm lockfile missing；
- bounded concurrency not yet implemented。

## 11. Merge policy

PR #14 remains Draft until focused/full Python, Vue build and minimum Spark real-data evidence pass. `docs/08-local-usage-and-handoff.md` is updated only after this major stage is actually accepted.
