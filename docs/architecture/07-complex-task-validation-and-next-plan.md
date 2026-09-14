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
Trace / Working Data / Claim-grade Evidence
        ↓
conversation answer
或
Finalizer -> Claims -> Product Answer
```

当前代码已实现、待本轮验收：

- 用户单一 `/runs` 入口；
- auto → conversation/task 内部分类，不调用 Router Model；
- 业务调查失败不能降级为普通对话；
- current system-health；
- image-quality；
- nipple 2D KPI；
- encoder compact window analysis；
- bounded log-context；
- Data Catalog + data-locator；
- Evidence Trace/Working/Claim/User Facts 分层；
- interval/daily/once；
- offline missed skip/no replay；
- run timing；
- 月历/day list；
- terminal task safe delete；
- feedback + review ZIP；
- Result-first / User Facts UI。

## 4. Real data baseline

```text
/opt/ScalingRobotics/CowDisinfect/Log
  -> /agent-data/logs
  CowDisinfect-YYYYMMDD-HHMMSS.log[.N]

/opt/ScalingRobotics/CowDisinfect/GrabbedImages/LeftCamera
  -> /agent-data/left-camera
  YYYYMMDD/HH/YYYYMMDD-HHMMSSmmm.jpg|json|pcd
```

统一定义在 `config/data-catalog.json`，Runtime 复制到 workspace 并自动挂载存在的 host path。

### Log selection

日志文件组起始时间可能不是自然小时。Locator 依据：

```text
[group_start, next_group_start)
```

和请求窗口是否重叠决定是否选择该组及其 `.1/.2/...`。

### Multimodal selection

LeftCamera 直接进入目标 `YYYYMMDD/HH`，再按 filename timestamp 过滤，不递归历史树。

## 5. Evidence correction from real failure

真实编码器失败包曾包含约 990 Evidence，主要来自 Skill.md、脚本源码和多个 15KB 分析 stdout。该设计已纠正：

```text
Trace
  Skill / script / locator / shell / model request

Working Data
  /task-scratch

Claim-grade Evidence
  business source / original image / host fact / structured business_facts

User Facts
  UI-visible factual basis
```

`business_facts` 一次稳定分析只形成一条结构化 Evidence，不再按 stdout 每行拆分。

## 6. Encoder failure correction

正式路径：

```text
requested window
 -> data-locator
 -> explicit rotated log list
 -> encoder_health.py once
 -> compact business_facts
 -> bounded context only for significant candidates
```

所有小 negative delta 只统计次数/P95/max。显著 negative outlier、large negative、positive outlier、gap、flat 才进入 candidate list。

此外 `investigation-error.json` 现在保留 OpenClaw CLI flags/liveness/error 信息，方便 review bundle 继续定位 `investigation_turn_incomplete`。

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
  tests.test_data_catalog \
  tests.test_business_skill_tools \
  tests.test_evidence_projector \
  tests.test_product_answer_readability \
  tests.test_schedules \
  tests.test_conversation_mode \
  tests.test_conversation_continuation \
  tests.test_conversation_api \
  tests.test_task_calendar_delete \
  tests.test_task_feedback_export \
  tests.test_fastapi_app \
  tests.test_runtime_api_factory \
  tests.test_skill_provisioning -v
```

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

Build/import `scopex-sandbox-analysis:step7`.

### Gate 5 — real data wiring

- Runtime startup automatically binds real Log / LeftCamera sources;
- `/workspace/scopex-data-catalog.json` exists;
- Locator query around non-round log start returns correct file group;
- LeftCamera query only visits target hour dir.

### Gate 6 — unified entry

- “当前有哪些能力” → normal answer, internal conversation;
- “检查3点编码器” → audited task;
- business attempt without facts cannot downgrade to chat.

### Gate 7 — encoder real acceptance

Repeat failed task “3点的编码器数据是否存在异常”:

- expected path near `locator -> one encoder_health -> optional small log-context`;
- no script-source inspection;
- no unrelated image listing;
- no `cd && python` preflight errors;
- Evidence count drastically lower than previous 990;
- Product Answer readable;
- manual candidate spot-check agrees with raw lines.

### Gate 8 — nipple KPI

Full one-hour manual reconciliation of named cow cycles / final `LastImgTimeStamp` / 2D `NippleNum` / KPI.

### Gate 9 — image

Strict single-image regression + bounded time-window image lookup.

### Gate 10 — scheduler/history/delete

- offline missed not replayed;
- run-now cadence stable;
- calendar/day aggregation correct;
- terminal deletion removes only ScopeX-owned data.

### Gate 11 — product integration

Timing / User Facts / feedback / review ZIP / Stop-Resume-Steer / refresh.

### Gate 12 — offline

Device Base + ScopeX Update install, self-recovery, rollback.

### Gate 13 — concurrency discussion

Only after Gates 1–11 are stable, move from single active slot to bounded concurrency. Initial direction remains max ~2 running tasks, schedule queue/coalesce semantics and future high-risk device resource locks. Do not implement unlimited parallelism.

## 10. Non-blocking gaps

- log root locator currently does non-recursive filename scanning; add lightweight index only if real file count proves this becomes slow;
- PCD workload may need capability-specific Sandbox resource profile after real measurements;
- completely missed cows need external ground truth;
- encoder firmware/site profile not frozen;
- network topology unknown;
- current model repo/revision not recorded;
- frontend npm lockfile missing;
- bounded concurrency not yet implemented.

## 11. Merge policy

PR #14 remains Draft until focused/full Python, Vue build and minimum Spark real-data evidence pass. `docs/08-local-usage-and-handoff.md` is updated only after this major stage is actually accepted.
