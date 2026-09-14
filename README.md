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
| Step 7A | Claim-bounded Product Answer over revalidated Claims/Evidence | **已实现，待本轮完整回归** |
| Step 7B | Result-first Vue UI | **已实现，待本轮完整回归** |
| Step 7C | Spark FastAPI + Vue real integration | **进行中** |
| Step 7D | 真实业务产品验收 | **待完成** |

6E/6F 已证明本地 `qwen3.8-27b-nvfp4` + OpenClaw 可以自主完成大数据筛选、多源交叉验证、多图视觉确认、受约束动作执行和动作后的真实状态验证；6F 在不降低任务要求的情况下达到约 `371.1 s / 14 requests`，进入当前产品默认 `600 s / 16 requests` Gate。

### 真实业务任务最新暴露的问题

Step 7 进入真实数据后，机制 Probe 没暴露出的两个产品级问题已经出现并进入修复：

1. **预算耗尽后的 Fresh Finalizer 可能因 JSON 长度截断导致整项任务 FAILED**：现在对 Claim 数量/Evidence refs 做有界约束，并只对 `finish_reason=length` 做一次无工具、同 Evidence 的长度恢复重试；
2. **单图/明确范围任务可能过度扩张调查**：现在 Runtime 增加通用“尊重用户范围 + 最短充分证据 + 证据够即停止”的任务契约，并默认加载内置 Skill。

这些修复属于当前实现基线，但在新的全量测试、镜像构建和 Spark 真实复测完成前，不标记为 PASS。

## 当前文档入口

- [Product Requirements](docs/01-requirements.md) — 当前需求基线；
- [Delivery & Acceptance](docs/02-delivery-and-acceptance.md) — 已验证/已实现/待验收状态；
- [OpenClaw / ScopeX Boundary](docs/architecture/06-openclaw-scopex-boundary.md) — 架构所有权边界；
- [Complex Task Validation and Next Plan](docs/architecture/07-complex-task-validation-and-next-plan.md) — 当前实施路线；
- [Local Usage & Handoff](docs/08-local-usage-and-handoff.md) — 日常启动、调试与接手；
- [Zero-to-One Build & Offline Deployment](docs/09-zero-to-one-build-and-offline-deployment.md) — 新机器构建、清华源、离线镜像/依赖包、systemd 和回滚。

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

## Agent 能力与 Skill

Runtime 默认内置：

```text
cow-disinfect-diagnosis
image-quality-diagnosis
```

ScopeX 启动时把内置 Skill 同步到 `<workspace>/skills` 后交给 OpenClaw allowlist。Skill 提供业务语义、证据纪律、停止原则和稳定脚本入口；模型仍自主决定具体工具与调查顺序。

通用运行原则：

- 用户明确指定的 target/source/scope 是任务约束；
- 只走完成问题所需的最短充分证据路径；
- 不因为目录里“还有别的数据”就自动扩张调查；
- 证据已足够回答时停止工具调用；
- 需要扩张范围时必须是为了回答用户原问题，而不是为了“把所有可能性都查一遍”。

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

## 离线部署

端侧网络不作为 ScopeX 正常运行前提。当前提供：

```text
scripts/export_offline_bundle.sh
scripts/install_offline_bundle.sh
deploy/systemd/scopex-runtime.service
deploy/systemd/runtime.env.example
```

离线 bundle 包含：

```text
固定 commit 的 ScopeX source
预构建 frontend/dist
ARM64/Python 对应 wheelhouse
scopex-sandbox-analysis Docker image
manifest + SHA256SUMS
```

OpenClaw、vLLM 和模型权重当前作为设备基础环境独立管理，不跟 ScopeX 小版本更新包绑定。完整流程见 `docs/09-zero-to-one-build-and-offline-deployment.md`。

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
scripts/                    # product entry + validation/profiling/offline packaging
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

Step 6 已冻结。当前不再扩展机制 Probe，剩余主线是把 Step 7 做成真实产品验收：

1. 当前 Python 全量单测通过；
2. Vue `npm run build` 通过；
3. `scopex-sandbox-analysis:step7` 在 Spark ARM64 构建并验证 toolbox；
4. FastAPI + Vue 真实联调；
5. 单图明确范围任务在少量请求内完成，且不访问用户明确排除的数据；
6. 一个真实业务复杂任务完成 Result-first 产品闭环；
7. Stop / Resume / Steering、刷新/重连和 Evidence 展开可用；
8. 生成一次真实 ARM64 offline bundle 并在离线目录完成安装 smoke。

在这些 Gate 有实施证据前，文档保持“已实现/待验收”，不因为代码已经合入就自动写成 PASS。
