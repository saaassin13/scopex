# ScopeX

ScopeX 是运行在 NVIDIA DGX Spark 上的本地、可交互、证据可追溯的工业诊断 Agent Runtime。

OpenClaw 负责模型驱动的 Agent Loop、工具和 Skill；ScopeX 负责 Task/Session、Progress、Stop/Resume/Steering、权限、Evidence、预算/运行时边界、结构化 Claims、可信校验、产品结果、审计和 API/UI。

> **OpenClaw owns execution. ScopeX owns product control and trust.**

## 当前状态

截至 **2026-09-14**：

| 阶段 | 能力 | 状态 |
|---|---|---|
| Runtime MVP Smoke | ScopeX + OpenClaw + vLLM + Evidence + Finalizer + Audit | **PASS** |
| 6A | Context / Compaction / structured state retention | **PASS** |
| 6B | Large Data / Multi-Image bounded working set + task scratch | **PASS** |
| 6C | Hard Budget + trustworthy partial finalization | **PASS** |
| 6D | OpenClaw native loop convergence + Evidence filtering | **PASS** |
| 6E | 120k telemetry + 15k logs + 48 images + constrained recovery + post-action verification | **CAPABILITY PASS** |
| 6F | Same complex task under product default 600 s / 16 requests | **PASS** |
| Step 7A | Claim-bounded Product Answer over revalidated Claims/Evidence | **已实现，待完整回归** |
| Step 7B | Result-first Vue UI | **已实现，待完整回归** |
| Business V1 | system / image / nipple JSON / encoder / log-context | **已实现第一版，待真实业务验收** |
| Step 7C | Spark FastAPI + Vue real integration | **进行中** |
| Step 7D | 真实业务产品验收 | **进行中** |

6E/6F 已证明本地 `qwen3.8-27b-nvfp4` + OpenClaw 可以自主完成大数据筛选、多源交叉验证、多图视觉确认、受约束动作执行和动作后的真实状态验证；6F 在不降低任务要求的情况下达到约 `371.1 s / 14 requests`，进入当前产品默认 `600 s / 16 requests` Gate。

真实业务阶段当前不再把旧排查脚本直接包装成产品能力。第一批业务能力采用“主数据源上的确定性分析 + 小范围日志上下文 + Agent 综合判断”的边界；网络能力等 topology 明确后再设计。

## 当前文档入口

- [Product Requirements](docs/01-requirements.md) — 当前需求基线；
- [Delivery & Acceptance](docs/02-delivery-and-acceptance.md) — 已验证/已实现/待验收状态；
- [OpenClaw / ScopeX Boundary](docs/architecture/06-openclaw-scopex-boundary.md) — 架构所有权边界；
- [Complex Task Validation and Next Plan](docs/architecture/07-complex-task-validation-and-next-plan.md) — 当前实施路线；
- [Local Usage & Handoff](docs/08-local-usage-and-handoff.md) — 日常启动、调试与接手；
- [Zero-to-One Build & Offline Deployment](docs/09-zero-to-one-build-and-offline-deployment.md) — Docker、vLLM、模型、清华源、离线镜像/依赖包、systemd 和回滚；
- [Business Capabilities V1](docs/business/01-business-capabilities-v1.md) — 第一批业务说明、数据边界和简要设计。

## 产品技术栈

```text
Vue 3 + TypeScript + Vite
        ↓ HTTP / polling
FastAPI + Uvicorn
        ↓
TaskService / ScopeX Runtime
        ↓
OpenClaw + local vLLM
        ↓
read / exec / process / view_image / Skills
```

当前不引入 Redis、数据库、WebSocket、Kubernetes 或工作流引擎。前端使用原生 `fetch`；FastAPI 只做产品传输层，不重新实现 Agent Runtime。

## 当前架构

```text
Goal / Trigger
    ↓
OpenClaw + Local Model
    ↓
autonomous investigation / tool use / action / verification / stop
    ↓ trace
ScopeX Task Runtime
    ├─ Task scope / permission boundary
    ├─ Stop / Resume / Steering
    ├─ hard runtime boundary / audit
    ├─ Evidence Projection
    └─ Fresh Finalizer / Claim Validator
    ↓
Validated Claims
    ↓
Product Answer + deterministic trust fallback
    ↓
FastAPI / Result-first UI
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
Claim-bounded Product Answer
        ├─ 结论
        ├─ 说明
        ├─ 执行情况
        └─ 建议
        ↓
Result-first UI

Deterministic Renderer = audit/trust fallback
```

Evidence 证明结论，不承担第二套 Agent Loop，也不是产品主界面本身。

## 第一批业务能力

Runtime 默认内置：

```text
system-health
image-quality-diagnosis
nipple-recognition-analysis
encoder-health
log-context
```

旧 `cow-disinfect-diagnosis` 仍保留在仓库供历史/专项回归，但不再作为默认业务 Skill。

职责：

```text
system-health
  宿主机 CPU / 内存 / 磁盘 / GPU / Docker / 进程历史

image-quality-diagnosis
  原图模糊 / 起雾 / 脏污

nipple-recognition-analysis
  推理 JSON -> 按牛聚合 -> 四乳头率 / 乳头识别率

encoder-health
  sample gap / 无效值 / raw 回退 / 大跳变候选 / flat

log-context
  只捞小范围原始日志上下文，不独立判根因
```

ScopeX 启动时把内置 Skill 同步到 `<workspace>/skills` 后交给 OpenClaw allowlist。Skill 提供业务语义、证据纪律、停止原则和稳定脚本入口；模型仍自主决定具体工具与调查顺序。

通用运行原则：

- 用户明确指定的 target/source/scope 是任务约束；
- 主数据源先回答核心问题；
- 不因为目录里还有日志/JSON/图片就自动全部扫描；
- 确定性脚本只算事实/候选，不硬编码根因；
- 日志在需要解释时才按小时间窗扩展；
- 证据已足够回答时停止工具调用。

## System Health 数据边界

Agent 默认运行在 Sandbox，不能把 Sandbox 的 `/proc/free/df/nvidia-smi` 当成 Spark 宿主机状态。

V1 使用 user-systemd 每 30 秒在宿主机执行：

```text
scripts/collect_system_metrics.py
    ↓
~/.local/share/scopex/system-metrics/system_metrics.jsonl
    ↓ read-only bind
/scopex-system-metrics/system_metrics.jsonl
```

这样 system-health 可以回答历史时间窗口，又不需要把 Agent 的 `exec_host` 全局切到 gateway。

## Analysis Sandbox

当前推荐镜像：

```text
scopex-sandbox-analysis:step7
```

运行期网络仍是 `none`。镜像 build 阶段预装常用离线分析工具：

```text
numpy / scipy / pandas / cv2 / Pillow / scikit-image
matplotlib / openpyxl / PyYAML / psutil / scikit-learn
Open3D（当前 ARM64 基础发行版存在 apt 包时）
```

实际工具箱写入：

```text
/opt/scopex/toolbox.json
```

Dockerfile 会把 Ubuntu/Debian build-time APT 源切换到清华 TUNA；Ubuntu ARM64 使用 `ubuntu-ports`。这不修改 Spark 宿主机系统源。

## 部署与离线能力

部署现在明确分成：

```text
Device Base Package（低频）
  Docker/NVIDIA Runtime + OpenClaw + vLLM image + model weights

ScopeX Update Bundle（高频）
  source + frontend + Python wheelhouse + analysis sandbox + Skills
```

当前生产 served model id 仍是：

```text
qwen3.8-27b-nvfp4
```

真正从 0 到 1 重建时必须另外记录模型 `MODEL_REPO + MODEL_REVISION`，不能只保存 served id。vLLM image 也必须固定 tag/digest；新模型必须重新跑 ScopeX Agent/图片/业务能力 Gate。

完整流程见 `docs/09-zero-to-one-build-and-offline-deployment.md`。

## 正式代码结构

```text
scopex/
├── api/                    # FastAPI transport + TaskService + production factory
├── runtime/                # Task/Session/Control/Convergence/Steering
├── agent/                  # OpenClaw/proxy/sandbox/skill provisioning/runtime contract
├── events/                 # observable Progress events
├── evidence/               # trace -> claim-grade Evidence projection
├── finalizer/              # Claims/validator/renderer/Product Answer
└── storage/                # filesystem audit

frontend/                   # Vue 3 + TypeScript + Vite
docker/                     # offline analysis sandbox layer
skills/                     # built-in product Skills + stable scripts
deploy/                     # systemd deployment templates
docs/business/              # product business semantics
scripts/                    # product entry + host collectors + validation/offline packaging
```

正式 `scopex/` 代码不得依赖 `scripts/poc*.py`，也不得把完整业务调查流程写死到 Handler。

## 开发 / 回归

```bash
cd /home/yanlan/workspaces/code/scopex
git checkout main
git pull --ff-only
python3 -m unittest discover -s tests -v
```

前端：

```bash
cd frontend
npm install
npm run build
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
- 模型可以自主调查、选择工具和决定调查深度，但必须尊重用户明确范围；
- 高风险设备/机器人/配置动作必须通过明确 capability / permission boundary；
- `exit code 0` 不是恢复成功，必须独立验证真实业务状态；
- 不做多 Agent、多任务编排、工作流编辑器和重型基础设施；
- 不在 ScopeX 重做 OpenClaw 已拥有的 Agent Loop、Tool Loop、Skill/File/Image 能力。

## 下一步 Gate

Step 6 已冻结。当前主线已经进入真实业务验收：

1. 当前 Python 全量单测通过；
2. Vue `npm run build` 通过；
3. `scopex-sandbox-analysis:step7` 在 Spark ARM64 构建并验证 toolbox；
4. system metrics timer 连续产出，system-health 能区分当前/历史宿主机状态；
5. 提供真实乳头推理 JSON，冻结字段 mapping 和 selected/latest/max 业务语义；
6. 用真实编码器日志验证 gap/backstep/flat/candidate 事件与 log-context；
7. 单图明确范围任务在少量请求内完成，且不访问用户明确排除的数据；
8. FastAPI + Vue 跑至少一个真实业务 Task 完成 Result-first 产品闭环；
9. 生成一次真实 ARM64 ScopeX update bundle，并验证 Device Base + ScopeX 双层离线恢复；
10. Stop / Resume / Steering、刷新/重连和 Evidence 展开可用。

在这些 Gate 有实施证据前，文档保持“已实现/待验收”，不因为代码存在就自动写成 PASS。
