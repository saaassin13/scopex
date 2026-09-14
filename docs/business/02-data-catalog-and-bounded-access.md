# ScopeX 数据目录与有界访问基线

状态：**2026-09-14 当前业务数据基线，待 Spark 真实目录验收**。

目标：让 Agent 知道“数据在哪里、怎么按时间定位、哪些目录很大、哪些事实能从什么数据源得到”，而不是面对 `/agent-data` 后自行递归探索整个端侧磁盘。

## 1. 真实宿主机目录

### CowDisinfect 日志

```text
/opt/ScalingRobotics/CowDisinfect/Log
```

同一文件组可能存在轮转文件：

```text
CowDisinfect-20260716-102336.log
CowDisinfect-20260716-102336.log.1
CowDisinfect-20260716-102336.log.2
```

`102336` 是**文件组起始时间**，不是“只包含 10 点数据”的自然小时标签。若下一组从 `11:23:36` 开始，则查询 11:00 数据仍应选择 `10:23:36` 这一组。

Sandbox 固定路径：

```text
/agent-data/logs
```

### LeftCamera 多模态数据

宿主机：

```text
/opt/ScalingRobotics/CowDisinfect/GrabbedImages/LeftCamera
```

目录：

```text
YYYYMMDD/HH/
```

文件：

```text
YYYYMMDD-HHMMSSmmm.jpg
YYYYMMDD-HHMMSSmmm.json
YYYYMMDD-HHMMSSmmm.pcd
```

Sandbox 固定路径：

```text
/agent-data/left-camera
```

同 stem JPG / JSON / PCD 表示同一采集结果的不同模态。检测或推理失败时部分 artifact 可能不存在，因此文件数不能自动等同牛数/检测次数。

## 2. Data Catalog

仓库：

```text
config/data-catalog.json
```

Runtime 复制到：

```text
/workspace/scopex-data-catalog.json
```

Catalog 中 host path 存在时默认自动只读挂载；额外/替代目录仍可通过 `--data-dir HOST:AGENT` 显式配置。

Catalog 是语义目录，不是启动时全量构建的文件索引。它只描述数据源、路径、命名、时间语义、访问预算和业务边界。

## 3. Data Locator

内部支持 Skill：

```text
data-locator
```

稳定脚本：

```text
/workspace/skills/data-locator/scripts/data_locator.py
```

### 日志

```bash
python3 /workspace/skills/data-locator/scripts/data_locator.py \
  --source cowdisinfect_logs \
  --start "2026-09-14 03:00:00" \
  --end   "2026-09-14 04:00:00"
```

日志 Locator 只做**非递归文件名扫描**，先把同 base timestamp 的 `.log/.log.1/.log.2` 归为一组，再按：

```text
[group_start, next_group_start)
```

和请求窗口是否重叠选择文件组。最后一组没有下一组时，定位层默认最多按约 `+1h` 推断；真正业务统计仍会按日志行 timestamp 严格过滤 `--start/--end`。

### LeftCamera

```bash
python3 /workspace/skills/data-locator/scripts/data_locator.py \
  --source left_camera_multimodal \
  --kind jpg \
  --start "2026-09-14 13:00:00" \
  --end   "2026-09-14 13:30:00" \
  --max-files 32
```

直接进入：

```text
/agent-data/left-camera/20260914/13
```

然后按 filename timestamp 过滤，不扫描其他日期/小时目录。

Locator 输出 `scopex_role=locator`，属于调查路由信息，不进入 Claim-grade Evidence。

## 4. 大目录访问规则

普通时间窗任务禁止默认执行：

```text
find /agent-data ...
du -a /agent-data ...
grep -R /agent-data ...
rg --files /agent-data ...
```

当前 Catalog 默认边界：

```text
CowDisinfect logs
  max_hour_buckets = 48
  max_files_per_operation = 32

LeftCamera multimodal
  max_hour_buckets = 24
  max_files_per_operation = 256
  max_claim_images = 4
  max_pointcloud_files_per_operation = 8
```

超过边界应先缩小时间窗/分段，而不是一次读全部历史。

日志根目录为了判断“前一组是否覆盖当前时间窗”需要非递归读取文件名。如果未来真实目录增长到几十万文件且该操作成为瓶颈，再增加轻量文件组索引；当前不提前引入数据库/后台全盘索引。

## 5. 稳定业务脚本输出

稳定业务脚本 stdout 使用：

```json
{"scopex_role":"business_facts"}
```

stdout 只保留紧凑事实，完整明细写 `/task-scratch`。

编码器产品路径：

```text
data-locator
  ↓ explicit rotated file list
encoder_health.py <file1> <file2> ... --start ... --end ...
```

脚本把显式文件合并到同一时间序列，因此可检测跨轮转边界。所有小 `delta<0` 只统计次数/幅度分布；显著候选进入 `top_candidates`，完整候选可写 `/task-scratch/encoder-events.json`。

乳头 KPI 同样先 locator，再把显式文件传给 `nipple_stats.py`。stdout 只输出 KPI/质量摘要，逐牛明细写 `/task-scratch/nipple-details.json`；需要 JPG/JSON 辅助核对时也只访问目标 `YYYYMMDD/HH`。

## 6. Evidence 分层

```text
Trace / 调查过程
  Skill、脚本源码、Locator、命令、模型请求

Working Data
  /task-scratch 临时脚本/明细/中间结果

Claim-grade Evidence
  /agent-data 原始日志行
  原始只读图片
  /scopex-host 当前快照事实
  scopex_role=business_facts 稳定结构化结果

User Facts
  UI 中展示的事实依据
```

Projector 固定：

- `/workspace/skills/**` read：Trace-only；
- `/workspace/scopex-data-catalog.json` read：Trace-only；
- `/task-scratch/**` read：Trace-only；
- `scopex_role=locator`：Trace-only；
- `scopex_role=business_facts`：整体一条结构化 Evidence，不逐行拆几百条。

## 7. Scheduler 断电语义

设备断电 / ScopeX 不运行期间错过的定时触发**不补跑**。

例如每 30 分钟任务错过 14:30 / 15:00 / 15:30 / 16:00，16:20 启动后只更新：

```text
missed_count += 4
last_missed_at = 16:00
next_run_at = 16:30
```

不会创建四个历史 Task。一次性任务若已过期则 `MISSED_OFFLINE` 并禁用。

## 8. Sandbox 资源保护

当前 OpenClaw Sandbox 已有：

```text
memory = 512 MiB
swap = 512 MiB
CPU = 1 core
PIDs = 256
exec timeout = 30s
network = none
read-only root
capabilities = drop all
```

硬限制可以避免单个误用命令无限吃 CPU/内存/进程，但不能替代正确的数据定位，尤其磁盘 IO 仍应通过 Catalog / Locator / 时间窗避免全盘扫描。

PCD 若真实 workload 需要超过 512 MiB，应基于真实文件大小/降采样测试设计 capability-specific resource profile，而不是直接放大全局 Sandbox。

## 9. 验收

1. Spark Runtime 自动挂载两个真实 host path；
2. 查询跨自然小时窗口时 Locator 能找到前一非整点文件组；
3. 13:00~13:30 LeftCamera 只访问 `YYYYMMDD/13`；
4. 编码器 `.log/.log.1/...` 一次分析并跨文件连续；
5. nipple KPI 不递归扫描整个 LeftCamera；
6. Evidence 不再出现 Skill.md / 脚本源码；
7. `business_facts` 一次输出只形成少量结构化 Evidence；
8. Scheduler 重启后历史 trigger 只计 missed，不创建 Task。
