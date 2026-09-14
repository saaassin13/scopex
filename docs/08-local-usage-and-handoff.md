# ScopeX 本地使用与接手手册

状态：**2026-09-14 当前有效**。

这份文档用于：

1. 在 Spark 上快速启动当前 ScopeX；
2. 调试当前真实 Runtime；
3. 新会话/新开发者快速接手；
4. 明确哪些能力已验证、哪些只是已实现待验收。

## 1. 当前阶段结论

冻结回归基线：

```text
6A Context / Compaction            PASS
6B Large Data / Multi-Image       PASS
6C Hard Budget                    PASS
6D Native Loop Convergence        PASS
6E Complex Task Capability        CAPABILITY PASS
6F 600s / 16-request Product Gate PASS
```

Step 7 当前：

```text
7A Claim-bounded Product Answer   IMPLEMENTED / acceptance pending
7B Result-first UI                IMPLEMENTED / acceptance pending
7C Spark FastAPI + Vue            IN PROGRESS
7D Real business acceptance       PENDING
7E Offline deployment baseline    IMPLEMENTED / smoke pending
```

固定边界：

> **OpenClaw owns execution. ScopeX owns product control and trust.**

不要重新实现第二套 Agent Loop / Workflow Engine。

## 2. 接手时先读什么

按顺序：

1. `README.md`
2. `docs/01-requirements.md`
3. `docs/02-delivery-and-acceptance.md`
4. `docs/architecture/06-openclaw-scopex-boundary.md`
5. `docs/architecture/07-complex-task-validation-and-next-plan.md`
6. 本文件
7. `docs/09-zero-to-one-build-and-offline-deployment.md`

`docs/poc/` 用于历史追溯，不应覆盖上述当前文档。

## 3. 当前运行基线

```text
Device: NVIDIA DGX Spark / ARM64
Agent Runtime: OpenClaw
Model: qwen3.8-27b-nvfp4
Inference: local vLLM OpenAI-compatible API
Product API: FastAPI + Uvicorn
UI: Vue 3 + TypeScript + Vite
Sandbox image: scopex-sandbox-analysis:step7
```

默认 OpenClaw CLI：

```text
~/.openclaw/bin/openclaw
```

确认：

```bash
~/.openclaw/bin/openclaw --version
curl -s http://127.0.0.1:18002/v1/models
```

## 4. Python / 前端

Host Python 推荐 venv：

```bash
cd /home/yanlan/workspaces/code/scopex
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

要求 Node `>= 22.18.0`。

离线现场不要重新执行 npm install；使用提前构建的 `frontend/dist`。完整离线流程见 `docs/09-zero-to-one-build-and-offline-deployment.md`。

## 5. Analysis Sandbox

目标镜像：

```text
scopex-sandbox-analysis:step7
```

从已验证 base image 构建：

```bash
docker build \
  -f docker/sandbox-analysis.Dockerfile \
  --build-arg BASE_IMAGE=scopex-sandbox-base:step6f \
  -t scopex-sandbox-analysis:step7 \
  .
```

Dockerfile build-time APT 源使用清华 TUNA；Ubuntu ARM64 自动使用 `ubuntu-ports`。这只影响容器构建，不修改 Spark host 源。

当前 toolbox：

```text
numpy / scipy / pandas / cv2 / Pillow / scikit-image
matplotlib / openpyxl / PyYAML / psutil / scikit-learn
Open3D when available
```

验证：

```bash
docker run --rm \
  --network none \
  --entrypoint python3 \
  scopex-sandbox-analysis:step7 \
  -c 'import cv2, PIL, numpy, pandas, scipy, skimage, matplotlib, openpyxl, yaml, psutil, sklearn; print("OK")'
```

实际 manifest：

```bash
docker run --rm \
  --network none \
  --entrypoint cat \
  scopex-sandbox-analysis:step7 \
  /opt/scopex/toolbox.json
```

## 6. 默认 Skill

Runtime API 默认加载：

```text
cow-disinfect-diagnosis
image-quality-diagnosis
```

启动时 ScopeX 会把仓库内置 Skill 同步到：

```text
<workspace>/skills/
```

之后再交给 OpenClaw allowlist。

检查：

```bash
find .local/workspace/skills -maxdepth 4 -type f -print
```

额外 Skill：

```bash
--skill <name>
```

框架回归需要完全关闭产品 Skill 时：

```bash
--no-default-skills
```

## 7. Runtime 通用任务契约

这是当前真实业务测试后新增的产品原则，不是固定 Workflow：

- 用户明确指定的 target/source/scope 是任务约束；
- 走回答问题所需的最短充分证据路径；
- 不因为 `/agent-data` 有其他文件就自动调查；
- 只有原范围不足以回答用户原问题时才最小扩张；
- 证据足够后停止工具调用；
- 无法确认具体原因时允许 `unknown/待验证`，不要以无限调查替代不确定性。

例如用户说“只看这一张图，不读日志/JSON/其他图片”，Agent 应尊重这个边界。

## 8. 启动 Runtime API

同步 main：

```bash
cd /home/yanlan/workspaces/code/scopex
git checkout main
git pull --ff-only
mkdir -p .local/workspace
```

启动：

```bash
.venv/bin/python scripts/runtime_api.py \
  --model qwen3.8-27b-nvfp4 \
  --base-url http://127.0.0.1:18002/v1 \
  --workspace .local/workspace \
  --sandbox-image scopex-sandbox-analysis:step7 \
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
sandbox runtime network: none
```

启动日志应看到：

```text
skills: cow-disinfect-diagnosis, image-quality-diagnosis
```

## 9. API 快速使用

健康检查：

```bash
curl -s http://127.0.0.1:8787/health
```

创建 Task：

```bash
curl -s \
  -X POST http://127.0.0.1:8787/tasks \
  -H 'Content-Type: application/json' \
  -d '{"message":"诊断 /agent-data 中当前异常，给出结论和证据。"}'
```

状态：

```bash
curl -s http://127.0.0.1:8787/tasks/<task_id>
```

Progress：

```bash
curl -s 'http://127.0.0.1:8787/tasks/<task_id>/events?after=0'
```

Evidence：

```bash
curl -s http://127.0.0.1:8787/tasks/<task_id>/evidence
```

Result：

```bash
curl -s http://127.0.0.1:8787/tasks/<task_id>/result
```

## 10. Stop / Resume / Steering

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
  -d '{"message":"继续刚才任务"}'
```

纠正方向：

```bash
curl -s \
  -X POST http://127.0.0.1:8787/tasks/<task_id>/steer \
  -H 'Content-Type: application/json' \
  -d '{"message":"只检查用户指定的图片，不要读取其他数据"}'
```

Stop/Steer 在安全 model-request boundary 生效，不等同于强杀已经发生副作用的动作。

## 11. Result / Audit

主可信链：

```text
Evidence
 -> Fresh Finalizer
 -> Validated Claims
 -> Product Answer
 -> Result-first UI
```

Task audit 默认在：

```text
.local/runtime-api/tasks/<task-id>/
```

可能包含：

```text
task.json
session.json
events.jsonl
evidence.json
claims.json
answer.json
result.json
final.txt
runtime-limit.json
runtime-guard.json
cleanup.json
```

`answer.json` 是产品投影；`final.txt` 继续作为 deterministic trust fallback。

如果 Finalizer 第一次因 `finish_reason=length` 截断，当前实现允许同 Evidence 一次长度恢复重试；`result.json` 会记录：

```text
finalizer_retry_count
```

## 12. Web UI

`frontend/dist` 存在时，FastAPI 自动在 `/` 提供 Vue 页面。

主视图现在是 Result-first：

```text
结论
说明
执行情况
建议

调查进度 >
相关证据 >
可信渲染 fallback >
```

Step 7 当前还需要在 Spark 上做完整产品联调；代码已经实现不等于 UI 验收已 PASS。

## 13. 单图范围验收

这是当前最重要的真实行为回归之一。

示例：

```text
只检查 /agent-data/07-img/example.jpg 这一张原图。
直接使用视觉能力判断：
1. 是否明显模糊；
2. 是否呈现起雾特征；
3. 是否呈现镜头脏污特征。
不要读取日志、JSON、其他图片或目录信息。
如果仅凭这一张图不能确认具体物理原因，直接说明不确定。
```

验收：

- 指定原图被实际 `view_image`；
- 不访问排除的数据；
- 不做目录级扫描；
- 不持续请求直到 16-request hard limit；
- 允许“不确定”；
- Claims/Answer 可追溯。

## 14. 全量回归

后端：

```bash
python3 -m unittest discover -s tests -v
```

前端：

```bash
cd frontend
npm run build
```

Sandbox：

```bash
docker image inspect scopex-sandbox-analysis:step7 >/dev/null
```

## 15. 离线部署

从 0 到 1和离线流程全部维护在：

```text
docs/09-zero-to-one-build-and-offline-deployment.md
```

在线 ARM64 构建机导出：

```bash
bash scripts/export_offline_bundle.sh
```

离线端解压后：

```bash
bash scopex-offline-<commit>-<arch>/scripts/install_offline_bundle.sh \
  scopex-offline-<commit>-<arch> \
  "$HOME/scopex"
```

不要在现场重新 npm install / apt install common analysis packages。

## 16. 自启动

模板：

```text
deploy/systemd/scopex-runtime.service
deploy/systemd/runtime.env.example
```

推荐 user systemd + `Restart=on-failure`，不使用 Docker-in-Docker。

## 17. 当前已知缺口

- 通用 business action verification provenance 仍未完全泛化；
- 前端没有 npm lockfile，离线部署依赖预构建 `frontend/dist`；
- Open3D 取决于 ARM64 base distribution；
- OpenClaw / vLLM / model 属于设备基础环境，不在 ScopeX 小版本 bundle；
- task scratch cleanup 仍简单；
- mixed gateway/sandbox scratch 尚需实测。

## 18. 新会话接手模板

```text
请接手 ScopeX / 端侧 Agent 项目。

仓库：/home/yanlan/workspaces/code/scopex
GitHub：saaassin13/scopex
main 是唯一当前集成基线。

先阅读：
1. README.md
2. docs/01-requirements.md
3. docs/02-delivery-and-acceptance.md
4. docs/architecture/06-openclaw-scopex-boundary.md
5. docs/architecture/07-complex-task-validation-and-next-plan.md
6. docs/08-local-usage-and-handoff.md
7. docs/09-zero-to-one-build-and-offline-deployment.md

固定架构边界：
OpenClaw + 模型负责自主调查、决策、执行、验证和停止；ScopeX 提供任务范围、能力、权限、生命周期、Evidence、审计和可信产品结果，不重新实现 Agent Loop / Workflow Engine。

Step 6A-6F 已冻结通过。
当前 Step 7A/7B 已实现但新整合版仍需完整回归；7C/7D 是主验收工作。
真实业务已暴露并修复两类问题：
- budget finalization 的 structured output length truncation；
- 单图明确范围任务过度扩张调查。

还新增：
- 默认 built-in Skill provisioning；
- image-quality-diagnosis Skill + stable metrics script；
- step7 analysis toolbox；
- 清华 build source + offline bundle/systemd 部署基线。

不要无证据重做 Step 6，也不要写业务专用 Workflow。先检查 main 最新 commit、测试状态和未完成 Gate。
```
