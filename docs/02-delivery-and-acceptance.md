# 交付、验收与当前状态

状态：**2026-09-13 当前有效版本**。本文件区分“已经实测通过”“当前产品实现”“下一阶段待完成”，避免把计划当成完成项。

## 1. 当前产品形态

```text
Vue 3 Web UI / local client
        ↓
FastAPI loopback API
        ↓
TaskService / ScopeX Runtime
        ↓
OpenClaw + qwen3.8-27b-nvfp4
        ↓
local vLLM
        ↓
read / exec / process / view_image / Skills
        ↓
Evidence -> Fresh Finalizer -> Validated Claims -> Product Result
```

OpenClaw + 模型拥有自主调查/工具/动作/验证循环；ScopeX 不重新实现 Agent Loop，只负责产品控制和可信输出。

## 2. 已交付并验证的核心能力

| 能力 | 当前状态 | 证据/说明 |
|---|---|---|
| Runtime MVP 核心执行链 | **PASS** | Task → OpenClaw → 工具 → Evidence → Fresh Finalizer → Result |
| Stop / Resume / Steering | **PASS** | 同 Session、安全请求边界 |
| Evidence-Calibrated Output | **PASS** | 精确 E refs、Claim Validator、deterministic renderer |
| 6A Context / Compaction | **PASS** | OpenClaw 原生 compaction + structured state retention |
| 6B Large Data / Multi-Image | **PASS** | 120k CSV、48 图、有界 working set、task scratch |
| 6C Hard Budget | **PASS** | request/time budget 单一执行层 + 可信 partial finalization |
| 6D Native Loop Convergence | **PASS** | OpenClaw loopDetection + Evidence runtime-control filtering |
| 6E Complex Task Capability | **PASS** | 日志 + telemetry + 图片 + constrained recovery + post-action verification |
| 6F Product-default Usability Gate | **PASS** | 同任务 `371.1 s / 14 requests`，进入 `600 s / 16 requests` |
| Local Runtime API | **已实现** | FastAPI loopback；完整 Spark 产品联调仍在 Step 7 |
| Vue Web UI | **MVP 已实现** | 当前仍需 Result-first 重构和 Spark 联调 |

## 3. 当前复杂任务验收基线

统一复杂任务产品 Gate：

```text
turn timeout <= 600 s
model requests <= 16
Task state = COMPLETED
Fresh Finalizer valid
runtime_limit = null
runtime_guard = null
```

任务正确性不能只看“模型回答了”。需要同时满足：

- 关键日志/数据/图片真实使用；
- 大数据 working set 有界；
- 原始只读数据未修改；
- 业务动作只执行允许的次数；
- 动作后独立查询真实状态；
- 最终结论受到 Evidence / Validated Claims 约束。

最新已通过综合案例：

```text
120,000 telemetry rows
15,000+ log lines
48 original images
PUMP_OVERLOAD diagnosis
1 constrained recovery
independent status verification
371.1 s
14 forwarded model requests
```

## 4. 当前交付物

仓库当前已经包含：

- `scopex/`：正式 Runtime、API、Evidence、Finalizer、Audit 代码；
- `frontend/`：Vue 3 MVP；
- `docker/sandbox-analysis.Dockerfile`：轻量 analysis sandbox 层；
- `scripts/runtime_api.py`：本地产品服务入口；
- `scripts/step6*.py`：机制/复杂任务回归 Probe；
- `docs/architecture/06-openclaw-scopex-boundary.md`：架构所有权边界；
- `docs/architecture/07-complex-task-validation-and-next-plan.md`：当前实施路线和剩余工作；
- `docs/08-local-usage-and-handoff.md`：本地使用与新会话接手手册。

运行时审计按 Task 保留 Task/Session/Events/Evidence/Claims/Result 等文件，具体位置由 `scripts/runtime_api.py --data-root` 决定。

## 5. 当前未完成项

### Step 7 — Product Answer + Result-first UI

当前主阶段，必须完成：

1. constrained Answer Composer：只允许基于 Validated Claims 组织自然语言；
2. 主结果结构：结论 / 说明 / 执行情况 / 建议；
3. Evidence 作为可展开辅助区，而不是主产品内容；
4. deterministic renderer 保持 audit/trust fallback；
5. FastAPI + Vue 在 Spark 上真实完整联调；
6. 用真实业务任务从 UI/API 完成一次端到端产品验收。

### 后续仅按真实需求触发

- SSE 替换 polling；
- vLLM speculative decoding / decode-throughput 深度优化；
- >4 张同时 claim-grade 原图的 batch Fresh Verification；
- Memory Search / 跨任务长期记忆；
- 更完整的 `/task-scratch` retention/cleanup；
- mixed gateway/sandbox 对共享 scratch 的真实验证。

这些不是当前 Step 7 的前置阻塞项。

## 6. 验收原则

- 不用扩大 timeout/request budget 掩盖可用性问题；
- 不通过降低任务要求提高通过率；
- 不把固定业务流程写成 Runtime Handler；
- 不把模型先前生成的自然语言当作原始 Evidence；
- 不把 exit code 0 当作业务动作成功；
- 不用单次成功宣称所有未知任务可靠；
- 先保留真实失败，再做控制变量修复和复测。

## 7. 当前产品阶段结论

截至 2026-09-13：

> **复杂任务的能力和当前产品默认预算 Gate 均已通过。当前主要工作已经从“证明 Agent 能不能做复杂任务”切换到“把可信结果做成真正好用的产品体验”。**

下一步以 `docs/architecture/07-complex-task-validation-and-next-plan.md` 为主路线，日常启动与接手流程见 `docs/08-local-usage-and-handoff.md`。
