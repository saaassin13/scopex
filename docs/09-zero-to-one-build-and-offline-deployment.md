# ScopeX 从 0 到 1 构建与离线部署

状态：**2026-09-14 当前部署基线**。

覆盖 NVIDIA DGX Spark 从基础环境到 ScopeX 可运行、弱网/离线交付、升级和回滚。

> **OpenClaw owns execution. ScopeX owns product control and trust.**

ScopeX Runtime 运行在 Spark 宿主机普通用户进程，通过本地 Docker daemon 启动隔离 Sandbox；不使用 Docker-in-Docker，不要求 Kubernetes。

## 1. 部署资产分层

不要把所有资产做成一个巨大更新包。

### Device Base Package（低频）

```text
DGX Spark Base
├── DGX OS / NVIDIA driver
├── Docker Engine
├── NVIDIA Container Runtime
├── OpenClaw CLI
├── vLLM Docker image
└── model weights
```

### ScopeX Update Bundle（高频）

```text
ScopeX Update
├── fixed-commit source
├── frontend/dist
├── host Python wheelhouse
├── scopex-sandbox-analysis image
├── built-in Skills
├── install script
└── manifest + SHA256
```

模型几十 GB，不应随着业务 Skill 小修改反复传输。

## 2. 目标机器

当前设备：

```text
NVIDIA DGX Spark
Linux / ARM64 (aarch64)
128 GB unified memory
```

先确认：

```bash
uname -m
python3 --version
docker version
nvidia-smi
~/.openclaw/bin/openclaw --version
```

Docker image 和 Python wheelhouse 必须按现场 ARM64 架构准备。

## 3. Docker / NVIDIA Runtime

DGX Spark 通常已经具备 NVIDIA Container Toolkit；ScopeX 不重复维护宿主机 Docker 安装器，先验证。

```bash
docker ps
nvidia-ctk --version
```

普通用户没有 Docker 权限时：

```bash
sudo usermod -aG docker "$USER"
newgrp docker
```

GPU 容器 smoke：

```bash
docker run --rm --gpus all \
  nvcr.io/nvidia/cuda:13.0.1-devel-ubuntu24.04 \
  nvidia-smi
```

如 toolkit 已安装但 runtime 未配置：

```bash
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

### 弱网/离线镜像

不要依赖现场拉 Docker Hub / NGC。联网构建机提前：

```bash
docker save <image:tag> | gzip -1 > image.tar.gz
```

现场：

```bash
gzip -dc image.tar.gz | docker load
```

## 4. 清华源边界

ScopeX 使用清华 TUNA 加速**构建阶段**：

- PyPI：`https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple`
- Ubuntu ARM64：TUNA `ubuntu-ports`
- Debian：TUNA Debian 主源 / security 对应镜像

`docker/sandbox-analysis.Dockerfile` 只修改 Sandbox build-time APT 源，**不修改 Spark 宿主机系统安全更新源**。

现场运行时 Sandbox 仍 `network=none`。

## 5. 模型选择

当前 ScopeX 已验证 **served model id**：

```text
qwen3.8-27b-nvfp4
```

Step 6/当前产品预算结论基于它，不在新模型通过同一 Gate 前自动替换。

注意：served id 不是下载地址。真正 0→1 重建必须记录：

```text
MODEL_REPO=<真实模型仓库>
MODEL_REVISION=<固定 commit/revision>
quantization=<真实量化格式>
served_model_id=qwen3.8-27b-nvfp4
```

当前已有部署对应的真实 `MODEL_REPO + MODEL_REVISION` 仍是已知事实缺口，必须从现机补录。

### 新模型 Gate

至少验证：

1. DGX Spark / ARM64 + 当前 vLLM 可运行；
2. tool calling 多轮可靠；
3. context 不低于 32768 基线；
4. 图片输入可用；
5. 内存余量；
6. Step 6 复杂任务 Gate；
7. 单图范围约束；
8. Business V1（system/image/nipple/encoder/log-context）。

官方推荐模型只能作为候选，不能替代实际回归。

## 6. 模型下载与离线搬运

联网 ARM64 构建机：

```bash
python3 -m venv "$HOME/model-tools"
source "$HOME/model-tools/bin/activate"
python -m pip install \
  -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple \
  huggingface_hub
```

固定 revision：

```bash
export MODEL_REPO='<真实 repo id>'
export MODEL_REVISION='<固定 commit/revision>'
export MODEL_DIR="$HOME/models/<model-version>"

mkdir -p "$MODEL_DIR"
hf download "$MODEL_REPO" \
  --revision "$MODEL_REVISION" \
  --local-dir "$MODEL_DIR"
```

生成校验：

```bash
(
  cd "$MODEL_DIR"
  find . -type f ! -path './.cache/*' -print0 \
    | sort -z \
    | xargs -0 sha256sum > MODEL_SHA256SUMS
)
```

把整个固定版本目录放进 Device Base Package；现场复制后：

```bash
cd "$MODEL_DIR"
sha256sum -c MODEL_SHA256SUMS
```

## 7. vLLM Docker 部署

生产不要长期使用 `latest`：

```bash
export VLLM_IMAGE='vllm/vllm-openai:<validated-tag>'
docker pull "$VLLM_IMAGE"
docker image inspect "$VLLM_IMAGE"
```

离线导出：

```bash
docker save "$VLLM_IMAGE" | gzip -1 > vllm-image.tar.gz
```

当前 ScopeX endpoint：

```text
http://127.0.0.1:18002/v1
context baseline = 32768
```

基础模板：

```bash
export MODEL_DIR="$HOME/models/<model-version>"
export SERVED_MODEL_NAME='qwen3.8-27b-nvfp4'
export VLLM_IMAGE='vllm/vllm-openai:<validated-tag>'

docker run -d \
  --name scopex-vllm \
  --restart unless-stopped \
  --gpus all \
  --ipc host \
  --ulimit memlock=-1 \
  --ulimit stack=67108864 \
  --entrypoint '' \
  -p 127.0.0.1:18002:8000 \
  -v "$MODEL_DIR:/models/model:ro" \
  "$VLLM_IMAGE" \
  vllm serve /models/model \
    --served-model-name "$SERVED_MODEL_NAME" \
    --max-model-len 32768 \
    --gpu-memory-utilization 0.8
```

模型专用 quantization/parser/tool-call 参数必须按真实模型验证后固定，不在通用文档里猜。

验证：

```bash
curl -sf http://127.0.0.1:18002/health
curl -s http://127.0.0.1:18002/v1/models | python3 -m json.tool
```

ScopeX `--model` 必须使用 `/v1/models` 返回的真实 id。

最小请求：

```bash
curl -s http://127.0.0.1:18002/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model":"qwen3.8-27b-nvfp4",
    "messages":[{"role":"user","content":"只回复 OK"}],
    "max_tokens":16,
    "temperature":0
  }'
```

## 8. OpenClaw

ScopeX bundle 不复制 `~/.openclaw`，避免携带 token/敏感配置。

确认固定版本：

```bash
~/.openclaw/bin/openclaw --version
```

OpenClaw 安装资产/版本进入 Device Base manifest。现场不要自动升级到未知最新版。

## 9. 获取 ScopeX

联网环境：

```bash
cd /home/yanlan/workspaces/code
git clone git@github.com:saaassin13/scopex.git
cd scopex
git checkout main
git pull --ff-only
```

离线包必须绑定 clean worktree 的具体 commit。

## 10. Host Python

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install \
  -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple \
  -r requirements-api.txt
```

Host 依赖保持轻量；图像/数据分析依赖主要放 Sandbox。

## 11. Web UI

要求 Node `>=22.18.0`：

```bash
cd frontend
npm install
npm run build
cd ..
```

现场离线端不执行 npm install，直接使用预构建 `frontend/dist`。

当前缺 npm lockfile；离线交付暂以预构建 dist 为准。

## 12. Analysis Sandbox

镜像：

```text
scopex-sandbox-analysis:step7
```

构建：

```bash
docker build \
  -f docker/sandbox-analysis.Dockerfile \
  -t scopex-sandbox-analysis:step7 \
  .
```

Dockerfile 已给 `BASE_IMAGE=scopex-sandbox-base:step6f` 默认值，仍可用 `--build-arg BASE_IMAGE=...` 覆盖。

验证：

```bash
docker run --rm --network none --entrypoint python3 \
  scopex-sandbox-analysis:step7 \
  -c 'import cv2, PIL, numpy, pandas, scipy, skimage, matplotlib, openpyxl, yaml, psutil, sklearn; print("ScopeX toolbox OK")'

docker run --rm --network none --entrypoint cat \
  scopex-sandbox-analysis:step7 /opt/scopex/toolbox.json
```

## 13. Business Skills

Runtime 默认：

```text
system-health
image-quality-diagnosis
nipple-recognition-analysis
encoder-health
log-context
```

启动时同步到 `<workspace>/skills` 并加入 OpenClaw allowlist。

## 14. 当前宿主机资源快照

**不部署系统资源 timer，不保存历史。**

每个 Task 创建时，ScopeX host 侧生成一次：

```text
.local/runtime-api/work/<task-id>/host/current.json
```

并只读挂载：

```text
/scopex-host/current.json
```

Agent 仍是 `exec_host=sandbox`。system-health 只能使用该当前快照；字段采集失败时返回 unavailable，禁止使用 Sandbox `/proc/free/df/nvidia-smi` fallback。

手工检查 helper：

```bash
python3 scripts/collect_system_metrics.py --pretty
```

该脚本只打印**当前一次**快照，不写 JSONL。

## 15. 启动 ScopeX Runtime API

```bash
mkdir -p .local/workspace
curl -s http://127.0.0.1:18002/v1/models

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
API = 127.0.0.1:8787
turn timeout = 600s
model requests/turn = 16
business data = read-only
host current snapshot = per task, read-only
sandbox network = none
scheduler = local simple trigger
```

健康检查：

```bash
curl -s http://127.0.0.1:8787/health
```

## 16. ScopeX systemd

模板：

```text
deploy/systemd/scopex-runtime.service
deploy/systemd/runtime.env.example
```

安装：

```bash
mkdir -p ~/.config/systemd/user ~/.config/scopex
cp deploy/systemd/scopex-runtime.service ~/.config/systemd/user/
cp deploy/systemd/runtime.env.example ~/.config/scopex/runtime.env
systemctl --user daemon-reload
systemctl --user enable --now scopex-runtime.service
```

未登录也要求开机运行：

```bash
sudo loginctl enable-linger "$USER"
```

日志：

```bash
journalctl --user -u scopex-runtime.service -f
```

vLLM 用 Docker `--restart unless-stopped`；ScopeX Runtime 用 `Restart=on-failure`。

## 17. ScopeX 离线 Update Bundle

联网 ARM64 构建机先确保：

- worktree clean；
- frontend/dist 已 build；
- analysis sandbox 已 build；
- pip 可访问 TUNA。

导出：

```bash
bash scripts/export_offline_bundle.sh
```

生成：

```text
dist/offline/scopex-offline-<commit>-<arch>/
dist/offline/scopex-offline-<commit>-<arch>.tar.gz
...sha256
```

内容：

```text
manifest.txt
SHA256SUMS
source/scopex-source.tar.gz
frontend-dist/
wheelhouse/
images/scopex-sandbox-analysis.tar.gz
scripts/install_offline_bundle.sh
```

不包含 OpenClaw、vLLM image、模型权重。

## 18. Device Base Package

建议：

```text
device-base-<version>/
├── manifest.txt
├── images/vllm-image.tar.gz
├── models/<model-version>/MODEL_SHA256SUMS
└── openclaw/<validated install artifact/instructions>
```

manifest 至少：

```text
architecture=aarch64
vllm_image=<tag>
vllm_image_id=<id/digest>
model_repo=<repo>
model_revision=<commit>
served_model_id=qwen3.8-27b-nvfp4
max_model_len=32768
openclaw_version=<version>
```

## 19. 离线安装 ScopeX

```bash
sha256sum -c scopex-offline-<commit>-<arch>.tar.gz.sha256
tar -xzf scopex-offline-<commit>-<arch>.tar.gz

bash scopex-offline-<commit>-<arch>/scripts/install_offline_bundle.sh \
  scopex-offline-<commit>-<arch> \
  "$HOME/scopex-releases/<commit>"
```

安装器：校验 SHA256/架构、解压源码、安装 frontend/dist、从 wheelhouse `--no-index` 安装 Python、`docker load` analysis image。不会覆盖已有非空版本目录。

## 20. 升级与回滚

```text
~/scopex-releases/
├── <commit-A>/
├── <commit-B>/
└── current -> <commit-B>
```

新版本先独立安装/smoke，成功后：

```bash
ln -sfn "$HOME/scopex-releases/<new-commit>" "$HOME/scopex-releases/current"
systemctl --user restart scopex-runtime.service
```

回滚切回旧 symlink + 旧 sandbox tag。不要自动删除旧模型/vLLM image/audit。

## 21. 新机器验收清单

```text
[ ] uname -m = aarch64
[ ] docker ps 可用
[ ] NVIDIA GPU container 可运行 nvidia-smi
[ ] OpenClaw 版本与 manifest 一致
[ ] vLLM image/tag/digest 与 manifest 一致
[ ] model repo/revision/SHA 一致
[ ] vLLM /health 正常
[ ] /v1/models 返回期望 served id
[ ] 最小 chat completion 成功
[ ] scopex-sandbox-analysis:step7 已导入
[ ] /opt/scopex/toolbox.json 可读
[ ] Python unit suite 通过
[ ] frontend/dist 存在
[ ] ScopeX /health 正常
[ ] 默认 Skills 可见
[ ] Conversation 普通问答可完成
[ ] 当前 system-health 使用 /scopex-host/current.json
[ ] 正式业务 Task 可完成 Evidence/Claims/Result
[ ] 定时任务可触发普通 Task
[ ] started/finished/duration 正确
[ ] 评价与 review ZIP 可用
[ ] 单图范围任务不读取被排除数据
[ ] Runtime / vLLM 自动恢复可验证
[ ] 上一版本可以回滚
```

## 22. 当前已知缺口

- 当前模型真实 `MODEL_REPO + MODEL_REVISION` 待从现机补录；
- frontend npm lockfile 缺失；
- Open3D ARM64 非强制；
- 网络 topology 未确认；
- Product V1 / Business V1 尚待 Spark 真实验收。

## 23. 上游参考

部署前版本变化时复核官方：

- NVIDIA DGX Spark Container Runtime：`https://docs.nvidia.com/dgx/dgx-spark/nvidia-container-runtime-for-docker.html`
- NVIDIA DGX Spark vLLM：`https://build.nvidia.com/spark/vllm/instructions`
- vLLM Docker：`https://docs.vllm.ai/en/stable/deployment/docker/`
- Hugging Face download：`https://huggingface.co/docs/huggingface_hub/main/guides/download`
- 清华 TUNA：`https://mirrors.tuna.tsinghua.edu.cn/help/`
