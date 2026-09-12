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
| Local Runtime API | Task/control/events/evidence/result loopback API | **已实现，待 Spark 验证** |

核心原则：

> **模型负责理解、调查和判断；Runtime 负责控制、权限、证据身份、收敛和输出强度。**

## 当前架构

```text
Web UI / Local Client
        ↓
Local Runtime API
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
├── api/                    # Local HTTP API + TaskService + production factory
├── runtime/                # Task/Session/Control/Convergence/Steering
├── agent/                  # OpenClaw, proxy, sandbox, process/environment boundary
├── events/                 # observable Progress events
├── evidence/               # extraction + runtime-owned provenance
├── finalizer/              # structured claims / validator / renderer
└── storage/                # filesystem audit
```

`scripts/poc*.py` 只用于历史验证/回归；正式 `scopex/` 代码禁止依赖 POC runner/grader。

## 开发入口

更新并跑全量回归：

```bash
cd /home/yanlan/workspaces/code/scopex
git pull --ff-only
python3 -m unittest discover -s tests -v
```

Local API 新增测试：

```bash
python3 -m unittest \
  tests.test_runtime_api_service \
  tests.test_runtime_api_http \
  -v
```

产品 API 入口：

```text
scripts/runtime_api.py
```

默认只监听：

```text
http://127.0.0.1:8787
```

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

具体启动和 curl 验证见 [Local Runtime API](docs/architecture/04-local-runtime-api.md)。

## v0.1 产品边界

- 单用户；
- 同一时间一个主要任务；
- PAUSED 任务仍占用任务槽位并保留同一 OpenClaw session；
- 默认自主执行只读查询、日志分析和临时 workspace 脚本；
- 配置修改、服务重启、删除和设备/机器人控制需要用户确认；
- 本地运行、loopback API；
- 不做多 Agent、多任务并发、Kubernetes、工作流编辑器和重型基础设施；
- 不把完整业务调查流程写死到 Handler。

下一阶段：Local Runtime API 通过真实 HTTP + OpenClaw 验证后，开始最小 Web UI。
