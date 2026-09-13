# ScopeX 本地使用与接手手册

状态：**2026-09-13 当前有效**。这份文档面向两类场景：

1. 在 Spark 上快速启动/使用当前 ScopeX；
2. 新会话或新开发者快速理解项目并继续 Step 7。

## 1. 先看当前结论

当前已冻结：

```text
6A Context / Compaction            PASS
6B Large Data / Multi-Image       PASS
6C Hard Budget                    PASS
6D Native Loop Convergence        PASS
6E Complex Task Capability        PASS
6F 600s / 16-request Product Gate PASS
```

最新综合任务：120k telemetry + 15k+ logs + 48 images + constrained recovery + post-action verification，在同一 `qwen3.8-27b-nvfp4` 上约 `371.1 s / 14 requests` 完成。

当前主阶段：**Step 7 — Product Answer + Result-first UI**。

不要重新讨论或实现第二套 Agent Loop。固定边界：

> **OpenClaw owns execution. ScopeX owns product control and trust.**

## 2. 代码与关键文档入口

仓库：

```text
/home/yanlan/workspaces/code/scopex
```

新接手者按这个顺序读：

1. `README.md` — 当前状态和产品入口；
2. `docs/01-requirements.md` — 当前需求基线；
3. `docs/architecture/06-openclaw-scopex-boundary.md` — 最重要的架构边界；
4. `docs/architecture/07-complex-task-validation-and-next-plan.md` — 已验证结果和下一步；
5. 本文件 — 本地运行和接手说明。

其他 `docs/poc/` 与 `docs/03-poc-roadmap.md` 主要用于历史追溯，不应覆盖上述当前文档。

## 3. 当前运行依赖

### Spark 主机

当前基线：

```text
Linux: NVIDIA DGX Spark
Agent Runtime: OpenClaw
Model: qwen3.8-27b-nvfp4
Inference: vLLM OpenAI-compatible API
Product API: FastAPI + Uvicorn
UI: Vue 3 + TypeScript + Vite
```

OpenClaw CLI 默认路径：

```text
~/.openclaw/bin/openclaw
```

确认：

```bash
~/.openclaw/bin/openclaw --version
```

### Python API 依赖

```bash
cd /home/yanlan/workspaces/code/scopex
python3 -m pip install -r requirements-api.txt
```

### vLLM

当前常用示例：

```text
http://127.0.0.1:18002/v1
model id: qwen3.8-27b-nvfp4
```

先确认真实服务，不要只依赖配置文件：

```bash
curl -s http://127.0.0.1:18002/v1/models
```

若 vLLM 需要 API key：

```bash
export SCOPEX_API_KEY='<local-vllm-key>'
```

## 4. Analysis Sandbox

复杂任务当前推荐使用已经验证过的轻量 analysis sandbox：

```text
scopex-sandbox-analysis:step6f
```

确认本机已有：

```bash
docker image inspect scopex-sandbox-analysis:step6f >/dev/null
```

如果需要重新构建，先给已经验证过的 OpenClaw sandbox base image 一个本地 tag，然后：

```bash
docker build \
  -f docker/sandbox-analysis.Dockerfile \
  --build-arg BASE_IMAGE=scopex-sandbox-base:step6f \
  -t scopex-sandbox-analysis:step6f \
  .
```

这个镜像只增加当前真实缺口需要的 Pillow。**Docker build 可以联网；Agent 运行时 Sandbox 仍保持 `network=none`。**

验证：

```bash
docker run --rm \
  --network none \
  --entrypoint python3 \
  scopex-sandbox-analysis:step6f \
  -c 'from PIL import Image; print(Image.__version__)'
```

## 5. 启动 ScopeX Runtime API

先同步主分支：

```bash
cd /home/yanlan/workspaces/code/scopex
git checkout main
git pull --ff-only
```

准备一个独立 OpenClaw workspace：

```bash
mkdir -p .local/workspace
```

假设待分析数据在 `/path/to/business-data`，启动：

```bash
python3 scripts/runtime_api.py \
  --model qwen3.8-27b-nvfp4 \
  --base-url http://127.0.0.1:18002/v1 \
  --workspace .local/workspace \
  --sandbox-image scopex-sandbox-analysis:step6f \
  --data-dir /path/to/business-data:/agent-data \
  --enable-view-image
```

默认：

```text
API: http://127.0.0.1:8787
turn timeout: 600 s
model requests / turn: 16
compaction: enabled
exec host: sandbox
exec mode: full
```

说明：

- `--data-dir HOST_DIR:AGENT_DIR` 可重复传入；host 数据会以只读方式挂载；
- 每个 Task 自动有 `/task-scratch` 可写临时目录；
- 需要检查 Spark 主机本身时，显式使用 `--exec-host gateway`，不要默认把业务任务切到 host exec；
- 图片任务需要 `--enable-view-image`；
- `--disable-compaction` 只用于回归/调试；
- `--skill` 可重复指定 Skill。

## 6. 最简单的本地使用方式

### 健康检查

```bash
curl -s http://127.0.0.1:8787/health
```

### 创建任务

```bash
curl -s \
  -X POST http://127.0.0.1:8787/tasks \
  -H 'Content-Type: application/json' \
  -d '{"message":"诊断 /agent-data 中当前异常，给出结论和证据；如果提供了允许的恢复能力，只在证据满足条件时执行，并在动作后重新验证真实状态。"}'
```

返回中记录 `task_id`。

### 查看任务

```bash
curl -s http://127.0.0.1:8787/tasks/<task_id>
```

### 查看进展

```bash
curl -s 'http://127.0.0.1:8787/tasks/<task_id>/events?after=0'
```

响应有 `next_after`；继续轮询时把它作为下一次 `after`。

### 查看 Evidence

```bash
curl -s http://127.0.0.1:8787/tasks/<task_id>/evidence
```

### 查看最终结果

```bash
curl -s http://127.0.0.1:8787/tasks/<task_id>/result
```

只有 Task 到 `COMPLETED` 后，最终结果才视为正式产品结果。

## 7. Stop / Resume / Steering

暂停：

```bash
curl -s \
  -X POST http://127.0.0.1:8787/tasks/<task_id>/stop \
  -H 'Content-Type: application/json' \
  -d '{"message":"先暂停"}'
```

继续：

```bash
curl -s \
  -X POST http://127.0.0.1:8787/tasks/<task_id>/resume \
  -H 'Content-Type: application/json' \
  -d '{"message":"继续调查"}'
```

不中断任务状态、只纠正方向：

```bash
curl -s \
  -X POST http://127.0.0.1:8787/tasks/<task_id>/steer \
  -H 'Content-Type: application/json' \
  -d '{"message":"先检查 system 侧，不要重复已经验证过的图片"}'
```

Stop/Steer 在安全 model-request boundary 生效，不等同于强杀已产生副作用的动作。

## 8. Audit 在哪里

默认 Runtime API 数据根：

```text
.local/runtime-api/
```

主要包括：

```text
.local/runtime-api/tasks/<task-id>/
.local/runtime-api/work/<task-id>/
```

Task audit 可能包含：

```text
task.json
session.json
events.jsonl
evidence.json
claims.json
result.json
final.txt
```

OpenClaw turn/wire audit 在 task work root 下。诊断 Runtime 问题时优先看真实 Trace / Tool Result / runtime-limit / runtime-guard 文件，不从最终自然语言反推执行过程。

## 9. 当前 Web UI 怎么用

Vue MVP 已存在，但 Step 7 正在做 Result-first 产品化，所以当前**API 是最稳的验证入口**。

如需构建现有 UI：

```bash
cd /home/yanlan/workspaces/code/scopex/frontend
npm install
npm run build
```

要求 Node：

```text
>= 22.18.0
```

构建成功后会产生 `frontend/dist/`。重新启动 `scripts/runtime_api.py`，FastAPI 会自动在 `/` 提供静态 Vue 页面。

不要把当前 UI 外观当成冻结产品设计；Step 7 会改成 Result-first。

## 10. 常见判断规则

- **模型说恢复成功 ≠ 恢复成功**：必须看独立 Tool Result / 真实状态；
- **exit code 0 ≠ 业务成功**；
- **Prompt 变长 ≠ 一定是当前性能瓶颈**：6F 已证明该综合任务主要浪费来自多轮 output/decode 与工具缺口；
- **不要把原始 CSV/日志整份输出给模型**：应在 Sandbox 中先筛选/计算；
- **不要在任务运行时装包**：Sandbox 无网络，依赖应在镜像 build 阶段准备；
- **不要把 runtime warning 当 Evidence**；
- **不要因为一次异常就新增 ScopeX workflow/handler**，先判断是否是 OpenClaw 已有能力、通用工具缺失或模型行为问题。

## 11. 当前剩余工作

当前唯一主线是 Step 7：

```text
Validated Claims
    ↓
Constrained Answer Composer
    ↓
Result-first API/UI
    ↓
Spark FastAPI + Vue real integration
    ↓
real business product acceptance
```

目标主视图：

```text
诊断结果

结论
<最重要结果>

说明
- <关键依据/原因>
- <关键依据/原因>

执行情况
<是否执行动作，动作后是否真实验证成功>

建议
<下一步>

相关证据 >
```

vLLM speculative decoding、SSE、Memory Search 等都不是当前主线，除非真实产品验收重新暴露明确需求。

## 12. 新会话快速接手模板

新开 ChatGPT 会话后，可以直接发下面这段：

```text
请接手 ScopeX / 端侧 Agent 项目。

仓库：/home/yanlan/workspaces/code/scopex
GitHub：saaassin13/scopex
当前以 main 为唯一稳定基线。

请先阅读 main 分支以下文件，再开始给方案或改代码：
1. README.md
2. docs/01-requirements.md
3. docs/architecture/06-openclaw-scopex-boundary.md
4. docs/architecture/07-complex-task-validation-and-next-plan.md
5. docs/08-local-usage-and-handoff.md

必须保持架构边界：
OpenClaw + 模型负责自主调查、决策、执行、验证和停止；ScopeX 只提供能力、权限、产品生命周期、Evidence、审计和可信输出，不重新实现 Agent Loop / Workflow Engine。

Step 6A-6F 已全部 PASS，不要无证据重做。
当前主任务是 Step 7：
- constrained Answer Composer over Validated Claims
- Result-first UI（结论 / 说明 / 执行情况 / 建议 / 可展开 Evidence）
- Spark 上 FastAPI + Vue 真正联调
- 最后用真实业务任务做产品验收

开始工作前先检查 main 最新提交和现有代码，给出你理解的当前状态、Step 7 最小实施方案和验收标准；不要先写业务专用 Workflow。
```

这段模板的目的不是替代代码/文档，而是强制新会话先读取当前事实，避免依赖旧聊天上下文。
