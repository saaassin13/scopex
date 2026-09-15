# 端侧部署

## 制作镜像

```bash
cd /path/to/scopex
npm --prefix frontend install
npm --prefix frontend run build
./deploy/edge/build-images.sh
./deploy/edge/export-images.sh scopex-edge-images
```

## 首次部署

```bash
rsync -az --delete --exclude .git/ --exclude .local/ --exclude .venv/ --exclude frontend/node_modules/ ./ scaling@10.200.0.7:/opt/ScalingRobotics/scopex/app/
rsync -az scopex-edge-images/ scaling@10.200.0.7:/opt/ScalingRobotics/scopex/images/
ssh scaling@10.200.0.7
cd /opt/ScalingRobotics/scopex/app
./deploy/edge/install.sh /opt/ScalingRobotics/scopex/images
```

## 修改配置

首次执行 `install.sh` 会生成：

```text
/opt/ScalingRobotics/scopex/config/edge.env
```

当前 `10.200.0.7` 使用默认配置，无需修改。其他设备首次部署时检查：

```bash
vi /opt/ScalingRobotics/scopex/config/edge.env
/opt/ScalingRobotics/scopex/app/deploy/edge/start.sh
```

按设备检查：

```text
SCOPEX_MODEL_DIR       默认 /opt/ScalingRobotics/scopex/model/Qwen3.8-27B-NVFP4
SCOPEX_VPN_INTERFACE  默认 wg0；脚本自动读取该网卡的 IPv4
COWDISINFECT_LOG_DIR   默认 /opt/ScalingRobotics/CowDisinfect/Log
LEFT_CAMERA_DIR        默认 /opt/ScalingRobotics/CowDisinfect/GrabbedImages/LeftCamera
```

当前产品默认值，通常不要修改：

```text
SCOPEX_IMAGE_LIMIT    12；同时用于 vLLM 和 ScopeX
VLLM_IMAGE            nvcr.io/nvidia/vllm:26.08-py3
SCOPEX_RUNTIME_IMAGE  scopex-runtime:2026.09.15
SCOPEX_SANDBOX_IMAGE  scopex-sandbox-analysis:2026.09.15
SCOPEX_API_KEY        空；本地 vLLM 启用鉴权时才填写
```

只有路径、VPN 网卡或导入的镜像标签与默认值不一致时才必须修改。

配置模板：

```text
/opt/ScalingRobotics/scopex/app/deploy/edge/edge.env.example
```

vLLM 参数在以下文件的 `vllm.command` 修改，ScopeX 参数在
`scopex.command` 修改：

```text
/opt/ScalingRobotics/scopex/app/deploy/edge/compose.yaml
```

例如 `--max-model-len`、`--max-num-seqs`、显存和 KV Cache 属于
`vllm.command`；`--timeout`、`--max-requests`、并发和队列属于
`scopex.command`。

修改后生效：

```bash
/opt/ScalingRobotics/scopex/app/deploy/edge/start.sh
```

访问地址由命令输出，例如：

```text
http://10.200.0.7:8787
```

## 更新代码和 Skill

```bash
cd /path/to/scopex
npm --prefix frontend run build
./deploy/edge/sync.sh scaling@10.200.0.7
```

## 操作

```bash
cd /opt/ScalingRobotics/scopex/app
./deploy/edge/start.sh
./deploy/edge/stop.sh
docker compose --env-file /opt/ScalingRobotics/scopex/config/edge.env -f deploy/edge/compose.yaml ps
docker compose --env-file /opt/ScalingRobotics/scopex/config/edge.env -f deploy/edge/compose.yaml logs -f scopex
```

数据目录：

```text
/opt/ScalingRobotics/scopex/data
```

不要执行 `docker compose down -v`，不要对 `/opt/ScalingRobotics/scopex/` 整体使用 `rsync --delete`。
