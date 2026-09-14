# ScopeX 从 0 到 1 构建与离线部署

状态：**2026-09-14 当前部署基线**。

这份文档覆盖一台 NVIDIA DGX Spark 从空机到 ScopeX 可运行、弱网/离线部署、版本升级和回滚。

固定边界：

> **OpenClaw owns execution. ScopeX owns product control and trust.**

ScopeX Runtime 运行在 Spark 宿主机普通用户进程中，通过本地 Docker daemon 启动隔离 Sandbox；不使用 Docker-in-Docker，也不要求 Kubernetes。

---

## 1. 部署资产分层

不要把所有东西打成一个巨大更新包。推荐分成两个生命周期。

### 1.1 Device Base Package（低频更新）

```text
DGX Spark Base
├── DGX OS / NVIDIA driver
├── Docker Engine
├── NVIDIA Container Toolkit / Runtime
├── OpenClaw CLI
├── vLLM Docker image
└── model weights
```

这些资产大、更新频率低，特别是模型权重不应跟着 ScopeX 小版本反复传输。

### 1.2 ScopeX Update Bundle（高频更新）

```text
ScopeX update
├── fixed-commit source
├── frontend/dist
├── host Python wheelhouse
├── scopex-sandbox-analysis image
├── built-in Skills
├── install script
└── manifest + SHA256
```

现有 `scripts/export_offline_bundle.sh` 只制作这一层，不包含 vLLM/model，这是有意设计。

---

## 2. 目标硬件与架构

当前目标：

```text
NVIDIA DGX Spark
Linux / ARM64 (aarch64)
128 GB unified memory
```

确认：

```bash
uname -m
python3 --version
docker version
nvidia-smi
~/.openclaw/bin/openclaw --version
```

Docker image 与 Python wheelhouse 必须按目标架构准备。推荐在联网的 ARM64 Spark/同架构 Linux 构建机上制作离线资产。

---

## 3. Docker 与 NVIDIA Runtime

NVIDIA 当前 DGX Spark 文档说明：NVIDIA Container Toolkit / Docker GPU runtime 在 DGX Spark 上默认预装并配置。ScopeX 不重复维护一套 Docker 安装脚本，先验证设备基础环境。

### 3.1 验证 Docker

```bash
docker ps
```

如果普通用户没有权限：

```bash
sudo usermod -aG docker "$USER"
newgrp docker
```

### 3.2 验证 NVIDIA Container Runtime

```bash
nvidia-ctk --version
```

GPU 容器验证（镜像 tag 以 NVIDIA 当前可用版本为准）：

```bash
docker run --rm --gpus all \
  nvcr.io/nvidia/cuda:13.0.1-devel-ubuntu24.04 \
  nvidia-smi
```

如果 toolkit 已安装但 Docker runtime 未配置：

```bash
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

### 3.3 弱网/离线 Docker 原则

ScopeX 只把 **APT / PyPI** 指向已经验证的清华 TUNA 源；不要为了“统一用清华源”而写一个未经验证的 Docker Hub/HuggingFace 镜像地址。

Docker/vLLM 镜像在联网构建机提前拉取，然后：

```bash
docker save <image:tag> | gzip -1 > image.tar.gz
```

现场：

```bash
gzip -dc image.tar.gz | docker load
```

这比依赖牧场现场访问 Docker Hub/NGC 更稳定。

---

## 4. 模型选择

模型是 ScopeX 的核心运行依赖，不能只写一个 `--model` 字符串。

### 4.1 当前生产基线

当前 ScopeX 已验证的 **served model id**：

```text
qwen3.8-27b-nvfp4
```

当前 Step 6/Step 7 的能力和预算结论都是基于这个本地 served id 得出的，因此在新模型完成同一套回归前，不自动更换生产默认模型。

**注意：served model id 不等于模型下载仓库。** 当前仓库尚未记录 `qwen3.8-27b-nvfp4` 对应的原始 HuggingFace/NGC model handle。要实现真正从 0 到 1 重建，必须把以下两项补齐并记录到设备资产 manifest：

```text
MODEL_REPO=<真实模型仓库，例如 org/model-name>
MODEL_REVISION=<固定 commit/revision>
```

不要只保存“模型名字”，否则未来下载到的新 revision 可能已经不同。

### 4.2 新模型选择 Gate

ScopeX 的模型至少要验证：

1. DGX Spark / ARM64 + 当前 vLLM 可运行；
2. Agent/tool calling 多轮任务可靠；
3. Context 至少满足当前 `32768` 基线；
4. 图片能力可用，因为 `image-quality-diagnosis` 需要原图视觉输入；
5. 当前量化格式在 Spark 上有足够内存余量；
6. Step 6 复杂任务 Gate；
7. 单图严格范围任务；
8. 第一批业务 Skill（system/nipple/encoder/log-context）回归。

NVIDIA 在 2026-09 当前 DGX Spark vLLM playbook 中推荐的 agent-ready 候选之一是：

```text
nvidia/Qwen3.6-35B-A3B-NVFP4
```

它可以作为后续候选测试，但**不是因为官方推荐就直接替换当前模型**；ScopeX 还有图片输入、既有 Prompt/Tool 行为和当前预算 Gate，需要实际回归。

---

## 5. 模型下载与离线搬运

Hugging Face 官方当前推荐使用 `hf download` / `snapshot_download` 下载固定 revision。

### 5.1 准备下载工具

```bash
python3 -m venv "$HOME/model-tools"
source "$HOME/model-tools/bin/activate"
python -m pip install \
  -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple \
  huggingface_hub
```

对于 gated/private 模型：

```bash
export HF_TOKEN='<token>'
```

### 5.2 固定仓库与 revision

```bash
export MODEL_REPO='<真实 repo id>'
export MODEL_REVISION='<固定 commit/revision>'
export MODEL_DIR="$HOME/models/<model-version>"

mkdir -p "$MODEL_DIR"
hf download "$MODEL_REPO" \
  --revision "$MODEL_REVISION" \
  --local-dir "$MODEL_DIR"
```

下载完成后记录：

```text
model_repo
model_revision
local_path
quantization
expected served model id
```

并可生成文件校验：

```bash
(
  cd "$MODEL_DIR"
  find . -type f ! -path './.cache/*' -print0 \
    | sort -z \
    | xargs -0 sha256sum > MODEL_SHA256SUMS
)
```

### 5.3 离线搬运

模型通常几十 GB 甚至更大，不建议塞进 ScopeX update bundle。直接把整个固定版本模型目录放到 Device Base Package/移动硬盘：

```text
/device-base/models/<model-version>/
```

现场复制到：

```text
~/models/<model-version>/
```

再用 `sha256sum -c MODEL_SHA256SUMS` 校验。

---

## 6. vLLM 部署

vLLM 官方提供 OpenAI-compatible Docker image `vllm/vllm-openai`；NVIDIA 的 DGX Spark playbook 也采用容器化 vLLM。

### 6.1 镜像版本

测试时可以参考官方当前 tag，生产设备不要长期依赖 `latest`：

```bash
export VLLM_IMAGE='vllm/vllm-openai:<validated-tag>'
docker pull "$VLLM_IMAGE"
```

记录实际 image ID / digest：

```bash
docker image inspect "$VLLM_IMAGE"
```

离线现场提前：

```bash
docker save "$VLLM_IMAGE" | gzip -1 > vllm-image.tar.gz
```

### 6.2 ScopeX 当前启动模板

当前 ScopeX 采用：

```text
host endpoint: http://127.0.0.1:18002/v1
context baseline: 32768
```

示例：

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

这是基础模板。**模型专用 recipe 的 quantization/parser/tool-call 参数必须按真实模型验证后固定**，不要在文档里猜测并强制所有模型共用。

当前 NVIDIA DGX Spark playbook 也提醒：Spark 使用统一内存，`--max-model-len` 越大 KV cache 压力越大。ScopeX 继续使用已经验证过的 32768 基线，而不是为了追求数字直接改成 131072。

### 6.3 验证 vLLM

```bash
docker logs -f scopex-vllm
```

健康检查：

```bash
curl -sf http://127.0.0.1:18002/health
```

确认 served id：

```bash
curl -s http://127.0.0.1:18002/v1/models | python3 -m json.tool
```

ScopeX `--model` **必须使用这里真实返回的 id**，不能只使用下载仓库名。

最小 OpenAI-compatible 请求：

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

---

## 7. OpenClaw

ScopeX update bundle 不复制 `~/.openclaw`，避免打包 token/本地敏感配置。

设备基础环境必须先准备并验证：

```bash
~/.openclaw/bin/openclaw --version
```

OpenClaw 的具体安装包/版本也应进入 Device Base Package manifest。不要现场联网“自动升级到最新版”，否则 ScopeX 回归基线会漂移。

---

## 8. 获取 ScopeX 代码

联网构建机：

```bash
cd /home/yanlan/workspaces/code
git clone git@github.com:saaassin13/scopex.git
cd scopex
git checkout main
git pull --ff-only
```

所有离线包绑定具体 git commit；不要从 dirty worktree 导出。

---

## 9. Host Python 环境

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install \
  -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple \
  -r requirements-api.txt
```

Host API 依赖保持轻量。数据/图像分析依赖放到 Sandbox；宿主机系统资源采集脚本使用 Python 标准库 + OS 命令，不要求业务 Agent 获得 gateway shell。

---

## 10. 构建 Web UI

要求 Node `>=22.18.0`：

```bash
cd frontend
npm install
npm run build
cd ..
```

现场离线机器不执行 `npm install`，直接使用 bundle 中预构建的 `frontend/dist`。

当前仍无 npm lockfile，这是源码完全可复现构建的已知缺口；离线交付暂以预构建 dist 为准。

---

## 11. Analysis Sandbox

当前镜像：

```text
scopex-sandbox-analysis:step7
```

运行时 `network=none`。镜像 build 阶段预装 numpy/scipy/pandas/OpenCV/Pillow/scikit-image/matplotlib/openpyxl/PyYAML/psutil/scikit-learn；Open3D 仅在 ARM64 基础发行版有对应 apt 包时安装。

### 11.1 清华 APT 源

`docker/sandbox-analysis.Dockerfile` 在 build 阶段把 Ubuntu/Debian 主源切到 TUNA：

```text
https://mirrors.tuna.tsinghua.edu.cn
```

ARM Ubuntu 使用 `ubuntu-ports`。该修改只作用于 Sandbox build，不修改 Spark 宿主机安全更新策略。

### 11.2 构建

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
  -c 'import cv2, PIL, numpy, pandas, scipy, skimage, matplotlib, openpyxl, yaml, psutil, sklearn; print("ScopeX toolbox OK")'

docker run --rm --network none --entrypoint cat \
  scopex-sandbox-analysis:step7 /opt/scopex/toolbox.json
```

---

## 12. 第一批业务 Skill

Runtime 默认暴露：

```text
system-health
image-quality-diagnosis
nipple-recognition-analysis
encoder-health
log-context
```

旧 `cow-disinfect-diagnosis` 保留在仓库供历史/专项回归，但不再作为默认业务 Skill。

详细业务口径见：

```text
docs/business/01-business-capabilities-v1.md
```

---

## 13. 宿主机系统资源历史

`system-health` 不能拿 Sandbox 自己的 `/proc` 当 Spark 状态。V1 用 host systemd timer 每 30 秒采一条事实。

安装：

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/scopex-system-metrics.service ~/.config/systemd/user/
cp deploy/systemd/scopex-system-metrics.timer ~/.config/systemd/user/

systemctl --user daemon-reload
systemctl --user enable --now scopex-system-metrics.timer
```

确认：

```bash
systemctl --user status scopex-system-metrics.timer
journalctl --user -u scopex-system-metrics.service -n 20

tail -n 2 ~/.local/share/scopex/system-metrics/system_metrics.jsonl | python3 -m json.tool
```

默认 history 文件上限 64 MiB，超过后保留尾部，避免无限增长。

---

## 14. 启动 ScopeX Runtime API

准备：

```bash
mkdir -p .local/workspace ~/.local/share/scopex/system-metrics
curl -s http://127.0.0.1:18002/v1/models
```

启动：

```bash
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
API = 127.0.0.1:8787
turn timeout = 600s
model requests/turn = 16
external business data = read-only
system metrics = read-only
sandbox network = none
```

健康检查：

```bash
curl -s http://127.0.0.1:8787/health
```

---

## 15. ScopeX systemd 自启/恢复

模板：

```text
deploy/systemd/scopex-runtime.service
deploy/systemd/runtime.env.example
deploy/systemd/scopex-system-metrics.service
deploy/systemd/scopex-system-metrics.timer
```

安装 Runtime：

```bash
mkdir -p ~/.config/systemd/user ~/.config/scopex
cp deploy/systemd/scopex-runtime.service ~/.config/systemd/user/
cp deploy/systemd/runtime.env.example ~/.config/scopex/runtime.env

systemctl --user daemon-reload
systemctl --user enable --now scopex-runtime.service
```

未登录也需要开机运行时：

```bash
sudo loginctl enable-linger "$USER"
```

日志：

```bash
journalctl --user -u scopex-runtime.service -f
```

vLLM 容器使用 `--restart unless-stopped`；ScopeX Runtime 使用 systemd `Restart=on-failure`。两者独立恢复。

---

## 16. 制作 ScopeX 离线 Update Bundle

先确保：

- worktree clean；
- `frontend/dist` 已 build；
- `scopex-sandbox-analysis:step7` 已 build；
- 构建机架构与现场一致；
- pip 可访问清华 PyPI。

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

明确不包含：OpenClaw、vLLM image、模型权重。

---

## 17. 制作 Device Base Package

这是低频的大包，可以手工或后续再自动化。

建议目录：

```text
device-base-<version>/
├── manifest.txt
├── images/
│   └── vllm-image.tar.gz
├── models/
│   └── <model-version>/
│       └── MODEL_SHA256SUMS
└── openclaw/
    └── <validated install artifact/instructions>
```

其中 manifest 至少记录：

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

现场顺序：

1. 校验 Device Base Package；
2. `docker load` vLLM image；
3. 拷贝并校验模型目录；
4. 安装/确认固定 OpenClaw；
5. 启动并验证 vLLM；
6. 再安装 ScopeX update bundle。

---

## 18. 离线安装 ScopeX Update Bundle

```bash
sha256sum -c scopex-offline-<commit>-<arch>.tar.gz.sha256
tar -xzf scopex-offline-<commit>-<arch>.tar.gz

bash scopex-offline-<commit>-<arch>/scripts/install_offline_bundle.sh \
  scopex-offline-<commit>-<arch> \
  "$HOME/scopex-releases/<commit>"
```

安装器会：校验 SHA256/架构、解压源码、安装预构建前端、从 wheelhouse `--no-index` 安装 Python 依赖、`docker load` analysis sandbox。

不会覆盖已有非空版本目录。

---

## 19. 升级与回滚

推荐：

```text
~/scopex-releases/
├── <commit-A>/
├── <commit-B>/
└── current -> <commit-B>
```

新版本先独立安装和 smoke，成功后才：

```bash
ln -sfn "$HOME/scopex-releases/<new-commit>" "$HOME/scopex-releases/current"
systemctl --user restart scopex-runtime.service
```

回滚切回旧 symlink + 旧 sandbox tag。不要升级时自动删除旧镜像、旧模型或 audit。

模型/vLLM 升级也应保留上一版本，不能和 ScopeX 代码升级绑成一次不可回退动作。

---

## 20. 新机器最小验收

```text
[ ] uname -m = aarch64
[ ] docker ps 可用
[ ] NVIDIA GPU container 可运行 nvidia-smi
[ ] OpenClaw 版本与 manifest 一致
[ ] vLLM image/tag/digest 与 manifest 一致
[ ] 模型 repo/revision/文件校验一致
[ ] /health 正常
[ ] /v1/models 返回期望 served id
[ ] 最小 chat completion 成功
[ ] scopex-sandbox-analysis:step7 已导入
[ ] /opt/scopex/toolbox.json 可读
[ ] Python unit suite 通过
[ ] frontend/dist 存在
[ ] system metrics timer 持续产出 JSONL
[ ] ScopeX /health 正常
[ ] 默认业务 Skills 可见
[ ] 创建只读诊断 Task 能完成
[ ] 单图明确范围任务不读取被排除的数据
[ ] result/claims/answer/final 可追溯
[ ] Runtime/vLLM 自动恢复可验证
[ ] 上一版本可以回滚
```

只有代码和镜像存在不算部署完成，必须完成实际运行链验证。

---

## 21. 上游参考

部署前如版本发生变化，以官方当前文档复核：

- NVIDIA DGX Spark Container Runtime：`https://docs.nvidia.com/dgx/dgx-spark/nvidia-container-runtime-for-docker.html`
- NVIDIA DGX Spark vLLM playbook：`https://build.nvidia.com/spark/vllm/instructions`
- NVIDIA DGX Spark agent-ready models：`https://build.nvidia.com/spark/vllm/agent-ready-models`
- vLLM Docker：`https://docs.vllm.ai/en/stable/deployment/docker/`
- Hugging Face download：`https://huggingface.co/docs/huggingface_hub/main/guides/download`
- 清华 TUNA PyPI / Ubuntu / Debian：`https://mirrors.tuna.tsinghua.edu.cn/help/`
