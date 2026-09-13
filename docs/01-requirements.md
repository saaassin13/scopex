# ScopeX 产品需求基线

状态：**2026-09-13 当前有效基线**。旧 POC 需求已收口为当前产品要求；后续需求变化应在本文件和架构/路线文档中同步更新。

## 1. 项目目标

在 NVIDIA DGX Spark 上交付一个本地、可交互、证据可追溯的工业诊断 Agent Runtime。

当前固定实现基线：

- Agent Runtime：OpenClaw；
- 本地模型：`qwen3.8-27b-nvfp4`；
- 推理服务：vLLM OpenAI-compatible `/v1`；
- 产品层：ScopeX Runtime + FastAPI + Vue；
- 默认本地运行，业务数据、日志、图片、审计记录不依赖公网服务。

核心边界：

> **OpenClaw owns execution. ScopeX owns product control and trust.**

OpenClaw + 模型负责自主调查、工具选择、执行、动作后验证和停止；ScopeX 负责能力/数据挂载、权限边界、Task 生命周期、Stop/Resume/Steer、预算护栏、Evidence、可信 Finalizer、审计和产品 API/UI。

**不得在 ScopeX 中重新实现第二套 Agent Loop / Workflow Engine / Decision Engine / Action Engine。**

## 2. 必须具备的能力

| ID | 需求 | 当前验收口径 |
|---|---|---|
| R01 | 本地任务链 | OpenClaw、vLLM、文件/Shell/图片、Evidence、Finalizer 均在 Spark 本地完成；运行期 Sandbox 默认无网络 |
| R02 | 自主调查 | 用户给目标后，模型自主决定调查顺序与工具，不要求用户逐步教操作 |
| R03 | 大数据工作集 | 大日志/CSV 不直接灌入 Context；Agent 用 Shell/Python 等做筛选、计算和有界摘要 |
| R04 | 多图分析 | 大图片集可先筛选，最终依赖的只读原图必须实际 `view_image`；最终 claim-grade 原图集合保持有界 |
| R05 | 可执行动作 | 业务动作只能通过明确 capability / permission boundary；动作前验证前置条件 |
| R06 | 动作后验证 | 命令 exit code 0 不等于业务成功；必须再次查询真实业务状态 |
| R07 | 上下文持续 | 长任务使用 OpenClaw 原生 compaction；Context 是工作记忆，不是原始数据存储 |
| R08 | 任务可接管 | 支持 Stop / Resume / Steering，并保留同一 OpenClaw Session 的有效上下文 |
| R09 | 防失控 | Hard request/time budget 单一来源；OpenClaw 原生 loopDetection 防止重复工具循环 |
| R10 | 可信输出 | 用户可见事实必须来自 claim-grade Evidence / 原图重新验证，不允许“模型自己说过的话”反向成为事实依据 |
| R11 | 结果优先 | 产品主界面优先展示结论、说明、执行情况和建议；Evidence 是可展开的支撑材料 |
| R12 | 可审计/可复测 | 每次关键验证保留 Task/Trace/Evidence/Claims/Result/Probe 指标，升级后可重复回归 |

## 3. 数据、文件与 Sandbox 规则

- 外部业务数据以只读 bind 暴露，例如 `/agent-data`；
- 每个 Task 自动获得 host-backed、task-local、可写 `/task-scratch`；
- `/task-scratch` 用于临时脚本、筛选结果、缩略图/接触表、JSON/CSV 中间产物；
- Scratch 派生图片不能自动升级为原始 claim-grade Image Evidence；
- Runtime Sandbox 默认 `network=none`；通用依赖应在镜像 build 阶段准备，而不是任务中在线安装；
- 当前轻量 analysis sandbox 只基于真实缺口增加 Pillow，不预装重型数据栈。

## 4. Evidence / Final Result 要求

可信链固定为：

```text
Raw Tool Result / Original Image
        ↓
Evidence Snapshot
        ↓
Fresh Structured Finalizer
        ↓
Validated Claims
        ↓
Deterministic Renderer
        ↓
Step 7 constrained Answer Composer
```

关键规则：

1. Evidence 是 OpenClaw Trace 的最小 claim-grade 投影，不是第二份 Transcript；
2. `read` / `exec` 的真实输出可以成为 Evidence，OpenClaw runtime-control warning/guard 文本不能成为业务 Evidence；
3. 图片 Evidence 记录原图身份和 SHA；Fresh Finalizer 必须重新解析并校验原图；
4. Finalizer 只能基于已有 Evidence 形成 Claims；
5. Answer Composer 只能改变表达方式，不能新增未经 Claims 支持的事实/因果判断；
6. deterministic renderer 始终保留为 audit/trust fallback。

## 5. 产品运行边界

v0.1 当前范围：

- 单用户；
- 同一时间一个主要 Task；
- PAUSED Task 仍占用任务槽位并保留 Session；
- Runtime API 只绑定 loopback；
- 原始数据默认只读；
- 不做多 Agent 编排、任务队列、Kubernetes、工作流编辑器、独立重型监控平台；
- Memory Search 暂不启用，只有出现真实跨任务长期召回需求时再引入；
- 高风险机器人/设备/配置动作必须另有明确 capability 和权限策略。

## 6. 当前性能与复杂任务 Gate

复杂任务产品默认预算：

```text
OpenClaw turn timeout = 600 s
model requests / turn = 16
```

Step 6F 已用同一综合任务真实通过：

- 120,000 行 telemetry；
- 15,000+ 行日志；
- 48 张原始图片；
- constrained recovery；
- recovery 后独立状态验证；
- `371.1 s / 14 requests`；
- `within_product_default_budget = true`。

这证明当前本地 OpenClaw + Qwen 27B 路径已具备复杂任务能力，并能进入当前定义的产品默认预算；不代表所有未知业务任务都自动满足相同 SLA。

## 7. 完成定义

一个诊断/执行任务只有同时满足以下条件才算正确完成：

- 找到并使用了任务所需的真实数据；
- 结论区分观察事实、推断和未证实项；
- 需要动作时，前置条件经过证据验证；
- 动作只执行允许的次数和范围；
- 动作后有独立真实状态验证；
- 最终结果通过 Fresh Finalizer / Claim Validator；
- 任务没有依赖未记录的云端调用或隐藏业务 Handler；
- 原始只读输入未被修改。

## 8. 当前主需求

Step 6 已冻结。当前产品主需求进入 **Step 7：Product Answer + Result-first UI**：

1. 在 Validated Claims 之上增加 constrained Answer Composer；
2. 产品结果固定为“结论 / 说明 / 执行情况 / 建议 / 相关证据”；
3. Evidence/Finding 降为支撑视图，不抢占主结果；
4. 完成真实 Spark FastAPI + Vue 产品联调；
5. 用真实业务任务从 API/UI 跑完整闭环验收；
6. speculative decoding 等 vLLM 性能优化仅在真实 SLA 再次成为瓶颈时启动。

详细架构边界见 `docs/architecture/06-openclaw-scopex-boundary.md`；实施与剩余工作见 `docs/architecture/07-complex-task-validation-and-next-plan.md`。
