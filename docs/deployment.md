# 端侧部署与更新

本文是 `deploy/edge/` Docker Compose 方案的操作入口。ScopeX 与 vLLM 均运行在容器中，OpenClaw 随 ScopeX Runtime 镜像安装。旧的 [宿主机部署方案](09-zero-to-one-build-and-offline-deployment.md) 仅供基础环境、模型准备与历史方案参考，不要混用两套启动命令。

**已确认：不自动重启。** 两个服务保留 `restart: "no"`；设备重启或服务退出后，由操作人员执行本文的启动步骤。

## 1. 先选择操作场景

| 场景 | 执行顺序 |
| --- | --- |
| 新设备首次安装 | 第 2 节准备基础环境 → 第 3 节制作并传输 → 第 4 节配置并安装 |
| 已安装设备日常启动、停止 | 第 5 节 |
| 更新 Python、前端或 Skill | 第 6 节；不需要重复传模型或制作镜像 |
| 修改设备路径、模型参数或任务额度 | 第 7 节 |
| 更新 Python 依赖、OpenClaw 或 Sandbox 工具依赖 | 第 8 节；需要重新制作对应镜像 |

命令示例使用设备 `scaling@10.200.0.7`、安装根目录 `/opt/ScalingRobotics/scopex`。替换设备地址时，后续所有命令保持一致。设备端使用有 Docker 权限的普通用户执行，不用 `sudo` 启动 ScopeX。

目录用途：

```text
/opt/ScalingRobotics/scopex/
├── app/       仓库代码、Skill、frontend/dist；代码同步只覆盖这里
├── config/    edge.env；设备配置，更新代码时保留
├── data/      任务历史、工作区、OpenClaw HOME；更新代码时保留
├── images/    离线镜像包
└── model/     模型权重；普通代码更新不传输
```

## 2. 首次安装前：准备设备基础环境

**执行位置：目标设备。仅新设备或基础环境缺失时执行。**

安装脚本不负责准备以下内容，须先具备：

- Docker Engine、Compose 插件、NVIDIA 驱动及容器 GPU 支持，当前普通用户可执行 Docker。
- `bash`、`ip`、`curl`、`python3`、`rsync`、`gzip`、`sha256sum`。
- VPN 网卡已连接，默认 `wg0`，具有 IPv4 地址。
- vLLM 镜像 `nvcr.io/nvidia/vllm:26.08-py3` 已导入。
- 完整模型目录 `/opt/ScalingRobotics/scopex/model/Qwen3.8-27B-NVFP4`，包含 `config.json` 和权重。
- 业务日志和图片目录已存在，执行用户有读取权限。
- 安装根目录可由执行用户写入。

ScopeX 镜像包**不包含 vLLM 镜像与模型权重**。已有设备无需重复准备；新设备应单独传输、导入并验证。镜像构建机与设备架构必须一致，不能直接把默认构建的其他架构镜像拿来使用。

## 3. 首次安装：制作并传输代码和镜像

### 3.1 制作部署产物

**执行位置：有网络、Docker 和 Node/npm 的准备机，仓库根目录。**

构建 Sandbox 时需要已有 `scopex-sandbox-analysis:step7`，或者 `scopex-sandbox-base:step6f` 基础镜像；脚本不会从零制作该基础镜像。

```bash
cd /path/to/scopex
npm --prefix frontend install
npm --prefix frontend run build
./deploy/edge/build-images.sh
./deploy/edge/export-images.sh scopex-edge-images
```

**当前脚本兼容步骤：** 导出脚本的校验清单包含输出目录前缀，导入脚本则在包目录内校验。传输前重新生成包内相对路径清单，否则按默认命令导入会失败：

```bash
(cd scopex-edge-images && sha256sum runtime.tar.gz sandbox.tar.gz > SHA256SUMS)
```

### 3.2 传输到设备

**执行位置：准备机，同一仓库根目录。**

```bash
ssh scaling@10.200.0.7 'mkdir -p /opt/ScalingRobotics/scopex/app /opt/ScalingRobotics/scopex/images'
rsync -az --delete \
  --exclude .git/ --exclude .local/ --exclude .venv/ --exclude __pycache__/ \
  --exclude frontend/node_modules/ --exclude /scopex-edge-images/ --exclude /dist/ \
  ./ scaling@10.200.0.7:/opt/ScalingRobotics/scopex/app/
rsync -az scopex-edge-images/ scaling@10.200.0.7:/opt/ScalingRobotics/scopex/images/
```

## 4. 首次安装：先配置，再导入和启动

**执行位置：目标设备。**

```bash
ssh scaling@10.200.0.7
cd /opt/ScalingRobotics/scopex/app
mkdir -p /opt/ScalingRobotics/scopex/config
if [ ! -f /opt/ScalingRobotics/scopex/config/edge.env ]; then
  cp deploy/edge/edge.env.example /opt/ScalingRobotics/scopex/config/edge.env
fi
chmod 600 /opt/ScalingRobotics/scopex/config/edge.env
vi /opt/ScalingRobotics/scopex/config/edge.env
```

按第 7 节检查设备路径和网卡。即使采用默认值，也先确认对应目录和镜像已存在，再执行：

```bash
./deploy/edge/install.sh /opt/ScalingRobotics/scopex/images
```

`install.sh` 顺序执行：校验镜像包 → 导入 Runtime 与 Sandbox 镜像 → 调用 `start.sh`。`start.sh` 检查目录、镜像、网卡，创建数据目录，启动服务并等待健康检查。**安装命令已经启动服务，不需要再执行一次启动。**

成功后输出访问地址，例如 `http://10.200.0.7:8787`。打开页面并执行一个有已知数据的任务，确认能访问业务数据、返回结果并查看历史。健康检查通过只代表服务可用，不代表业务验收完成。

## 5. 日常启动、停止和排障

**执行位置：目标设备。每次只执行所需操作。**

启动（包含设备重启后的人工启动）：

```bash
cd /opt/ScalingRobotics/scopex/app
./deploy/edge/start.sh
```

停止（等待正在执行的任务结束后操作；停止两个服务，保留数据）：

```bash
cd /opt/ScalingRobotics/scopex/app
./deploy/edge/stop.sh
```

查看容器状态与日志：

```bash
cd /opt/ScalingRobotics/scopex/app
set -a
. /opt/ScalingRobotics/scopex/config/edge.env
set +a
export SCOPEX_VPN_IP="$(ip -4 -o addr show dev "$SCOPEX_VPN_INTERFACE" scope global | awk 'NR==1 {split($4,a,"/"); print a[1]}')"
export SCOPEX_UID="$(id -u)" SCOPEX_GID="$(id -g)" SCOPEX_DOCKER_GID="$(stat -c %g /var/run/docker.sock)"
docker compose --env-file /opt/ScalingRobotics/scopex/config/edge.env --env-file /opt/ScalingRobotics/scopex/config/edge.runtime.env -f deploy/edge/compose.yaml ps
docker compose --env-file /opt/ScalingRobotics/scopex/config/edge.env --env-file /opt/ScalingRobotics/scopex/config/edge.runtime.env -f deploy/edge/compose.yaml logs --tail 100 scopex vllm
```

## 6. 后续更新：Python、前端和 Skill

**不重复首次安装，不传模型，不重新构建 Runtime 镜像。** 如果依赖发生变化，改走第 8 节。

当前 `update.sh` 只执行 `compose up -d`，挂载代码变化不保证重启 Python 进程。因此当前更新流程先停服务，更新后再启动，避免旧进程继续运行。此流程会短暂停止 vLLM。

1. **准备机：** 构建前端（前端依赖变化时先执行 `npm --prefix frontend install`）。

   ```bash
   cd /path/to/scopex
   npm --prefix frontend run build
   ```

2. **目标设备：** 确认没有运行或排队任务，在维护期间不要提交新任务；若有定时任务，先在页面暂停，记录需要恢复的任务。然后停止服务。

   ```bash
   cd /opt/ScalingRobotics/scopex/app
   ./deploy/edge/update.sh --check-only
   ./deploy/edge/stop.sh
   ```

3. **准备机：** 仅同步代码。当前流程使用下面的 `rsync`，避免 `sync.sh` 提前启动 ScopeX。

   ```bash
   cd /path/to/scopex
   rsync -az --delete \
     --exclude .git/ --exclude .local/ --exclude .venv/ --exclude __pycache__/ \
     --exclude frontend/node_modules/ --exclude /scopex-edge-images/ --exclude /dist/ \
     ./ scaling@10.200.0.7:/opt/ScalingRobotics/scopex/app/
   ```

4. **目标设备：** 启动两个服务，验证页面、业务任务与原有历史，再恢复先前暂停的定时任务。

   ```bash
   cd /opt/ScalingRobotics/scopex/app
   ./deploy/edge/start.sh
   ```

代码同步会覆盖 `app/` 中受同步影响的文件。设备上直接修改的 Compose 或 Catalog 要先备份并合入准备机版本，避免被覆盖。`config/edge.env`、`data/` 和 `model/` 在代码同步目录之外，保留原内容。

## 7. 修改配置：改哪里、如何生效

**执行位置：目标设备。首次安装在启动前修改；已运行设备先安排维护窗口。**

主配置：`/opt/ScalingRobotics/scopex/config/edge.env`。

| 配置项 | 默认值 / 用途 |
| --- | --- |
| `SCOPEX_MODEL_DIR` | `/opt/ScalingRobotics/scopex/model/Qwen3.8-27B-NVFP4` |
| `SCOPEX_VPN_INTERFACE` | `wg0`；启动脚本读取其 IPv4 |
| `COWDISINFECT_LOG_DIR` | `/opt/ScalingRobotics/CowDisinfect/Log` |
| `LEFT_CAMERA_DIR` | `/opt/ScalingRobotics/CowDisinfect/GrabbedImages/LeftCamera` |
| `SCOPEX_IMAGE_LIMIT` | `12`；同时传给 vLLM 和 ScopeX |
| `VLLM_IMAGE` | `nvcr.io/nvidia/vllm:26.08-py3` |
| `SCOPEX_RUNTIME_IMAGE` | `scopex-runtime:2026.09.15` |
| `SCOPEX_SANDBOX_IMAGE` | `scopex-sandbox-analysis:2026.09.15` |
| `SCOPEX_API_KEY` | 默认空；这是 ScopeX 请求模型的凭据，填写不会自动为 vLLM 开启鉴权 |

**修改日志或图片路径时，还必须同步修改 `app/config/data-catalog.json` 中对应数据源的 `host_path`。** 当前没有自动联动。`agent_path` 保持原值；数据时间戳时区在对应 `layout.timezone` 配置，应依据数据来源确认。

模型和任务参数位于 `app/deploy/edge/compose.yaml`：

- `vllm.command`：模型上下文、并发、显存、KV Cache 等。
- `scopex.command`：任务超时、请求次数、并发、排队等。

已运行设备修改配置后，确认没有活动任务，执行停止和启动，确保包括 Catalog 在内的配置被重新加载：

```bash
cd /opt/ScalingRobotics/scopex/app
./deploy/edge/stop.sh
./deploy/edge/start.sh
```

不要直接修改安装根目录、APP/DATA 路径来搬迁已有安装：脚本和容器 HOME 存在默认路径约定，迁移不属于普通配置更新。

## 8. 后续更新：镜像或依赖变化

当 `requirements-api.txt`、Runtime Dockerfile、OpenClaw 版本或 Sandbox 镜像依赖变化时：

1. **准备机：** 使用与该镜像对应的代码，重新执行第 3.1 节构建、导出及清单修正。Sandbox 若需重新构建，不能复用旧 `step7` 镜像；使用 `SCOPEX_SANDBOX_SOURCE_IMAGE` 指向预先构建的新镜像，或指向不存在的标签以走基础镜像构建路径。
2. **目标设备：** 按第 6 节要求暂停定时任务、等待任务结束并停止服务。
3. **准备机：** 按第 3.2 节传输代码和镜像；先保留设备自定义的 Compose/Catalog 配置。
4. **目标设备：** 按第 4 节执行 `install.sh` 导入并启动；已有 `edge.env` 不会被模板覆盖。镜像标签变化时，先更新 `edge.env`。
5. **目标设备：** 验证业务任务及历史，再恢复定时任务。

vLLM 镜像和模型升级需要单独准备、验证；不包含在 Runtime/Sandbox 镜像包中。

## 9. 数据保留

不要执行 `docker compose down -v`，不要对 `/opt/ScalingRobotics/scopex/` 整体使用 `rsync --delete`。代码同步的删除范围仅限 `app/`。停止、启动及导入镜像无需删除任务历史或工作区。
