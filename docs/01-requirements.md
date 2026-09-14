# ScopeX 产品需求基线

状态：**2026-09-14 当前有效基线**。旧 POC 需求已收口为当前产品要求；后续需求变化应在本文件和架构/路线文档中同步更新。

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

OpenClaw + 模型负责自主调查、工具选择、执行、动作后验证和停止；ScopeX 负责能力/数据挂载、任务范围、权限边界、Task 生命周期、Stop/Resume/Steer、预算护栏、Evidence、可信 Finalizer、审计和产品 API/UI。

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
| R13 | 范围与停止 | 用户明确指定 target/source/scope 时必须视为任务边界；只走最短充分证据路径，证据足够后停止，不因目录中存在其他数据而自动扩张调查 |
| R14 | Skill / 稳定脚本 | 业务知识、证据纪律、常用分析方法通过 Skill/稳定脚本提供；Skill 引导模型但不替代 OpenClaw Agent Loop |
| R15 | 离线部署 | ScopeX source、Web dist、host wheelhouse、analysis sandbox image 可预构建并离线导入；离线包必须记录 commit/架构/镜像 ID 并带 SHA256 |

## 3. 数据、文件与 Sandbox 规则

- 外部业务数据以只读 bind 暴露，例如 `/agent-data`；
- 每个 Task 自动获得 host-backed、task-local、可写 `/task-scratch`；
- `/task-scratch` 用于临时脚本、筛选结果、缩略图/接触表、JSON/CSV 中间产物；
- Scratch 派生图片不能自动升级为原始 claim-grade Image Evidence；
- Runtime Sandbox 默认 `network=none`；通用依赖应在镜像 build 阶段准备，而不是任务中在线安装；
- 当前 analysis sandbox 预装 `numpy/scipy/pandas/cv2/Pillow/scikit-image/matplotlib/openpyxl/PyYAML/psutil/scikit-learn`；Open3D 仅在基础发行版提供 ARM64 apt 包时安装；
- 实际可用工具箱由 `/opt/scopex/toolbox.json` 记录；模型不应为了探测常用包是否存在而反复消耗工具回合；
- 镜像 build 阶段可使用清华 TUNA APT 镜像；这不应修改 Spark 宿主机安全更新策略。

## 4. Task 范围与停止规则

ScopeX 不决定业务调查流程，但必须把产品任务边界明确交给 OpenClaw：

1. 用户明确指定的一张图、一个日志、一个时间窗、一个设备或“只用视觉/不要查日志”等要求属于**范围约束**；
2. 模型应优先选择回答问题所需的最短充分证据路径；
3. 只有当当前范围不足以回答原问题时，才允许扩张调查，并应保持扩张最小；
4. 不允许因为 `/agent-data` 中存在更多文件就默认扫描其他数据；
5. 一旦当前证据已经足够回答，停止工具调用并交付结果；
6. 如果无法确认具体物理原因，允许明确输出 `unknown/待验证`，不能以“继续调查所有可能性”代替不确定性。

这是一条通用 Agent 合约，不是图片/日志专用 Workflow。

## 5. Skill 与脚本规则

当前内置 Skill：

```text
cow-disinfect-diagnosis
image-quality-diagnosis
```

Runtime 启动时把仓库内置 Skill 同步到 `<workspace>/skills`，再交给 OpenClaw allowlist。

Skill 可包含：

- 业务术语；
- 证据边界；
- 推荐分析方法；
- 何时停止；
- 稳定脚本入口。

稳定脚本必须：

- 只做确定性计算/读取/变换，不替模型直接给业务结论；
- 输入范围明确；
- 输出紧凑、可审计；
- 不默认扫描整个数据目录；
- 不自行联网安装依赖。

例如图片质量 Skill 提供的指标脚本只计算 Laplacian variance、gradient、亮度/对比度等客观指标，不直接判断“起雾/镜头脏污一定成立”。

## 6. Evidence / Final Result 要求

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
Claim-bounded Product Answer
        ↓
Result-first UI

Deterministic Renderer = audit/trust fallback
```

关键规则：

1. Evidence 是 OpenClaw Trace 的最小 claim-grade 投影，不是第二份 Transcript；
2. `read` / `exec` 的真实输出可以成为 Evidence，OpenClaw runtime-control warning/guard 文本不能成为业务 Evidence；
3. 图片 Evidence 记录原图身份和 SHA；Fresh Finalizer 必须重新解析并校验原图；
4. Finalizer 只能基于已有 Evidence 形成 Claims；
5. Product Answer 只能基于重新校验通过的 Claims/Evidence 组织结果，不能新增未经支持的事实或因果；
6. deterministic renderer 始终保留为 audit/trust fallback；
7. Finalizer 输出必须有界；仅当 transport 因 `finish_reason=length` 被截断时，允许对同一 Evidence 做一次无工具长度恢复重试，这不是新的调查回合。

## 7. 产品运行边界

v0.1 当前范围：

- 单用户；
- 同一时间一个主要 Task；
- PAUSED Task 仍占用任务槽位并保留 Session；
- Runtime API 只绑定 loopback；
- 原始数据默认只读；
- 不做多 Agent 编排、任务队列、Kubernetes、工作流编辑器、独立重型监控平台；
- Memory Search 暂不启用，只有出现真实跨任务长期召回需求时再引入；
- 高风险机器人/设备/配置动作必须另有明确 capability 和权限策略。

## 8. 离线部署要求

端侧现场网络不能作为 ScopeX 正常运行前提。

### ScopeX 更新包

至少包含：

```text
固定 git commit 的 source archive
预构建 frontend/dist
与目标 ARM64/Python 匹配的 wheelhouse
scopex-sandbox-analysis Docker image
manifest + SHA256SUMS
```

要求：

- 离线包在与目标一致的 CPU 架构上构建；
- 安装前校验架构和 SHA256；
- host Python 使用 `pip --no-index --find-links` 离线安装；
- 现场不执行 `npm install`；
- Docker image 使用 `docker save/load`；
- 旧版本代码目录和镜像 tag 保留，允许快速回滚。

### 独立设备基础环境

当前不跟 ScopeX 小版本 bundle 绑定：

- Docker Engine；
- OpenClaw；
- vLLM；
- 模型权重。

这些大组件更新节奏和体积不同，应由设备基础镜像/独立离线包管理。

## 9. 当前性能与复杂任务 Gate

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

真实业务任务已证明：预算语义正确不等于产品收口一定成功。Finalizer 自身的输出边界、Task 范围和 Skill/toolbox 也必须单独验证。

## 10. 完成定义

一个诊断/执行任务只有同时满足以下条件才算正确完成：

- 找到并使用了任务所需的真实数据；
- 没有无理由越过用户明确指定的 target/source/scope；
- 结论区分观察事实、推断和未证实项；
- 需要动作时，前置条件经过证据验证；
- 动作只执行允许的次数和范围；
- 动作后有独立真实状态验证；
- 最终结果通过 Fresh Finalizer / Claim Validator；
- 产品 Answer 未增加 Claims 不支持的事实；
- 任务没有依赖未记录的云端调用或隐藏业务 Handler；
- 原始只读输入未被修改；
- 证据已足够时 Agent 能停止，而不是持续扩张调查直到预算耗尽。

## 11. 当前主需求

Step 6 已冻结。Step 7 当前实现已经覆盖：

- Claim-bounded Product Answer；
- Result-first UI；
- Finalizer length recovery；
- 通用任务范围/停止契约；
- 内置 Skill provisioning；
- 图片质量 Skill + 稳定指标脚本；
- 常用离线分析 Sandbox；
- 从 0 到 1/离线部署脚本和文档。

当前剩余 Gate：

1. Python 全量单测；
2. Vue build；
3. Spark ARM64 构建/验证 `scopex-sandbox-analysis:step7`；
4. FastAPI + Vue 真联调；
5. 单图明确范围任务验证“不越界 + 少量请求结束”；
6. 一个真实复杂业务任务完成 Result-first 产品验收；
7. Stop / Resume / Steering / refresh-reconnect 产品面验收；
8. 真实导出并导入一次 ARM64 offline bundle。

详细架构边界见 `docs/architecture/06-openclaw-scopex-boundary.md`；实施与剩余工作见 `docs/architecture/07-complex-task-validation-and-next-plan.md`；部署见 `docs/09-zero-to-one-build-and-offline-deployment.md`。
