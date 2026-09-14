# ScopeX 本地使用与接手手册

状态：**2026-09-14 当前有效**。

用于 Spark 本地启动、真实 Runtime 调试、新会话接手，以及区分“已验证”和“已实现待验收”。

## 1. 当前阶段

冻结基线：

```text
6A Context / Compaction            PASS
6B Large Data / Multi-Image       PASS
6C Hard Budget                    PASS
6D Native Loop Convergence        PASS
6E Complex Task Capability        CAPABILITY PASS
6F 600s / 16-request Product Gate PASS
```

当前产品/业务阶段：

```text
7A Product Answer                 IMPLEMENTED / acceptance pending
7B Result-first UI                IMPLEMENTED / acceptance pending
7C Spark FastAPI + Vue            IN PROGRESS
Business V1 Skills                IMPLEMENTED / real-data acceptance pending
Offline deployment baseline       IMPLEMENTED / smoke pending
```

固定边界：

> **OpenClaw owns execution. ScopeX owns product control and trust.**

不要实现第二套 Agent Loop / Workflow Engine。

## 2. 接手阅读顺序

1. `README.md`
2. `docs/01-requirements.md`
3. `docs/02-delivery-and-acceptance.md`
4. `docs/architecture/06-openclaw-scopex-boundary.md`
5. `docs/architecture/07-complex-task-validation-and-next-plan.md`
6. `docs/business/01-business-capabilities-v1.md`
7. 本文件
8. `docs/09-zero-to-one-build-and-offline-deployment.md`

`docs/poc/` 和历史脚本仅作追溯，不覆盖当前业务定义。

## 3. 当前运行基线

```text
Device: NVIDIA DGX Spark / ARM64
Agent Runtime: OpenClaw
Served model id: qwen3.8-27b-nvfp4
Inference: local vLLM OpenAI-compatible API
Product API: FastAPI + Uvicorn
UI: Vue 3 + TypeScript + Vite
Sandbox: scopex-sandbox-analysis:step7
```

确认：

```bash
~/.openclaw/bin/openclaw --version
curl -s http://127.0.0.1:18002/v1/models
```

模型真正从 0 到 1 重建还必须记录 `MODEL_REPO + MODEL_REVISION`；served id 本身不是下载地址。详细见 `docs/09-zero-to-one-build-and-offline-deployment.md`。

## 4. 第一批业务 Skill

默认：

```text
system-health
image-quality-diagnosis
nipple-recognition-analysis
encoder-health
log-context
```

旧 `cow-disinfect-diagnosis` 保留供历史/专项回归，但不再默认加载。

ScopeX 启动时把内置 Skill 同步到：

```text
<workspace>/skills/
```

检查：

```bash
find .local/workspace/skills -maxdepth 4 -type f -print
```

业务设计见：

```text
docs/business/01-business-capabilities-v1.md
```

### 能力边界

- `system-health`：Spark 宿主机资源历史；
- `image-quality-diagnosis`：原始图片质量；
- `nipple-recognition-analysis`：推理 JSON 按牛聚合 KPI；
- `encoder-health`：编码器采样健康事实/候选；
- `log-context`：公共小窗口日志证据，不独立给根因。

网络能力暂缓，等待 topology 明确。

## 5. 通用任务契约

- 用户明确 target/source/scope 是约束；
- 主数据源先回答核心问题；
- 走最短充分证据路径；
- 不因为 `/agent-data` 有其他文件就全部扫描；
- 只有需要解释时才扩展到小范围日志上下文；
- 证据够即停止；
- 无法确认根因时允许 `unknown/待验证`。

## 6. System Health 宿主机数据

Agent 默认运行在 Sandbox，不能用 Sandbox 自己的 `/proc/free/df/nvidia-smi` 表示 Spark host。

V1 使用 user-systemd timer 每 30 秒采样：

```text
scripts/collect_system_metrics.py
 -> ~/.local/share/scopex/system-metrics/system_metrics.jsonl
 -> read-only /scopex-system-metrics/system_metrics.jsonl
 -> system-health Skill
```

安装：

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/scopex-system-metrics.service ~/.config/systemd/user/
cp deploy/systemd/scopex-system-metrics.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now scopex-system-metrics.timer
```

检查：

```bash
systemctl --user status scopex-system-metrics.timer
tail -n 2 ~/.local/share/scopex/system-metrics/system_metrics.jsonl
```

## 7. Host Python / Web

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install \
  -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple \
  -r requirements-api.txt
```

前端：

```bash
cd frontend
npm install
npm run build
cd ..
```

离线现场直接使用预构建 `frontend/dist`。

## 8. Analysis Sandbox

```bash
docker build \
  -f docker/sandbox-analysis.Dockerfile \
  --build-arg BASE_IMAGE=scopex-sandbox-base:step6f \
  -t scopex-sandbox-analysis:step7 \
  .
```

验证：

```bash
docker run --rm --network none --entrypoint python3 \
  scopex-sandbox-analysis:step7 \
  -c 'import cv2, PIL, numpy, pandas, scipy, skimage, matplotlib, openpyxl, yaml, psutil, sklearn; print("OK")'

docker run --rm --network none --entrypoint cat \
  scopex-sandbox-analysis:step7 /opt/scopex/toolbox.json
```

APT build-time 使用清华 TUNA；运行时网络仍是 `none`。

## 9. 启动 Runtime API

```bash
mkdir -p .local/workspace ~/.local/share/scopex/system-metrics

.venv/bin/python scripts/runtime_api.py \
  --model qwen3.8-27b-nvfp4 \
  --base-url http://127.0.0.1:18002/v1 \
  --workspace .local/workspace \
  --sandbox-image scopex-sandbox-analysis:step7 \
  --data-dir /path/to/business-data:/agent-data \
  --system-metrics-dir "$HOME/.local/share/scopex/system-metrics" \
  --enable-view-image
```

默认：

```text
API: 127.0.0.1:8787
turn timeout: 600s
model requests/turn: 16
exec host: sandbox
sandbox network: none
business data: read-only
system metrics: read-only
```

启动日志应看到：

```text
skills: system-health, image-quality-diagnosis, nipple-recognition-analysis, encoder-health, log-context
```

## 10. API 快速使用

```bash
curl -s http://127.0.0.1:8787/health
```

创建任务：

```bash
curl -s -X POST http://127.0.0.1:8787/tasks \
  -H 'Content-Type: application/json' \
  -d '{"message":"分析 7 点到 8 点的乳头识别率。"}'
```

其他：

```text
GET  /tasks/<id>
GET  /tasks/<id>/events?after=0
GET  /tasks/<id>/evidence
GET  /tasks/<id>/result
POST /tasks/<id>/stop
POST /tasks/<id>/resume
POST /tasks/<id>/steer
```

## 11. Result / Audit

```text
Evidence
 -> Fresh Finalizer
 -> Validated Claims
 -> Product Answer
 -> Result-first UI
```

Task audit：

```text
.local/runtime-api/tasks/<task-id>/
```

包括 `task/session/events/evidence/claims/answer/result/final` 等文件；`final.txt` 是 deterministic trust fallback。

## 12. 第一批真实业务验收

### system-health

- timer 连续产出 JSONL；
- 当前与历史时间窗口可区分；
- Agent 不使用 Sandbox 资源冒充 host；
- 资源压力与业务异常只有时间重合时才建立关联。

### image-quality

- 指定原图实际 `view_image`；
- 不访问用户排除的数据；
- 单图任务不默认跑 exec；
- 证据够后停止。

### nipple-recognition-analysis

需要真实推理 JSON 冻结：

```text
time field
cow key
nipple field
selected/latest/max 真实业务语义
```

逐牛结果和小时统计必须可人工复算；`>4` 不允许提高识别率到 100% 以上。

### encoder-health

用真实样本验证：

```text
invalid/read failure
sampling gap
negative jump
large negative candidate
positive delta outlier candidate
flat raw candidate
```

需要解释时才调用 `log-context`。

## 13. 后端/前端回归

```bash
python3 -m unittest discover -s tests -v
```

```bash
cd frontend
npm run build
```

业务工具专项：

```bash
python3 -m unittest tests.test_business_skill_tools tests.test_skill_provisioning tests.test_deployment_assets -v
```

## 14. Stop / Resume / Steering

示例纠正方向：

```bash
curl -s -X POST http://127.0.0.1:8787/tasks/<id>/steer \
  -H 'Content-Type: application/json' \
  -d '{"message":"只分析编码器原始值和对应 ±5 秒日志，不要检查图片。"}'
```

Stop/Steer 在安全 model-request boundary 生效。

## 15. 部署 / 离线

完整文档：

```text
docs/09-zero-to-one-build-and-offline-deployment.md
```

现在明确拆分：

```text
Device Base Package
  Docker/NVIDIA Runtime + OpenClaw + vLLM + model

ScopeX Update Bundle
  source + frontend + wheels + analysis sandbox + Skills
```

ScopeX 更新包：

```bash
bash scripts/export_offline_bundle.sh
```

模型/vLLM 不随 ScopeX 小版本重复传输。

## 16. systemd

模板：

```text
deploy/systemd/scopex-runtime.service
deploy/systemd/runtime.env.example
deploy/systemd/scopex-system-metrics.service
deploy/systemd/scopex-system-metrics.timer
```

ScopeX Runtime 使用 `Restart=on-failure`；vLLM 建议 Docker `--restart unless-stopped`。

## 17. 当前已知缺口

- 真实乳头 JSON schema/最终结果选择语义尚未冻结；
- 编码器无效值、物理阈值仍需按真实协议/固件形成 profile；
- 网络 topology 未确认；
- 通用 business action verification provenance 尚未完全泛化；
- 前端暂无 npm lockfile；
- Open3D 取决于 ARM64 base distribution；
- 当前模型的真实 `MODEL_REPO + MODEL_REVISION` 还必须补到设备资产 manifest；
- task scratch cleanup / mixed gateway-sandbox scratch 仍需后续实测。

## 18. 新会话接手模板

```text
请接手 ScopeX / 端侧 Agent 项目。

仓库：/home/yanlan/workspaces/code/scopex
GitHub：saaassin13/scopex
main 是当前集成基线。

先读：
README.md
01 requirements
02 delivery
06 OpenClaw/ScopeX boundary
07 next plan
business/01-business-capabilities-v1.md
08 handoff
09 deployment

架构边界：OpenClaw + 模型负责自主调查/执行/验证/停止；ScopeX 提供范围、能力、权限、Evidence、审计和可信结果，不重做 Agent Loop。

Step 6A-6F 已冻结。
第一批业务能力是 system-health / image-quality / nipple-recognition-analysis / encoder-health，log-context 作为公共上下文能力；网络暂缓。

历史脚本只作为业务理解和回归参考，不直接当产品需求。
先检查 main 最新 commit、测试状态、真实 JSON/日志数据和剩余验收 Gate。
```
