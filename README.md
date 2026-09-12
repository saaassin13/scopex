# ScopeX

ScopeX 是运行在 NVIDIA DGX Spark 上的本地、可交互、证据可追溯的工业诊断 Agent Runtime。

OpenClaw 负责模型驱动的 Agent Loop、工具和 Skill；ScopeX 负责 Task/Session、Progress、Stop/Resume/Steering、权限、收敛、Evidence、结构化 Claims、校验、可追溯输出和产品 API。

## 当前状态

POC01–POC06 已冻结为回归基线，正式生产代码只放在 `scopex/`。

| 阶段 | 能力 | 结果 |
|---|---|---|
| POC02 | OpenClaw 原生 wire/sandbox/security | PASS |
| POC03 | 自主调查 + Fresh Finalizer | PASS |
| POC04 | Mid-turn Steering + 用户纠正 | PASS |
| POC05 | Progress + Stop + Resume + Re-steer | PASS |
| POC06 | Evidence-Calibrated Structured Output | PASS |
| Runtime MVP Smoke | 正式 Runtime + OpenClaw + vLLM + Evidence + Finalizer + Audit | **PASS** |
| Runtime Refinalize | 逐行 Evidence + 去重 Claims + concise Renderer | **PASS** |
| Local Runtime API | Task/control/events/evidence/result | **FastAPI 已落地，待 Spark 真实 HTTP 验证** |
| Web UI | Task/Progress/Evidence/Result/Controls | **Vue 3 MVP 已落地，待构建联调** |

核心原则：

> **模型负责理解、调查和判断；Runtime 负责控制、权限、证据身份、收敛和输出强度。**

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

当前不引入 Pinia、axios、Redis、数据库、WebSocket 或 Nginx。前端使用原生 `fetch` 和 Vue composable/组件状态；FastAPI 仅替换 HTTP 传输层，不重新实现 Runtime 逻辑。

## 当前架构

```text
Vue Web UI / Local Client
        ↓
FastAPI Local Runtime API
        ↓
TaskService / TaskController
        ↓
OpenClaw Investigation Agent
        ↓
Tool Gateway / Docker Sandbox
        ↓
Evidence Extraction
        ↓
Evidence Catalog (E1..En, source:line)
        ↓
Fresh Structured Finalizer
        ↓
Generic Claim Validator
        ↓
Deterministic Renderer
        ↓
AuditStore + Final Result
```

详细设计：

- [Runtime MVP Architecture](docs/architecture/01-runtime-mvp.md)
- [POC → Runtime Migration](docs/architecture/02-poc-to-runtime-migration.md)
- [Runtime MVP Status](docs/architecture/03-runtime-mvp-implementation-status.md)
- [Local Runtime API](docs/architecture/04-local-runtime-api.md)
- [Frozen POC Baselines](docs/poc/README.md)

## 正式代码结构

```text
scopex/
├── api/                    # FastAPI transport + TaskService + production factory
├── runtime/                # Task/Session/Control/Convergence/Steering
├── agent/                  # OpenClaw, proxy, sandbox, process/environment boundary
├── events/                 # observable Progress events
├── evidence/               # extraction + runtime-owned provenance
├── finalizer/              # structured claims / validator / renderer
└── storage/                # filesystem audit

frontend/                   # Vue 3 + TypeScript + Vite
```

`scripts/poc*.py` 只用于历史验证/回归；正式 `scopex/` 代码禁止依赖 POC runner/grader。

## 安装产品层依赖

Python API：

```bash
cd /home/yanlan/workspaces/code/scopex
python3 -m pip install -r requirements-api.txt
```

前端（当前依赖 Vite/官方 Vue 工具链，建议 Node 22.18+）：

```bash
cd /home/yanlan/workspaces/code/scopex/frontend
npm install
```

## 开发入口

更新并跑回归：

```bash
cd /home/yanlan/workspaces/code/scopex
git pull --ff-only
python3 -m unittest discover -s tests -v
```

FastAPI 新增测试：

```bash
python3 -m unittest \
  tests.test_runtime_api_service \
  tests.test_fastapi_app \
  -v
```

前端类型检查和构建：

```bash
cd frontend
npm run build
```

产品入口：

```text
scripts/runtime_api.py
```

默认只监听：

```text
http://127.0.0.1:8787
```

如果 `frontend/dist/` 已存在，FastAPI 会同时在 `/` 提供 Vue 静态页面；否则以 API-only 模式启动。

接口：

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

FastAPI 自动文档：

```text
http://127.0.0.1:8787/docs
```

## v0.1 产品边界

- 单用户；
- 同一时间一个主要任务；
- PAUSED 任务仍占用任务槽位并保留同一 OpenClaw session；
- 默认自主执行只读查询、日志分析和临时 workspace 脚本；
- 配置修改、服务重启、删除和设备/机器人控制需要用户确认；
- 本地运行、loopback API；
- UI 当前使用 1.5–2.5 秒 polling，后续可替换为 SSE；
- 不做多 Agent、多任务并发、Kubernetes、工作流编辑器和重型基础设施；
- 不把完整业务调查流程写死到 Handler。

下一阶段：先完成 FastAPI + Vue 在 Spark 上的真实联调，再进入 UI 细节和 SSE。
