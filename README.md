# ScopeX

ScopeX 是运行在 NVIDIA DGX Spark 上的本地、可交互、证据可追溯的工业诊断 Agent Runtime。

OpenClaw 负责模型驱动的 Agent Loop、工具和 Skill；ScopeX 负责 Task/Session、Progress、Stop/Resume/Steering、权限、Evidence、预算/运行时边界、结构化 Claims、可信校验、可追溯输出和产品 API。

> **OpenClaw owns execution. ScopeX owns product control and trust.**

## 当前状态

POC01–POC06 已冻结为回归基线。Step 6 已完成复杂任务能力验证：

| 阶段 | 能力 | 结果 |
|---|---|---|
| Runtime MVP Smoke | 正式 Runtime + OpenClaw + vLLM + Evidence + Finalizer + Audit | **PASS** |
| 6A | Context / Compaction / structured state retention | **PASS** |
| 6B | Large Data / Multi-Image bounded working set + task scratch | **PASS** |
| 6C | Hard Budget single source + trustworthy partial finalization | **PASS** |
| 6D | OpenClaw native loop convergence + runtime-control Evidence filtering | **PASS** |
| 6E | 120k telemetry + 15k logs + 48 images + constrained recovery + post-action verification | **CAPABILITY PASS** |
| 6F | Complex-task usability / latency tuning | **IN PROGRESS（实验分支，不在 main）** |
| Local Runtime API | Task/control/events/evidence/result | FastAPI 已落地，待完整 Spark HTTP 联调 |
| Web UI | Task/Progress/Evidence/Result/Controls | Vue 3 MVP 已落地，待产品化联调 |

6E 已证明本地 `qwen3.8-27b-nvfp4` + OpenClaw 不仅能做简单问答：它能够自主完成大数据筛选、多源交叉验证、多图视觉确认、受约束动作执行和动作后的真实业务状态验证。

当前主要未通过项是**复杂任务产品可用性**：已通过的 6E 综合任务约 1008.5 s / 23 次模型请求，超过当前产品默认 600 s / 16 requests。离线 profile 显示约 90% wall time 在模型请求，主要瓶颈是本地 decode/output 成本与通用 Sandbox 工具缺口，而不是 32K Context hard wall。

详细结论和下一步计划见：

- [OpenClaw / ScopeX Boundary](docs/architecture/06-openclaw-scopex-boundary.md)
- [Complex Task Validation and Next Plan](docs/architecture/07-complex-task-validation-and-next-plan.md)

## 产品技术栈

```text
Vue 3 + TypeScript + Vite
        ↓ HTTP / polling（后续 SSE）
FastAPI + Uvicorn
        ↓
TaskService / ScopeX Runtime
        ↓
OpenClaw + local vLLM
```

当前不引入 Pinia、axios、Redis、数据库、WebSocket、Kubernetes 或工作流引擎。前端使用原生 `fetch` 和 Vue 状态；FastAPI 只做产品传输层，不重新实现 Agent Runtime。

## 当前架构

```text
Goal / Trigger
    ↓
OpenClaw + Local Model
    ↓
autonomous investigation / tool use / action / verification
    ↓ trace
ScopeX Task Runtime
    ├─ Stop / Resume / Steering
    ├─ hard runtime boundary / audit
    ├─ Evidence Projection
    └─ Fresh Finalizer / Claim Validator
    ↓
Product Result / API / UI
```

可信输出链：

```text
Raw Tool Result / Original Image
        ↓
Evidence Snapshot (E1..En)
        ↓
Fresh Structured Finalizer
        ↓
Validated Claims
        ↓
Deterministic Renderer
        ↓
future constrained Answer Composer
```

Evidence 只证明结论，不承担第二套 Agent Loop，也不是产品主界面本身。

## 正式代码结构

```text
scopex/
├── api/                    # FastAPI transport + TaskService + production factory
├── runtime/                # Task/Session/Control/Convergence/Steering
├── agent/                  # OpenClaw, proxy, sandbox, process/environment boundary
├── events/                 # observable Progress events
├── evidence/               # trace -> claim-grade Evidence projection
├── finalizer/              # structured claims / validator / renderer
└── storage/                # filesystem audit

frontend/                   # Vue 3 + TypeScript + Vite
scripts/                    # validation/profiling/runbook scripts; not Agent control logic
```

正式 `scopex/` 代码不得依赖 `scripts/poc*.py`，也不得把完整业务调查流程写死到 Handler。

## 开发 / 回归

```bash
cd /home/yanlan/workspaces/code/scopex
git pull --ff-only
python3 -m unittest discover -s tests -v
```

产品入口：

```text
scripts/runtime_api.py
```

默认监听：

```text
http://127.0.0.1:8787
```

主要接口：

```text
POST /tasks
GET  /tasks
GET  /tasks/{id}
POST /tasks/{id}/stop
POST /tasks/{id}/resume
POST /tasks/{id}/steer
GET  /tasks/{id}/events?after=<seq>
GET  /tasks/{id}/evidence
GET  /tasks/{id}/result
```

## v0.1 产品边界

- 单用户；同一时间一个主要任务；
- PAUSED 任务保留同一 OpenClaw session；
- 原始外部数据默认只读，任务中间产物写 `/task-scratch`；
- 模型可以自主使用现有只读/受控能力调查和执行；
- 高风险设备/机器人/配置动作必须通过明确 capability / permission boundary；
- `exit code 0` 不是恢复成功，必须验证真实业务状态；
- 不做多 Agent、多任务并发、工作流编辑器和重型基础设施；
- 不在 ScopeX 重做 OpenClaw 已拥有的 Agent Loop、Tool Loop、Skill/Process/File/Image 能力。

## 下一阶段

1. **6F Complex Task Usability**：用同一 6E Gate 验证轻量分析 Sandbox 和 concise handoff 是否能进入 600 s / 16 requests；
2. 若仍超预算，做 vLLM decode throughput / speculative decoding 的控制变量实验；
3. 复杂任务可用性达标后进入 **Step 7 Answer Composer + Result-first UI**；
4. 完成真实 Spark FastAPI + Vue 产品联调，再根据需要把 polling 替换为 SSE。

详细验收条件、顺序和已知非阻塞项统一维护在 `docs/architecture/07-complex-task-validation-and-next-plan.md`。
