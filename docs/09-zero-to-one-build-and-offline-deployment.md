# ScopeX 从 0 到 1 构建与离线部署

更新：2026-09-14 阶段收口。适用 NVIDIA DGX Spark / Linux ARM64。命令在宿主机普通用户终端执行，不在 Agent 任务里执行。本文件包含现场已确认的配置和待验证的切换步骤，**命令存在不等于已在现场执行成功**。

> OpenClaw owns execution. ScopeX owns product control and trust.

## 1. 部署分层与责任

```text
Device Base Package（低频）
  DGX OS / NVIDIA driver / Docker / NVIDIA Container Toolkit
  OpenClaw 固定安装资产 / vLLM 镜像 / 固定模型权重

ScopeX Update Bundle（高频）
  固定 commit 源码 / frontend dist / ARM64 Python wheelhouse
  analysis sandbox / Skills / manifest / SHA256SUMS
```

ScopeX Runtime 是宿主机普通用户进程，调用本地 Docker daemon 运行隔离工具；不使用 Docker-in-Docker。模型不随每次业务脚本更新重复传输。没有在 ScopeX 仓库里维护通用离线 Docker/驱动安装器，基础软件须在设备制备阶段准备并验证。

## 2. 基础环境检查（必须在目标设备执行）

```bash
uname -m
python3 --version
docker version
docker ps
nvidia-smi
nvidia-ctk --version
/home/yanlan/.openclaw/bin/openclaw --version
```

目标架构应为 aarch64。Docker/NVIDIA 已可运行时不重装、不重启整个 Docker 服务。没有普通用户 Docker 权限时，按现场安全策略配置 docker 用户组并重新登录；不要用 sudo 启动 ScopeX。

Toolkit 已安装但尚未配置 GPU runtime 的新设备才使用：

```bash
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

此操作会影响宿主机其他容器，应在维护窗口执行。验证 GPU 可复用现有已载入镜像，不要求临时联网：

```bash
docker run --rm --pull never --gpus all \
  --entrypoint nvidia-smi nvcr.io/nvidia/vllm:26.08-py3
```

OpenClaw 精确版本必须从现机记录；不要自动升级。其安装文件/依赖由 Device Base 保存，但不能复制带 token 的整个 `~/.openclaw` 作为公开交付包。

## 3. 现机模型事实（用户 inspect 已确认）

```text
MODEL_REPO=unsloth/Qwen3.8-27B-NVFP4
MODEL_REVISION=f0b7c9e722f5565102fff8481c99e4d86ae099c7
SERVED_MODEL_NAME=qwen3.8-27b-nvfp4
VLLM_IMAGE=nvcr.io/nvidia/vllm:26.08-py3
local image ID=sha256:20b5b6d2f4709f6a72aa954b87bf46f806462314c81df095f9d20252683158b4
MODEL_ROOT=/home/yanlan/.cache/huggingface/hub/models--unsloth--Qwen3.8-27B-NVFP4
container model=/hfmodel/snapshots/f0b7c9e722f5565102fff8481c99e4d86ae099c7
```

本地 image ID 不是 registry manifest digest；离线复制现机镜像后应核对 ID。没有验证的 `vllm/vllm-openai` 或其他 tag 只是替代候选，不要替换现机 NGC 镜像。

served id 是 API 名称，不是下载 repo。换模型需重新验证工具调用、多轮/图片、32768 上下文、资源预算和真实业务，不能只凭名称接近就替换。

## 4. 模型下载与离线复制

联网准备机创建下载工具环境：

```bash
python3 -m venv "$HOME/model-tools"
"$HOME/model-tools/bin/python" -m pip install \
  -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple huggingface_hub
export MODEL_REPO='unsloth/Qwen3.8-27B-NVFP4'
export MODEL_REVISION='f0b7c9e722f5565102fff8481c99e4d86ae099c7'
export MODEL_DIR="$HOME/models/qwen3.8-27b-nvfp4-$MODEL_REVISION"
"$HOME/model-tools/bin/hf" download "$MODEL_REPO" \
  --revision "$MODEL_REVISION" --local-dir "$MODEL_DIR"
```

此下载目录是平铺模型目录，容器挂载为 `/models/model:ro` 后对应 `vllm serve /models/model`，不同于现机 `/hfmodel/snapshots/...` 缓存布局。不能把两种目录混用。

已有现机缓存不需重新下载。复制缓存必须同时保留 `blobs`、`snapshots` 和内部符号链接；只复制 snapshots 的链接可能得到失效权重。另一选择是先生成完整平铺目录再传输。模型和镜像都必须使用固定版本并核对，不自动选择 latest。

平铺目录校验清单（排除清单自身和下载缓存）：

```bash
(
  cd "$MODEL_DIR"
  find . -type f ! -path './.cache/*' ! -name MODEL_SHA256SUMS -print0 \
    | sort -z | xargs -0 -r sha256sum > MODEL_SHA256SUMS
)
# 复制到设备后，在对应目录执行：
(cd "$MODEL_DIR" && sha256sum -c MODEL_SHA256SUMS)
```

不要把 API key、HF token、私有环境变量写入清单或 Git。

## 5. 镜像搬运与清华源

在已验证且同架构的联网机器准备：

```bash
docker save nvcr.io/nvidia/vllm:26.08-py3 | gzip -1 > vllm-image.tar.gz
sha256sum vllm-image.tar.gz > vllm-image.tar.gz.sha256
```

目标机同目录校验与导入：

```bash
sha256sum -c vllm-image.tar.gz.sha256
gzip -dc vllm-image.tar.gz | docker load
docker image inspect nvcr.io/nvidia/vllm:26.08-py3
```

ScopeX analysis Sandbox 构建 APT/PyPI 使用清华 TUNA；Ubuntu ARM64 使用 ubuntu-ports。仅修改容器构建环境，不替换宿主机安全更新策略。Docker 镜像来源没有因为清华 PyPI/APT 配置就自动可用，弱网环境需提前 save/load。

## 6. vLLM 参数基线和完整启动示例

最后已确认的容器：`scaling-scope-vllm-nvfp4`，网络 `deploy_default`，IPC private，共享内存 8 GiB，入口 `/opt/nvidia/nvidia_entrypoint.sh`，重启策略 no，loopback `18002 -> 8000`。

最后已确认图片数是 **4**。12 张版本的切换命令已经提供，但没有现场成功回执。下面新建示例默认 4；需要 12 时显式修改 IMAGE_LIMIT，并在启动后通过容量探针。不要改变其他已经验证的参数。

**仅用于无同名容器的新建，或已经备份并处理旧容器的维护窗口**。已有容器先保存 `docker inspect` 和日志；该备份可能含环境密钥，只保存在本机受限目录。重建时还需继承现机额外环境和 network aliases；原 Compose 管理入口未补录前，不混用 `compose up` 与手工重建。

```bash
IMAGE_LIMIT=4
MODEL_ROOT='/home/yanlan/.cache/huggingface/hub/models--unsloth--Qwen3.8-27B-NVFP4'
REV='f0b7c9e722f5565102fff8481c99e4d86ae099c7'
IMAGE='sha256:20b5b6d2f4709f6a72aa954b87bf46f806462314c81df095f9d20252683158b4'

test -r "$MODEL_ROOT/snapshots/$REV/config.json"
docker image inspect "$IMAGE" >/dev/null
docker network inspect deploy_default >/dev/null

docker run -d --name scaling-scope-vllm-nvfp4 \
  --pull never --restart no --runtime runc --gpus all \
  --network deploy_default --ipc private --shm-size 8g \
  --workdir /workspace --entrypoint /opt/nvidia/nvidia_entrypoint.sh \
  -p 127.0.0.1:18002:8000 \
  --mount "type=bind,src=$MODEL_ROOT,dst=/hfmodel,readonly" \
  "$IMAGE" \
  vllm serve "/hfmodel/snapshots/$REV" \
    --served-model-name qwen3.8-27b-nvfp4 \
    --host 0.0.0.0 --port 8000 \
    --max-model-len 32768 --max-num-seqs 1 \
    --gpu-memory-utilization 0.60 --kv-cache-memory-bytes 8G \
    --kv-cache-dtype bfloat16 --enable-prefix-caching \
    --reasoning-parser qwen3 --enable-auto-tool-choice \
    --tool-call-parser qwen3_coder \
    --limit-mm-per-prompt "{\"image\":{\"count\":$IMAGE_LIMIT,\"width\":1024,\"height\":1024},\"video\":0}" \
    --mm-processor-cache-gb 0.5

docker logs --tail 100 -f scaling-scope-vllm-nvfp4
```

新设备没有 deploy_default 时，应先确认业务网络再创建/配置网络；不要猜其他服务的连接关系。启动失败先保留日志，不删权重、不提高全部资源预算。

模型服务验证：

```bash
curl -fsS --max-time 10 http://127.0.0.1:18002/health
curl -fsS --max-time 10 http://127.0.0.1:18002/v1/models | python3 -m json.tool
```

`--model` 必须与返回的真实 id 一致。有 API 鉴权时健康探测按现机服务配置携带凭证，避免公开粘贴。

## 7. 图片容量必须跨层一致

`SCOPEX_MAX_IMAGES_PER_PROMPT` 同时控制 Runtime 提示、完整请求图片检查和 Fresh Finalizer 图片加载。默认 4，可显式设 1–12；每次 view_image 最多 2 张是另一条限制。多轮图片会累积，2+2+2 仍然是 6。

```bash
cd /home/yanlan/workspaces/code/scopex
IMAGE_LIMIT=4  # vLLM 已改 12 后才设为 12
.venv/bin/python scripts/check_model_image_capacity.py \
  --base-url http://127.0.0.1:18002/v1 \
  --model qwen3.8-27b-nvfp4 --images "$IMAGE_LIMIT" --timeout 180
```

必须 `accepted=true` 才开展图片业务测试。小图探针只验证数量/传输，不证明多张全分辨率原图能装进 32768 Context，也不证明视觉正确率。width/height 不能被当成输入已经统一缩放的保证；HTTP body、token、显存和 RAM 预算仍需实测。

验证通过后才单独决定是否开启自启动：

```bash
docker update --restart unless-stopped scaling-scope-vllm-nvfp4
```

容器改名备份不是修改原配置；仅 `docker restart` 也不能改变 Cmd。恢复旧容器时要把 ScopeX 图片额度同步改回旧值。不要为了释放资源执行全局 `docker system prune`。

## 8. 获取代码与宿主机依赖

```bash
cd /home/yanlan/workspaces/code/scopex
git status --short
# 有本地改动先保存，不使用 reset --hard / clean -fdx。
git fetch origin
git switch main
git pull --ff-only origin main
python3 -m venv .venv
.venv/bin/python -m pip install \
  -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple \
  -r requirements-api.txt
```

从零无仓库时先在目标父目录 clone `saaassin13/scopex`。仓库代码/前端可在准备机构建；Docker 架构和 wheelhouse 的 Python 小版本必须与设备匹配。

前端在线构建使用 Node >=22.18.0：

```bash
(cd frontend && npm install --no-audit --no-fund && npm run build)
```

当前还没有入库的 npm lockfile；本轮 CI 构建通过不能证明依赖完全锁定。现场离线使用预构建 dist，不 npm install。

## 9. Analysis Sandbox 与 Skill

```bash
docker build -f docker/sandbox-analysis.Dockerfile \
  -t scopex-sandbox-analysis:step7 .

docker run --rm --network none --entrypoint python3 \
  scopex-sandbox-analysis:step7 \
  -c 'import cv2,PIL,numpy,pandas,scipy,skimage,matplotlib,openpyxl,yaml,psutil,sklearn; print("ScopeX toolbox OK")'

docker run --rm --network none --entrypoint cat \
  scopex-sandbox-analysis:step7 /opt/scopex/toolbox.json
```

默认 BASE_IMAGE=scopex-sandbox-base:step6f；可通过 build arg 显式覆盖为已验证 base。Open3D 是否可用取决于实际 ARM64 镜像，不能假定所有点云工具已安装。

默认 Skills：data-locator、system-health、image-quality-diagnosis、nipple-recognition-analysis、encoder-health、log-context。启动 Runtime 时刷新 workspace 副本与 Locator references Catalog。仅修改 Python/Skill 不需要重新构建 Sandbox 包镜像，但必须重启 Runtime；修改 Vue 则重建 dist。

**不部署系统资源 timer，不保存资源历史。** 每个 Run 生成一次 `/scopex-host/current.json`，只读挂载；不能拿 Sandbox 状态代替 host。测试只写临时目录，不会 provision 真实 workspace。

## 10. 开发机 Runtime 完整命令

保留现有开发状态 `.local/runtime-api`，不要因为文档升级迁移或清空：

```bash
cd /home/yanlan/workspaces/code/scopex
IMAGE_LIMIT=4  # 使用已通过现机探针的容量
SCOPEX_MAX_IMAGES_PER_PROMPT="$IMAGE_LIMIT" \
.venv/bin/python scripts/runtime_api.py \
  --model qwen3.8-27b-nvfp4 \
  --base-url http://127.0.0.1:18002/v1 \
  --openclaw-bin /home/yanlan/.openclaw/bin/openclaw \
  --workspace /home/yanlan/workspaces/code/scopex/.local/workspace \
  --data-root /home/yanlan/workspaces/code/scopex/.local/runtime-api \
  --data-catalog /home/yanlan/workspaces/code/scopex/config/data-catalog.json \
  --sandbox-image scopex-sandbox-analysis:step7 \
  --data-dir /opt/ScalingRobotics/CowDisinfect/Log:/agent-data/logs \
  --data-dir /opt/ScalingRobotics/CowDisinfect/GrabbedImages/LeftCamera:/agent-data/left-camera \
  --web-dist /home/yanlan/workspaces/code/scopex/frontend/dist \
  --host 127.0.0.1 --port 8787 \
  --timeout 600 --max-requests 16 --max-tokens 2048 \
  --finalizer-max-tokens 768 --finalizer-timeout 180 --enable-view-image
```

显式两条 data-dir 与默认 Catalog 一致，是 fail-fast 目录校验；正常现场也可省略两条让 Catalog 自动挂载。不能用一个未知业务根覆盖 `/agent-data`。

另一个终端：

```bash
curl -fsS http://127.0.0.1:8787/health | python3 -m json.tool
ls -l .local/workspace/skills/data-locator/references/data-catalog.json
```

API 仅 loopback，远程使用既有 SSH 隧道，不暴露公网。不要同时启两份 Runtime 使用同一状态目录/端口。调查 turn 与 Finalizer/Report 有各自耗时，最终任务耗时需查看 Run 记录。

## 11. release / systemd 的持久化路径

新的部署模板明确分离：

```text
~/scopex-releases/<commit>/           # 不可变代码、venv、frontend
~/scopex-releases/current            # 版本链接
~/.local/share/scopex/workspace/      # 内置/自定义 Skill workspace
~/.local/share/scopex/runtime-api/    # tasks、work、scheduler、exports
~/.config/scopex/runtime.env          # 本机配置/密钥
```

这是修正旧模板把状态放在 current/.local、升级后历史和 Schedule 看似丢失的问题。**不自动迁移旧数据，也不覆盖开发机 `.local`。**

既有安装切换模板前：停 ScopeX；记录实际 --workspace/--data-root；完整备份旧目录；仅在新目标为空时复制；检查 tasks/work/scheduler/exports、权限、审计引用和自定义 Skill，再启新服务。历史 audit 可能含旧绝对路径，应保留旧目录只读备份直至验证，不能一复制就删除。禁止将两套已有状态直接混合。

新部署配置模板（不要覆盖已有 runtime.env）：

```bash
mkdir -p ~/.config/systemd/user ~/.config/scopex
cp deploy/systemd/scopex-runtime.service ~/.config/systemd/user/
if [ ! -e ~/.config/scopex/runtime.env ]; then
  cp deploy/systemd/runtime.env.example ~/.config/scopex/runtime.env
fi
chmod 600 ~/.config/scopex/runtime.env
# 人工确认模型、图片额度、current 目标及状态迁移后再执行：
systemctl --user daemon-reload
systemctl --user enable --now scopex-runtime.service
journalctl --user -u scopex-runtime.service -f
```

未登录也需开机启动时由管理员执行 `sudo loginctl enable-linger "$USER"`。环境文件中的图片额度必须与服务一致。该模板修改本身不改变正在运行的用户服务。

## 12. ScopeX 离线 Update Bundle

联网准备机使用已提交 clean worktree、当前前端 dist、匹配 ARM64/Python 的 wheelhouse 和 analysis 镜像：

```bash
bash scripts/export_offline_bundle.sh
```

输出 `dist/offline/scopex-offline-<short-commit>-<arch>/`、`.tar.gz` 和校验文件。包包含固定源码、前端 dist、Python wheels、Sandbox、manifest、SHA256SUMS、安装脚本；不含 OpenClaw、vLLM/模型及 secrets。输出使用新的路径，勿指向业务数据目录。

现场解压后安装脚本的真实接口：

```bash
# BUNDLE 指向已解压的本次离线包目录。
BUNDLE='/path/to/scopex-offline-<short-commit>-aarch64'
(cd "$BUNDLE" && sha256sum -c SHA256SUMS)
bash "$BUNDLE/scripts/install_offline_bundle.sh" "$BUNDLE"
```

安装器校验内容/架构，再用 pip --no-index --find-links 安装和 docker load；默认写入 `~/scopex-releases/<commit>`，拒绝非空目标。**不会自动切 current、不会自动业务 smoke/回滚。** `.tar.gz` 外层校验清单在搬运后应使用同目录相对文件名，避免联网准备机绝对路径造成误用。

先对候选 release 做测试/健康检查，再停旧服务切换 current。记录旧 `readlink -f current`；失败时切回旧 release 并恢复相应环境/镜像。不要只回滚代码却混用已改变的配置。运行数据固定目录不随代码替换。

## 13. Device Base 与交付检查

除 ScopeX 包外，设备出厂/离线初始化还需要：OS/驱动/Docker/Toolkit、OpenClaw 固定安装资产、vLLM 镜像和模型。manifest 记录架构、Python、OpenClaw版本、镜像 tag和ID、repo/revision、served id、Context和图片额度。清单里不放 token。

交付前检查：目标机GPU可用、模型health/models/容量、真实目录只读挂载、当前资源快照、业务新任务、原始文件未改、开始结束耗时、Report/评价/导出、重启不补历史任务、版本切换历史仍在、失败回滚。

现阶段完整 Device Base 离线装机、ARM64业务多图与恢复链未获全部现场 PASS。不要把仓库 CI 或小图探针记成此 Gate 通过。
