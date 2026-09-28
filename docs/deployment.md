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

当前 `update.sh` 执行 `compose up -d --no-deps --force-recreate scopex`，会重建 ScopeX Runtime，使挂载代码生效；不会启动或重建 vLLM。仅更新 Python、前端或 Skill，且 Compose、设备配置与镜像均未变化、vLLM 正在运行时，可以在完成维护前检查和同步后执行 `update.sh`。下列为完整停机更新流程：当模型/Compose 参数也有变化，或 vLLM 原本已停止时使用；会停止并重新启动两个服务。

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
| `SCOPEX_COLLECTION_MAX_BYTES` | `2147483648`；单包源数据内容最多 2 GiB（不含 ZIP/manifest 开销） |
| `SCOPEX_COLLECTION_MAX_FILES` | `5000`；单个任务原始数据包最多 5000 个文件 |
| `VLLM_IMAGE` | `nvcr.io/nvidia/vllm:26.08-py3` |
| `SCOPEX_RUNTIME_IMAGE` | `scopex-runtime:2026.09.15` |
| `SCOPEX_SANDBOX_IMAGE` | `scopex-sandbox-analysis:2026.09.15` |
| `SCOPEX_API_KEY` | 默认空；这是 ScopeX 请求模型的凭据，填写不会自动为 vLLM 开启鉴权 |
| `SCOPEX_MONITOR_ENABLED` 等 | 牛掉落监控相关变量与 `config/monitor.json`，见第 10 节 |

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

## 10. 监控服务（牛掉落，可选）

监控是独立的 compose 服务（`monitor` profile），**默认关闭**——不启用时部署行为与第 3/6 节完全一致。启用后拓扑：

```
相机 192.168.40.96 ──(仅端侧可路由)──> 端侧 10.200.0.14（ssh 隧道转发，零新增进程）
workstation 宿主 autossh -L 127.0.0.1:8554 -> 192.168.40.96:554
scopex-monitor 容器（host 网络）ffmpeg 拉 127.0.0.1:8554 → 检测 → 事件落 data/monitor/cow-fall/
scopex-runtime 容器 127.0.0.1:8790 控制监控；页面"常驻监控"启停/看事件/发起模型确认
```

### 10.1 镜像

`deploy/edge/build-images.sh` 会额外构建 `scopex-monitor`（`docker/monitor.Dockerfile`：分析沙箱镜像 + ffmpeg + onnxruntime），并在构建时验证 `cv2/numpy/onnxruntime` 同进程导入与 ffmpeg 可用。`export-images.sh` / `import-images.sh` 已包含 `monitor.tar.gz`。

**端侧网络与依赖来源**：镜像构建的 apt 与 pip 源已全部固定为清华 TUNA（无公网要求，与既有镜像构建一致；如需更换，改 Dockerfile 里的 mirror 变量为阿里源即可）。YOLO 模型 `yolov8n-coco.onnx` 是本地转换产物（POC 时期离线转换），**不从网络下载**——新设备部署时从现有设备的 `models-monitor/` 目录复制，首次安装前放到位即可。onnxruntime wheel（含 aarch64）由清华 PyPI 源提供。

已部署环境首次启用监控：只需在准备机导出镜像、传输后 `docker load < monitor.tar.gz`（其余镜像不变），不需要走第 8 节全量流程。

### 10.2 配置（全部走配置文件）

**`config/edge.env`**（第 7 节体系，0600）追加：

| 配置项 | 说明 |
| --- | --- |
| `SCOPEX_MONITOR_ENABLED` | `1` 启用；start.sh 据此加载 compose monitor profile 并做镜像/目录预检 |
| `SCOPEX_MONITOR_IMAGE` | 默认 `scopex-monitor:2026.09.26` |
| `SCOPEX_MONITOR_MODEL_DIR` | 默认 `/opt/ScalingRobotics/scopex/models-monitor`（内含 yolov8n-coco.onnx） |
| `COW_FALL_RTSP_URL` | 完整 RTSP 地址（含凭据）。凭据经 compose environment 注入，`docker inspect` 对 docker 组成员可见——与 `SCOPEX_API_KEY` 同一信任层级 |

**`config/monitor.json`**（首次启用时 start.sh 自动从 `deploy/edge/monitor.json.example` 拷贝，0600）：相机标识、`scopex_api_url`（**必须填本机 VPN IP 的 `http://<VPN_IP>:8787`，ScopeX 不监听 127.0.0.1**）、`auto_start`（容器启动即拉流）、`auto_confirm`（事件自动建模型确认任务，默认关）、看门狗/退避/保留策略。检测区域来自**监控标定页面**的当前启用版本（存储在 `data/runtime-api/calibrations/<camera>/`，`calibrations_root` 必须指向该目录），启动时合成；启用新版本后在页面"常驻监控"里点"重启以应用新标定"。

### 10.3 ssh 隧道常驻

workstation 上以 systemd 用户级服务维持隧道（模板 `deploy/systemd/cow-fall-tunnel.service`，普通 ssh + `Restart=always`，不需要 autossh 或 sudo）：

```bash
# 前置：已配置到 scaling@10.200.0.14 的 ssh 免密登录
mkdir -p ~/.config/systemd/user
cp app/deploy/systemd/cow-fall-tunnel.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now cow-fall-tunnel
loginctl enable-linger "$USER"   # 注销后隧道保持
```

隧道必须绑定回环（模板已写 `-L 127.0.0.1:8554:...`）。**不要写 `-L 8554:...` 或 `-L *:8554`**——那会把相机流暴露到 VPN 网段。

### 10.4 启用与验收

1. `config/edge.env` 置 `SCOPEX_MONITOR_ENABLED=1` 并填 `COW_FALL_RTSP_URL`；确认标定页面已有启用版本。
2. `./deploy/edge/start.sh`（重启全部服务）；`docker ps` 应见 `scopex-monitor`（healthy）。
3. 页面"常驻监控"：点启动 → 状态变"运行中"、画面延迟秒级、无重启计数增长。
4. **拔隧道演练**：`systemctl --user stop cow-fall-tunnel`，60 秒内监控页应出现"画面无新帧"并自动重启退避；`start` 隧道后监控自动恢复运行（退避序列 5/10/20/60s）。
5. 确认链路（建议 `auto_confirm` 先保持关，手动在事件上点"发确认"）：确认任务能读到 `/agent-data/cow-fall/events/<id>/` 的帧并给出结论（依赖 `config/data-catalog.json` 的 `cow_fall_events` 源与 start.sh 预建目录）。真实 1080p 帧 view_image 抽样 2~4 张在图片额度内。
6. 观察半天~一天无异常事件（正常场景零误报为基线，见 docs/monitoring/01-cow-fall-monitor.md）。

事件档案默认保留 30 天且至少 50 个（`monitor.json` 可调）；监控日志 `docker logs scopex-monitor`（10MB×3 轮转）。

### 编码器与上下文修复的部署验收（2026-09-15）

本次涉及 Python、Skill 和 Compose，按普通代码更新流程同步并重新启动 ScopeX，
无需因本次修改重新制作镜像。Compose 已显式开启 `--enable-compaction`，运行时同时
启用 `agents.defaults.compaction.midTurnPrecheck.enabled=true`，保留不自动重启的部署策略。
启动日志应显示 `compaction: enabled`，新任务生成的 OpenClaw 配置应同时包含
`agents.defaults.compaction.enabled=true`、`agents.defaults.compaction.midTurnPrecheck.enabled=true`
和 `agents.defaults.compaction.keepRecentTokens=8192`。该值为有界近期上下文保留量，
为工具结果和原生 overflow recovery 留出工作区；不扩大 vLLM 的模型上下文窗口。

### 上下文窗口扩容（2026-09-24）

OpenClaw（含 2026.9.6）对 compaction reserve 有硬编码下限 `2e4` tokens且已将该调优项
退役，mid-turn prompt 预算恒等于 `max_model_len - 20000`。在 32768 窗口下预算仅 12768
tokens，多步工具任务（如编码器全窗口分析）必然溢出且压缩无法恢复。当日先把 vLLM
`--max-model-len` 与 ScopeX `--context-window` 提升至 65536（prompt 预算 45536），
经全窗口任务实测后最终定为 98304（见下方"最终预算定型"）；现网 KV cache（8 GiB）
容量实测 114k–124k tokens，两档扩容均不增加显存。验收时确认 vLLM 启动日志
`max_model_len=98304`，且新任务 wire 审计中 `context_budget.context_window=98304`。

同日补充：OpenClaw 原生 compaction 的嵌入式安全超时默认 180 秒，低于本地 vLLM 单并发
~7.7 tokens/s 吞吐下一次完整 summarization 生成（约 270 秒），导致 65536 窗口下首次
真实压缩在中途被断开（BrokenPipe 后报 "Compaction timed out"）。生成的 OpenClaw 配置
现显式设置 `agents.defaults.compaction.timeoutSeconds=600`。同时 encoder-health 的
inspect 默认页密度收紧（trace 4 行、控制日志上下文各 3 条，`--offset`/`--limit`/
`--context-ms` 分页语义不变），单次输出约减半，16 候选全窗口分析在 45536 预算内
无需压缩即可完成。上述两项生效后，完整窗口任务继续暴露 ScopeX 自身
`--max-requests 16` 上限不足（16 候选 inspect 加 report 分页与最终回答约需 23–25 次模型
调用，实测第 17 次请求被 ModelProxy 以 422 拒绝、状态正确落 FAILED+unavailable）；
edge `--max-requests` 提升至 32，factory 校验范围同步放宽到 2–64。

随后修复请求预算估算的双重计费：`view_image` 之后的请求把 base64 图片数据同时按
文本字符上界与图片 token 预留计费（task-d711f848cb25 wire-08 实测 20.6k 文本 + 两张
data URI 被估为 212k tokens 而本地拒绝）。估算方法升级为 `utf8-json-upper-bound-v2`：
已识别并计入 `image_tokens` 的 image_url 数据 URI 字符不再参与文本上界，同一 payload
复算为 23k tokens、正常放行；短 URL 与图片预留语义不变。

最终预算定型（task-f6f94604aaf6 实测后）：全窗口编码器任务真实需求为 46k prompt、
~28 次模型调用（含 view_image 与 report 分页）、约 900 秒；65536/32/1200s 的配置在
第 33 次调用处以 478 tokens 之差触发 precheck 溢出，且 compaction 恢复请求被 32 次
上限拒绝。edge 最终值：vLLM 与 ScopeX 窗口 98304（prompt 预算 78304；模型原生
262144、KV cache 约 121k tokens）、`--max-requests 48`、turn `--timeout 1800`（含一次
600 秒 compaction 的余量）、`stop_grace_period 1820s`；factory 的 timeout_s 校验上限
同步放宽到 1800。

复查固定时间窗 `2026-09-15 17:56:13` 至 `2026-09-15 18:26:13`，确认完整交付，
并保留归零、瞬时回归及采样缺口的区别。另验证一次足够长的上下文确实触发原生
压缩且保留证据；仅看到开关启用或短任务成功不代表压缩链路已验收。


### 临时下载原始数据

任务结束后在详情页“原始数据”选择范围，点击收集、下载或重新收集。
照片变化时提供当前文件并提示；缺失、读取失败或超限时尽量提供部分包，页面及
`manifest.json` 会提示未完整收集。全部不可用或收集失败时保留旧包。
时间范围日志按源文件存放于 `logs/window/`，逐行截取，不包含范围外记录。
编码器任务的 `working_plots` 模式把 plot 生成的 working-derived PNG（总览与候选
局部图）收进 `plots/`，manifest 标注为工作态派生可视化；它们不改变结构化数值
结论的地位。同一任务收集中暂不能删除或重复收集；结束后删除任务会删除包，
不删除外部原始文件。

### 2026-09-26 批量检查与请求上限

task-5bbe39c61fa1（16 候选、无 profile）以逐候选 inspect 跑满 48 次请求上限，
第 49 次（生成最终回答）被 ModelProxy 422 拒绝，任务 FAILED（native_answer_unavailable），
分析结果完整留在快照中。修复：encoder-health SKILL 强制 4+ 候选单次批量 inspect
（同快照实测 16 候选 0.93 秒、结论与逐个一致）；edge `--max-requests` 提升至 64
（factory 校验范围 2–64 未变）。预期同类任务模型请求降至 ~10 次，不再触发
中途压缩。

### 2026-09-26 监控服务（牛掉落）部署验收

workstation（10.200.0.2，arm64/Debian bookworm）首次启用 monitor profile。
构建与部署踩到两处并已修复：镜像内无 pip（Dockerfile 补装 python3-pip；
onnxruntime 1.20.1 aarch64 wheel 可用）；edge.env 中 RTSP URL 含 `&`，赋值
必须单引号（否则 source 拆断，start.sh 报 requires COW_FALL_RTSP_URL）。

验收结果（全部通过）：

- 三容器 healthy（vllm / runtime / monitor），监控 auto_start 即时拉流，
  实时帧龄亚秒级；容器内 45 秒真实录像回放零事件正常退出（真实模型链路）。
- **拔隧道演练**：`systemctl --user stop cow-fall-tunnel` 后帧龄按秒增长，
  60s 看门狗触发重启并进入退避（5/10/20s 阶梯）；隧道恢复后 20 秒内自动
  回到 running，容器全程 healthy。
- **确认任务全链路**（经 catalog `cow_fall_events` 源读 `/agent-data/cow-fall/`）：
  合成事件与真实事件各验证一次，模型 view_image 抽查 2~4 张代表帧并给出
  结论与依据帧名。首个真实事件 fall-1（19:03，R1 检测框中心越界，121 帧）
  确认结论"明显误报"（牛被防护罩遮挡导致检测框偏移）——检测宁多勿漏、
  确认层过滤的工作方式符合设计。
- 部署期发现并修复一处代码缺陷：`auto_confirm` 关闭时确认 worker 未启动，
  页面手动"发确认"会被静默丢弃（提交 4b 补丁，worker 无条件启动）。

当前配置：`auto_start=true`、`auto_confirm=false`（事件确认走页面手动）；
zones 使用 `docs/monitoring/zones-camera-96-v1.json` 兜底（标定页面启用版本
前优先级更高）。真实掉落召回仍待现场样本。

### 2026-09-26 fall-1 事件复盘与标定链路修正

fall-1（19:03，121 帧）人工核对帧档案修正结论：当时为**夜间红外模式、转盘无牛**，
走道右下部堆放着白色覆盖物与拖地软管（真实异物）；监控 19:01 刚启动、MOG2 背景
仍在学习期，静止异物被计为持续前景（主判据），夜视下 YOLO 将白色覆盖物误检为牛
（R1）。确认层判"明显误报"正确；"走道应无物"的检测语义也成立——但该异物长期
存在，收敛后不再触发；监控每次重启的学习期（约 1~2 分钟）对走道静止异物可能
再报一次，确认忽略即可。

标定链路修正三处：① 标定页面存储实际在 `data/runtime-api/calibrations/`，
monitor.json 的 `calibrations_root` 与 start.sh 预建目录已统一到该路径（此前误配
`data/calibrations`，页面标定 daemon 读不到）；② 标定页面版本历史新增"查看"，
可将已保存版本（zones+参数+参考帧）载入画布续改；③ 手工兜底 zones
（9/23 时期，禁区右侧凸起误罩转盘台面 29% 重叠，即 fall-1 前段分析的病灶之一）
已由用户在页面导入为 camera-96 v1，daemon 与页面版本已对齐
（running == active == v1）。重标时重点：禁区右段沿转盘台面前缘走线，勿上凸。

### 10.5 监控日志与排障速查

明天回看时按下面查（workstation 上执行）。注意 `docker logs -t` 的时间戳是
**UTC，加 8 小时即北京时间**；journalctl 与页面时间均为本地时间。

```bash
# 1. 监控容器日志：启停、重启、看门狗、事件触发、确认（10MB×3 轮转）
docker logs -t --since 24h scopex-monitor | less
docker logs -t scopex-monitor 2>&1 | grep -E "event|watchdog|frame age|launch failed"

# 2. 隧道日志：断线与自动重连记录
journalctl --user -u cow-fall-tunnel --since today --no-pager

# 3. 事件档案（每事件 meta.json + 前后各 30 秒帧；30 天自动清理）
ls -lt /opt/ScalingRobotics/scopex/data/monitor/cow-fall/events/ | head
python3 -m json.tool /opt/ScalingRobotics/scopex/data/monitor/cow-fall/events/fall-*/meta.json

# 4. 页面（VPN 内 http://10.200.0.2:8787）
#    常驻监控：运行状态/帧龄/重启计数/事件列表与帧图；确认任务在"临时任务"历史里
```

对照关系：`[supervisor] session started (calibration vN)` = 换标定重启；
`frame age ... exceeds watchdog` = 断流看门狗触发（通常伴随隧道日志断线）；
`[event] fall-N rules=[...]` = 事件触发，对应事件目录与页面条目；
`[confirm] fall-N -> task-xxx` = 已建确认任务。

### 2026-09-28 任务结果页面与异常复核规则更新

代码 `8c6a8f9` 已部署到 `yanlan@10.200.0.2` 的原仓库 `/opt/ScalingRobotics/scopex/app`。现有仓库切到 `feature/video-inference-validation`，原 main 引用与三处现场前端差异保留；没有建立第二个仓库或服务。目标 Linux 全量 831 项通过，已有 Runtime 镜像的 Node 24.21.0 构建前端通过，部署后 Runtime 内新增专项 10 项通过。

本次按当前 `update.sh` 的 `up -d --no-deps --force-recreate scopex` 更新，仅重建 ScopeX Runtime；第 6 节关于脚本不强制重启的旧描述不适用于当前脚本。新 API、前端资源哈希和历史记录核验通过，29 条任务 ID/状态保留，配置校验一致，vLLM 与 monitor 容器未重建。回滚包及验收回执位于 `/opt/ScalingRobotics/scopex/deploy-backups/20260928-8c6a8f9/`。业务识别准确率边界见交接第 7 节。

### 2026-09-28 scaling@10.200.0.7 更新与启动回执

检查时目标仍为原 `c6d36a2` 对应应用，两个容器自 9 月 17 日起已停止，新版相机连续性工具不存在。用户此前已授权部署，本轮按第 6 节完整流程和第 7 节配置变更步骤完成：备份 → 暂停计划 → `update.sh --check-only` 与 `stop.sh` → 只同步 `app/` → `start.sh` → 数据/业务/页面验收 → 恢复计划。没有重复首次安装或混用旧 systemd 启动方式。

- 发布代码 `8c6a8f9`，文档基线 `086e53f`，另同步本对话的交接文档更新；336 个发布文件在同步后逐项 SHA-256 匹配。目标不含 `.git`，版本以发布清单和回执为证，不伪造目标 Git HEAD。
- 复用已安装 Runtime、Sandbox、vLLM 镜像和现有模型，依赖探针通过；本机前端构建通过。目标实际 Runtime 中 38 项回归、分析 Sandbox 中 70 项编码器回归通过。之前同版代码的 Linux 831 项全量结果保留原范围，不冒称本机重新执行了 831 项。
- 启动后 ScopeX 可访问、vLLM healthy。采用当前 Compose 的 `98304` 上下文、`64` 请求、`1800s` 调查预算，两个服务 `restart=no`。设备路径、模型权重和已有镜像不变。仅为兼容可选监控配置补入关闭状态及默认镜像/模型目录变量，`SCOPEX_MONITOR_ENABLED=0`，未创建 monitor 容器，8554/8790 无监听。
- 原 257 条历史任务的 2,443 个资产文件逐项哈希不变；原 4 个计划恢复启用，规则和任务内容保留，下一次触发从恢复时重新计算（30 分钟计划 13:09:04、60 分钟计划 13:39:04，Asia/Shanghai）。停机期间不补跑。
- 稳定日志 `CowDisinfect-20260928-110000.log` 的 11:49:39–11:50:39 窗口成功读取 3,305 个 raw 样本。最初探针使用正在轮转的 12 点日志，触发 `input_changed` 并拒绝结果；这次未通过记录保留，没有改检测器绕过保护。
- 原生业务任务 `task-1783a079dd87` COMPLETED/complete，实际查看三张指定原图并执行 `neighbors`、`summarize`，保存了连续三张规则的异常结果和覆盖限制。这证明部署调用链可用，不是独立人工确认的识别准确率验收。
- 前端 index、JS、CSS 的 HTTP 内容哈希一致；浏览器连接工具起初超时，后通过原生 Chrome 实际打开任务列表和上述结果详情并截图，页面复验完成。

回滚配置、旧应用、计划和服务回执保存在目标 `/opt/ScalingRobotics/scopex/deploy-backups/20260928-edge7-8c6a8f9/`；本地发布清单、验证日志、业务结果和截图在 `.local/deploy-20260928-edge7/`。本轮不提交或推送代码，不修改另一台 workstation 的服务状态。


### 2026-09-28 停用计划编辑与照片预览更新（10.200.0.7）

功能提交 `32ee650` 已发布，验收时间 `2026-09-28 13:06:04 +08:00`。本轮 Python/前端变更，无依赖、Compose 或模型变化，按第 6 节代码更新路径执行：备份应用和配置、暂停原 4 个计划、`update.sh --check-only`、停止空闲 ScopeX Runtime、同步 `app/`、目标 Runtime 镜像回归、`update.sh` 重建 Runtime、验收、恢复计划。vLLM 容器 ID/启动时间保留，RTSP 关闭、两个服务仍为 `restart: no`。

- 发布 338 文件哈希一致；目标 Python 3.11 Runtime 的 34 项相关回归通过。
- 实际停用计划可编辑并保持停用，修改后恢复原定义；启用状态编辑返回 409。原计划 ID、名称、内容、规则及历史字段保留，4 个启用计划恢复，下一次时间从恢复时重新计算。
- 相机历史任务 `task-1783a079dd87` 的三张原图接口返回 JPEG，逐张 SHA-256 与证据一致。页面 index/JS/CSS 与本地构建一致。Chrome 主流程此前在本地通过；本轮线上浏览器复验未完成（IAB 超时，原生 Chrome 有用户输入），不计为线上交互验收通过。
- 原 258 个任务、2453 个任务资产文件 SHA-256 未变；健康检查 ok。未创建新业务任务或修改原任务结果。
- 回滚资产及回执位于 `/opt/ScalingRobotics/scopex/deploy-backups/20260928-32ee650/`：`app-before.tar.gz`、`config-before.tar.gz`、`before.json`、`manifest.json`、`runtime-regression.txt`、`feature-acceptance.json`、`acceptance.json`。代码回滚时暂停计划并确认无活动任务，恢复备份的 `app/`，执行 `update.sh`，验收后恢复先前计划；普通代码回滚无需恢复配置备份或替换历史数据。
