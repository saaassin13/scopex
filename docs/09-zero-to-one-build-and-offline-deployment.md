# ScopeX 从 0 到 1 构建与离线部署

状态：**2026-09-14 当前部署基线**。

这份文档回答两个问题：

1. 一台新的 NVIDIA DGX Spark 如何从 0 到 1 启动 ScopeX；
2. 现场网络较差或完全离线时，如何提前制作可校验、可回滚的离线部署包。

ScopeX 的部署边界保持不变：

> **OpenClaw owns execution. ScopeX owns product control and trust.**

ScopeX Runtime 运行在 Spark 宿主机普通用户进程中，通过本地 Docker daemon 启动隔离 Sandbox；不使用 Docker-in-Docker，也不要求 Kubernetes。

---

## 1. 最终需要哪些东西

一套可运行环境包含：

```text
Spark host
├── Docker Engine / compatible daemon
├── Python 3 + venv
├── OpenClaw CLI（已验证版本）
├── local vLLM + model weights
├── ScopeX source + .venv
├── frontend/dist
└── Docker image: scopex-sandbox-analysis:step7
```

ScopeX 自己负责的部署资产：

- `scopex/` Runtime/API/Evidence/Finalizer 代码；
- `frontend/dist` 产品 Web UI；
- `scopex-sandbox-analysis:step7` 分析 Sandbox；
- `skills/` 内置 Skill；
- host Python API 依赖；
- 离线 bundle manifest / SHA256。

以下当前仍是外部前置依赖，不默认塞进 ScopeX 离线包：

- Docker Engine；
- OpenClaw 安装本体；
- vLLM 服务；
- 模型权重。

完全 air-gapped 场景如果这些也需要离线安装，应由设备镜像/基础环境单独打包。模型权重体积很大，不建议和 ScopeX 小版本更新包绑定在一起。

---

## 2. 架构与版本要求

当前目标设备：

```text
NVIDIA DGX Spark
Linux / ARM64 (aarch64)
```

离线包必须在**相同 CPU 架构**上制作，尤其是：

- Docker image；
- Python wheelhouse。

推荐直接在一台联网的 ARM64 Linux/Spark 构建机上制作离线包。

检查：

```bash
uname -m
python3 --version
docker version
~/.openclaw/bin/openclaw --version
```

目标应看到 `aarch64`（或与现场机器完全一致的架构）。

---

## 3. 获取代码

联网构建机：

```bash
cd /home/yanlan/workspaces/code
git clone git@github.com:saaassin13/scopex.git
cd scopex
git checkout main
git pull --ff-only
```

后续所有离线包都绑定到具体 git commit；不要从有未提交改动的工作区导出部署包。

---

## 4. Host Python 环境

推荐使用项目独立 venv：

```bash
cd /home/yanlan/workspaces/code/scopex
python3 -m venv .venv
source .venv/bin/activate
```

联网环境使用清华 PyPI 镜像：

```bash
python -m pip install \
  -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple \
  -r requirements-api.txt
```

当前 host API 依赖保持很小：FastAPI / Uvicorn / HTTPX。数据分析、图像和点云包不安装在 host Python，而是放在隔离 Sandbox 中。

---

## 5. 构建 Web UI

要求 Node `>= 22.18.0`。

联网构建机：

```bash
cd frontend
npm install
npm run build
cd ..
```

产物：

```text
frontend/dist/
```

**现场离线机器不应再执行 `npm install`。** 离线 bundle 直接携带已经构建好的 `frontend/dist`。

当前仓库尚未提交 npm lockfile，因此“重新 npm install 后得到完全可重复的 dependency graph”仍是已知缺口。正式版本发布前应补 lockfile 并切换到 `npm ci`；在此之前，离线部署以预构建 `frontend/dist` 为准。

---

## 6. Analysis Sandbox

当前镜像：

```text
scopex-sandbox-analysis:step7
```

Sandbox 运行期仍保持 `network=none`。常用依赖在镜像 build 阶段预装，避免 Agent 在任务中浪费模型/tool round 探测或尝试安装包。

预装基线：

```text
numpy
scipy
pandas
opencv (cv2)
Pillow
scikit-image
matplotlib
openpyxl
PyYAML
psutil
scikit-learn
Open3D（基础发行版存在 ARM64 apt 包时）
```

镜像会生成：

```text
/opt/scopex/toolbox.json
```

作为实际可用 toolbox manifest。

### 6.1 清华 APT 镜像

`docker/sandbox-analysis.Dockerfile` 会在**容器 build 阶段**自动把 Ubuntu/Debian APT 主源切换到清华 TUNA：

```text
https://mirrors.tuna.tsinghua.edu.cn
```

Ubuntu ARM64 会使用：

```text
/ubuntu-ports
```

Debian 使用：

```text
/debian
/debian-security
```

这个修改只作用于 Sandbox 镜像构建，不修改 Spark 宿主机系统源。

注意：TUNA 官方说明镜像同步存在延迟，生产宿主机的安全更新源不建议因为 ScopeX 而强行替换。ScopeX 这里只为可重复构建离线 Sandbox 使用镜像源。

### 6.2 构建

先准备已经验证过的 OpenClaw Sandbox base image，例如：

```text
scopex-sandbox-base:step6f
```

然后：

```bash
docker build \
  -f docker/sandbox-analysis.Dockerfile \
  --build-arg BASE_IMAGE=scopex-sandbox-base:step6f \
  -t scopex-sandbox-analysis:step7 \
  .
```

验证：

```bash
docker run --rm \
  --network none \
  --entrypoint python3 \
  scopex-sandbox-analysis:step7 \
  -c 'import cv2, PIL, numpy, pandas, scipy, skimage, matplotlib, openpyxl, yaml, psutil, sklearn; print("ScopeX toolbox OK")'
```

查看 manifest：

```bash
docker run --rm \
  --network none \
  --entrypoint cat \
  scopex-sandbox-analysis:step7 \
  /opt/scopex/toolbox.json
```

如果 `Open3D.available=false`，不要让 Agent 运行时联网安装。需要 Open3D 的真实点云任务出现后，再针对当前 Spark 基础发行版固定离线安装方式。

---

## 7. 内置 Skill

Runtime 默认暴露：

```text
cow-disinfect-diagnosis
image-quality-diagnosis
```

启动 `scripts/runtime_api.py` 时，ScopeX 会把仓库内置 Skill 同步到：

```text
<workspace>/skills/
```

然后交给 OpenClaw allowlist。

Skill 负责：

- 业务语义；
- 证据原则；
- 最短充分调查路径；
- 何时停止；
- 可复用脚本入口。

Skill **不**负责实现固定 Workflow Engine。模型仍决定具体工具和调查顺序。

额外 Skill：

```bash
--skill <name>
```

纯框架回归可关闭默认内置 Skill：

```bash
--no-default-skills
```

---

## 8. 本地测试

后端：

```bash
python3 -m unittest discover -s tests -v
```

前端：

```bash
cd frontend
npm run build
cd ..
```

镜像：

```bash
docker image inspect scopex-sandbox-analysis:step7 >/dev/null
```

在新的代码/镜像没有完成这些验证前，文档只标记“已实现”，不要升级为 PASS。

---

## 9. 启动 Runtime API

准备 workspace：

```bash
mkdir -p .local/workspace
```

确认 vLLM：

```bash
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
  --enable-view-image
```

默认：

```text
API = http://127.0.0.1:8787
turn timeout = 600 s
model requests / turn = 16
sandbox network = none
external business data = read-only
```

健康检查：

```bash
curl -s http://127.0.0.1:8787/health
```

---

## 10. 开机自启与自恢复

ScopeX Runtime 本身运行在宿主机普通用户进程，Sandbox 才运行在 Docker 中。推荐使用 user systemd，而不是 Docker-in-Docker。

模板：

```text
deploy/systemd/scopex-runtime.service
deploy/systemd/runtime.env.example
```

安装示例：

```bash
mkdir -p ~/.config/systemd/user ~/.config/scopex
cp deploy/systemd/scopex-runtime.service ~/.config/systemd/user/
cp deploy/systemd/runtime.env.example ~/.config/scopex/runtime.env
```

编辑：

```text
~/.config/scopex/runtime.env
```

然后：

```bash
systemctl --user daemon-reload
systemctl --user enable --now scopex-runtime.service
systemctl --user status scopex-runtime.service
```

需要机器重启后在未登录状态也启动时：

```bash
sudo loginctl enable-linger "$USER"
```

日志：

```bash
journalctl --user -u scopex-runtime.service -f
```

---

## 11. 制作离线 Bundle

### 11.1 前提

必须先完成：

- 当前 commit 已提交，worktree clean；
- `frontend/dist` 已 build；
- `scopex-sandbox-analysis:step7` 已 build；
- 当前机器 CPU 架构与现场一致；
- Python pip 可联网访问清华 PyPI 镜像。

导出：

```bash
bash scripts/export_offline_bundle.sh
```

默认生成：

```text
dist/offline/scopex-offline-<commit>-<arch>/
dist/offline/scopex-offline-<commit>-<arch>.tar.gz
dist/offline/scopex-offline-<commit>-<arch>.tar.gz.sha256
```

目录内容：

```text
manifest.txt
SHA256SUMS
source/scopex-source.tar.gz
frontend-dist/
wheelhouse/
images/scopex-sandbox-analysis.tar.gz
scripts/install_offline_bundle.sh
```

`manifest.txt` 固定记录：

- git commit；
- branch；
- architecture；
- Python 版本；
- sandbox image / image ID；
- PyPI mirror；
- 哪些大组件没有被包含。

### 11.2 为什么不把模型一起打进去

模型权重和 vLLM 镜像通常远大于 ScopeX 本身，而且更新节奏不同。建议设备基础镜像单独管理：

```text
Base device package
├── Docker / NVIDIA runtime
├── OpenClaw
├── vLLM image/runtime
└── model weights

ScopeX update bundle
├── ScopeX source
├── frontend/dist
├── Python wheelhouse
├── sandbox image
└── manifest/checksum
```

这样 ScopeX 小版本升级不需要反复传输几十/上百 GB 模型文件。

---

## 12. 离线端安装

先验证外层压缩包：

```bash
sha256sum -c scopex-offline-<commit>-<arch>.tar.gz.sha256
```

解压：

```bash
tar -xzf scopex-offline-<commit>-<arch>.tar.gz
```

安装到一个**新的空版本目录**：

```bash
bash scopex-offline-<commit>-<arch>/scripts/install_offline_bundle.sh \
  scopex-offline-<commit>-<arch> \
  "$HOME/scopex-releases/<commit>"
```

导出脚本已经把同版本的 `install_offline_bundle.sh` 放进 bundle，因此现场不需要另外从 GitHub 获取安装脚本。

安装过程会：

1. 校验 bundle 内 `SHA256SUMS`；
2. 校验 CPU architecture；
3. 解压对应 commit 的源码；
4. 放入预构建 `frontend/dist`；
5. 创建 `.venv`；
6. 使用 `--no-index --find-links` 从 wheelhouse 离线安装 host Python 依赖；
7. `docker load` 导入 analysis sandbox image。

如果目标目录非空，安装脚本默认拒绝覆盖，避免破坏上一版本。

### 12.1 当前脚本调用说明

仓库中的 shell 文件通过 GitHub Contents API 创建时可能没有 executable bit，因此统一使用：

```bash
bash scripts/export_offline_bundle.sh
bash scripts/install_offline_bundle.sh ...
```

不要依赖 `./script.sh` 是否可执行。

---

## 13. 版本升级与回滚

推荐版本目录：

```text
~/scopex-releases/
├── <commit-A>/
├── <commit-B>/
└── current -> <commit-B>
```

Sandbox image 使用不可覆盖的版本 tag，例如：

```text
scopex-sandbox-analysis:step7-<commit>
```

升级前：

1. 保留上一版本代码目录；
2. 保留上一 sandbox image tag；
3. 不删除 `.local/runtime-api/tasks` 审计数据；
4. 新版本单独做 health + smoke；
5. 再切换 systemd `WorkingDirectory` / `current` symlink。

回滚只需要：

- 切回旧代码目录；
- 使用旧 sandbox image tag；
- restart user service。

不要在升级时自动删除旧镜像和旧审计。

---

## 14. 离线部署仍需注意的边界

### 14.1 OpenClaw

当前 ScopeX bundle 不自动复制 `~/.openclaw`，避免误打包 token、配置或其他本地敏感数据。设备基础环境必须先准备已验证 OpenClaw CLI。

### 14.2 vLLM / 模型

ScopeX 只检查 `/v1/models`；不会安装或下载模型。完全离线现场需要提前准备 vLLM 和模型权重。

### 14.3 前端 lockfile

当前 `frontend/package.json` 已固定直接依赖版本，但仓库尚无 lockfile。这不影响已经预构建好的 `frontend/dist` 离线部署，但影响“从源码完全可复现重建 UI”。应在后续版本补 lockfile。

### 14.4 Open3D

ARM64 发行版不一定提供 `python3-open3d`。当前为可选能力，必须以 `/opt/scopex/toolbox.json` 实际结果为准。

### 14.5 磁盘

现场需要预留：

- 模型权重；
- vLLM runtime；
- Docker images/layers；
- ScopeX audit；
- task scratch；
- 业务数据。

不要只按照 ScopeX 源码大小估算磁盘。

---

## 15. 新机器最小验收

从 0 到 1 完成后至少验证：

```text
[ ] Docker 可用
[ ] OpenClaw CLI 可用
[ ] vLLM /v1/models 正确
[ ] scopex-sandbox-analysis:step7 已导入
[ ] /opt/scopex/toolbox.json 可读
[ ] Python unit suite 通过
[ ] frontend/dist 存在
[ ] /health 正常
[ ] 创建一个只读诊断 Task 能完成
[ ] 单图明确范围任务不会读取用户排除的数据
[ ] result.json / claims.json / answer.json / final.txt 可追溯
[ ] systemd Restart=on-failure 生效
[ ] 上一版本仍可回滚
```

只有代码存在不算完成部署；需要这条实际运行链闭环。
