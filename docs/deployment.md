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
