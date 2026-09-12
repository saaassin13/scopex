# ScopeX

ScopeX 是运行在 NVIDIA DGX Spark 上的本地、可交互、证据可追溯的工业诊断 Agent Runtime。

当前阶段已经从 POC 探索进入 **Runtime MVP 工程化**。OpenClaw 负责模型驱动的 Agent Loop、工具和 Skill；ScopeX 负责 Task/Session、Progress、Stop/Resume/Steering、权限、收敛、Evidence、结构化 Claims、校验和可追溯输出。

## 当前结论

已验证能力：

| 阶段 | 能力 | 结果 |
|---|---|---|
| POC02 | OpenClaw 原生 wire/sandbox/security 基线 | PASS |
| POC03 | Skill 驱动自主调查 + Fresh Finalizer | PASS |
| POC04 | 中途 Steering + 用户纠正早期假设 | PASS |
| POC05 | Progress + 确定性 Stop + Resume + Re-steer | PASS |
| POC06 | Evidence-Calibrated Structured Output | PASS |

POC 现在是冻结的回归基线，不再作为生产代码继续堆功能。详见 [POC Baselines](docs/poc/README.md)。

## Runtime MVP 架构

```text
User / Web UI
    |
    v
Task + Session
    |
    v
ScopeX Task Controller -------- Progress Events
    |
    v
OpenClaw Investigation Agent
    |
    v
Tool Gateway / Sandbox
    |
    v
Evidence Catalog (E1..En)
    |
    +---- Convergence Guard
    |
    v
Fresh Structured Finalizer
    |
    v
Generic Claim Validator
    |
    v
Deterministic Renderer
    |
    v
Final Result + Audit
```

核心原则：

> **模型负责理解、调查和判断；Runtime 负责控制、权限、证据身份、收敛和输出强度。**

详细设计见 [Runtime MVP Architecture](docs/architecture/01-runtime-mvp.md)。

## 正式代码结构

```text
scopex/
├── runtime/
│   ├── task.py            # Task 状态机
│   ├── session.py         # 用户/控制历史
│   ├── controller.py      # Stop/Resume/Steer/Finalize
│   ├── convergence.py     # 通用收敛/预算
│   └── permissions.py     # 副作用权限策略
├── agent/
│   ├── base.py            # Agent Adapter 边界
│   └── openclaw.py        # OpenClaw session-key 命令构造
├── events/
│   └── progress.py        # Runtime Progress Event
├── evidence/
│   └── catalog.py         # Runtime-owned E1...En
├── finalizer/
│   ├── claims.py          # fact/inference/unknown 结构
│   ├── validator.py       # 通用证据强度校验
│   └── renderer.py        # 确定性渲染
└── storage/
    └── audit.py           # 本地任务审计
```

`scripts/poc*.py` 保留用于历史验证和回归，正式 `scopex/` 代码禁止依赖它们。

## 当前开发入口

Spark 上更新代码后先跑全部单测：

```bash
cd /home/yanlan/workspaces/code/scopex
git pull --ff-only
python3 -m unittest discover -s tests -v
```

Runtime MVP 核心模块可以单独验证：

```bash
python3 -m unittest tests/test_runtime_mvp_core.py -v
```

当前正式开发顺序：

1. **Core domain/control** — 已开始；
2. **OpenClaw execution adapter** — 下一步，从 POC02/03 只抽通用执行/recorder/sandbox 能力；
3. **Investigation coordinator** — 串联 Controller、Agent、Evidence、Convergence；
4. **Structured finalization service** — Fresh Finalizer + Validator + Renderer；
5. **Local API / Web UI** — Task、Chat、Progress、Stop/Resume、Evidence、Result。

## v0.1 产品边界

首版：单用户、同一时间一个主要任务、本地运行。

默认允许自动执行只读查询、日志分析和临时 workspace 脚本；真实配置修改、服务重启、删除和设备/机器人控制必须用户确认。

首版不做多 Agent、多任务并发、Kubernetes、工作流编辑器，也不把完整业务调查路径写死到 Handler 中。

## 数据与安全

仓库公开。`.local/`、`runs/`、真实日志、图片、密钥、模型权重、生产配置和现场数据不得提交到 Git。

端侧运行继续以 OpenClaw 原生 sandbox/security 验证结果为基线；ScopeX 在其外层增加任务控制、权限、证据和审计，不重复造通用 Agent 工具。
