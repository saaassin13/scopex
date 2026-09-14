# Complex Task Validation and Next Plan

状态：**2026-09-14 当前有效**。

## 1. Frozen architecture boundary

> **OpenClaw owns execution. ScopeX owns product control and trust.**

OpenClaw + 本地模型负责调查、工具选择、执行、验证和停止；ScopeX 提供任务范围、能力、权限、生命周期、Evidence、预算、审计、时间触发和可信产品结果。不增加第二套 Workflow / Decision / Action Engine。

## 2. Step 6 — frozen

| Step | Result |
|---|---|
| 6A Context / Compaction | **PASS** |
| 6B Large Data / Multi-Image | **PASS** |
| 6C Hard Budget | **PASS** |
| 6D Native Loop Convergence | **PASS** |
| 6E Complex Task Capability | **CAPABILITY PASS** |
| 6F 600s / 16-request Product Gate | **PASS** |

6F 既定综合任务约 `371.1 s / 14 requests`。这些结论只证明冻结基线，不自动覆盖新业务代码。

## 3. Step 7 / Product V1 当前实现

```text
Conversation / Manual Task / Scheduled Trigger
        ↓
TaskService
        ↓
OpenClaw + local model
        ↓
Skills / tools
        ↓
Progress / Audit
        ↓
Conversation result
或
Evidence -> Finalizer -> Claims -> Product Answer
```

当前代码已实现、尚待回归：

- Claim-bounded Product Answer；
- deterministic business readability；
- Result-first UI；
- Conversation 与 Task 共用 Runtime；
- Conversation 正常结束时允许无 Evidence；
- started/finished/duration/trigger metadata；
- interval/daily/once 简单 Schedule；
- busy 时 `SKIPPED_BUSY`；
- run-now 不改变 recurring cadence；
- Task 评价；
- review ZIP export；
- FastAPI/Vue 对应页面/API。

Scheduler 仅是时间 Trigger，不决定 Skill 或业务步骤。

## 4. 真实产品问题与对应修复

### 4.1 Finalizer length truncation

已有 Evidence 时结构化输出可能 `finish_reason=length`。当前：Claims 数量/Evidence refs 有界；仅 length 时同 Evidence 一次无工具恢复。

### 4.2 Explicit scope over-expansion

用户明确 target/source/scope 是约束。走最短充分路径，只有原范围不足时才最小扩展，证据够即停止。

### 4.3 普通问答被诊断流程误判失败

旧行为：无 Evidence → `investigation_completed_without_evidence`。

当前：Conversation 与 Task 仍共用 TaskService/OpenClaw，但 Conversation 的正常 final answer 可直接发布；正式 Task 仍要求 Evidence。

### 4.4 Product Answer 太机械

当前增加受 Claims 约束的确定性字段格式化：百分比、单位、常见业务字段翻译。原始 Evidence/技术错误下沉详情；不调用第二次诊断模型。

## 5. Business V1

默认 Skills：

```text
system-health
image-quality-diagnosis
nipple-recognition-analysis
encoder-health
log-context
```

### system-health

只做**当前 host state**：

```text
Task create
 -> host snapshot
 -> <task-work>/host/current.json
 -> read-only /scopex-host/current.json
 -> Agent
```

已取消：30 秒 timer、`system_metrics.jsonl`、历史 CPU/memory/disk/GPU 分析。

Agent 不获得 host shell；Snapshot 不可用时必须 unknown，禁止 Sandbox fallback。

### image-quality

原图优先，必要时稳定 metrics；严格范围、证据够停止。

### nipple-recognition-analysis

正式口径：

```text
rotated CowDisinfect logs
 -> named cow cycles
 -> per-frame 2D NippleNum
 -> New cow detecte finished / LastImgTimeStamp
 -> final consumed 2D NippleNum
 -> KPI
```

- 一头牛固定 4 个乳头；
- 只统计 2D boxes，不使用 3D valid；
- 不取 max，不求和；
- JPG/JSON 失败路径可能不存在，因此不做 denominator；
- `>4` 单列过检、KPI cap=4；
- unfinished cycle 保留在保守分母。

### encoder-health

V1 只分析 invalid/read failure、sample gap、negative jump、large negative candidate、positive delta outlier candidate、flat raw、raw/filtered diff。旧业务阈值不直接继承。

### log-context

公共 bounded raw log window，不独立判根因。

## 6. Deployment baseline

部署分层：

```text
Device Base Package
  DGX OS / Docker / NVIDIA Runtime / OpenClaw / vLLM / model

ScopeX Update Bundle
  source / frontend dist / wheelhouse / analysis sandbox / Skills / checksum
```

当前 served id：`qwen3.8-27b-nvfp4`；仍需补录真实 `MODEL_REPO + MODEL_REVISION`。完整流程见 `docs/09-zero-to-one-build-and-offline-deployment.md`。

## 7. Remaining acceptance order

### Gate 1 — Focused backend

```bash
python3 -m unittest \
  tests.test_product_answer \
  tests.test_product_answer_readability \
  tests.test_business_skill_tools \
  tests.test_schedules \
  tests.test_task_run_metadata \
  tests.test_fastapi_app \
  tests.test_skill_provisioning \
  tests.test_deployment_assets -v
```

### Gate 2 — Full backend

```bash
python3 -m unittest discover -s tests -v
```

### Gate 3 — Frontend

```bash
cd frontend
npm run build
```

### Gate 4 — ARM64 Sandbox

构建 `scopex-sandbox-analysis:step7`，验证 toolbox imports + manifest。

### Gate 5 — Conversation

输入“当前可用的 Skill 有哪些”，应正常完成；可以没有 Evidence；不得进入 `investigation_completed_without_evidence`。

### Gate 6 — Current system-health

- `/scopex-host/current.json` 存在且采样时间接近 Task；
- 当前磁盘/内存/CPU/GPU 能正确读取；
- 某字段 collector 失败时显示 unavailable；
- Trace 不用 Sandbox `/proc/free/df/nvidia-smi` 代替 host。

### Gate 7 — nipple 2D KPI

用完整一小时轮转日志人工核对：牛周期、`LastImgTimeStamp`、最终 `NippleNum`、分布、`Σmin(N,4)/(total×4)`；有完整 artifact 时只做辅助核对。

### Gate 8 — encoder

用正常 + 已知毛刺/回退/读取失败样本核对 candidate events，再小窗口 log-context 解释。

### Gate 9 — image scope

明确单图：实际查看原图、不读排除数据、不默认 exec、少量请求结束。

### Gate 10 — Schedule / timing / feedback / export

- interval/daily/once；
- busy → `SKIPPED_BUSY`；
- run-now 不改 next_run；
- started/finished/duration；
- 评价保存/修改；
- review ZIP 可供大模型复盘。

### Gate 11 — Product integration

FastAPI + Vue 跑 Conversation、手动 Task、Scheduled Task 至少各一个；检查 Result/Evidence/refresh/Stop/Resume/Steer。

### Gate 12 — Offline

Device Base + ScopeX Update 双层 ARM64 离线安装、自恢复、rollback。

## 8. Non-blocking gaps

- 完全漏检且无命名牛周期的物理牛仍缺 ground truth；
- encoder firmware/site profile 未冻结；
- 网络 topology 未确认；
- current model repo/revision 未补录；
- generic high-risk business action provenance 仍需后续扩展；
- frontend npm lockfile 缺失；
- task scratch retention/cleanup 仍需真实验证；
- review bundle 环境版本清单可继续增强。

## 9. Merge policy

PR #14 保持 Draft。完成 focused/full Python、Vue build 和最小 Spark 真实验收前不合入 main；不要在这一批再混入网络 topology、机器人新动作或模型性能实验。handoff 文档在本大阶段验收完成后再更新。
