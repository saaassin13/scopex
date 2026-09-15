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

修改设备路径、VPN 网卡、图片数量和镜像版本：

```bash
vi /opt/ScalingRobotics/scopex/config/edge.env
/opt/ScalingRobotics/scopex/app/deploy/edge/start.sh
```

常用变量：

```text
SCOPEX_MODEL_DIR       模型目录
SCOPEX_VPN_INTERFACE  WireGuard 网卡，默认 wg0
SCOPEX_IMAGE_LIMIT    单次 Prompt 图片上限
VLLM_IMAGE            vLLM 镜像
SCOPEX_RUNTIME_IMAGE  Runtime 镜像
SCOPEX_SANDBOX_IMAGE  Analysis Sandbox 镜像
```

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
