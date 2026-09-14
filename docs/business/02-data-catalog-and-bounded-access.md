# ScopeX 数据目录与有界访问基线

状态：**2026-09-14 当前业务数据基线，待 Spark 真实目录验收**。

目标：让 Agent 知道“数据在哪里、怎么按时间定位、哪些目录很大、哪些事实能从什么数据源得到”，而不是面对 `/agent-data` 后自行递归探索整个端侧磁盘。

## 1. 真实宿主机目录

### CowDisinfect 日志

```text
/opt/ScalingRobotics/CowDisinfect/Log
```

文件按小时生成，同一小时可能存在轮转文件：

```text
CowDisinfect-20260716-102336.log
CowDisinfect-20260716-102336.log.1
CowDisinfect-20260716-102336.log.2
```

ScopeX Sandbox 固定逻辑路径：

```text
/agent-data/logs
```

### LeftCamera 多模态数据

```text
/opt/ScalingRobotics/CowDisinfect/GrabbedImages/LeftCamera
```

目录按日期 / 小时组织：

```text
YYYYMMDD/HH/
```

示例：

```text
20260716/00/
20260716/01/
```

文件使用毫秒级时间戳 stem 关联：

```text
20260914-130002161.jpg
20260914-130002161.json
20260914-130002161.pcd
```

ScopeX Sandbox 固定逻辑路径：

```text
/agent-data/left-camera
```

同 stem JPG / JSON / PCD 表示同一采集结果的不同模态。检测或推理失败时部分 artifact 可能根本不会保存，因此文件数不能自动等同牛数/检测次数。

## 2. Data Catalog

仓库维护：

```text
config/data-catalog.json
```

Runtime 启动时复制到：

```text
/workspace/scopex-data-catalog.json
```

如果 Catalog 中声明的宿主机目录存在，Runtime 默认自动只读挂载。额外/替代目录仍可通过 `--data-dir HOST:AGENT` 显式配置。

Catalog 是**语义索引**，不是文件索引。它只描述：

- 数据源名称；
- host / agent path；
- 目录和文件命名；
- 时间语义；
- 访问预算；
- 业务边界。

Runtime 不会启动时 `find` 全盘建立索引。

## 3. Data Locator

支持 Skill：

```text
data-locator
```

稳定脚本：

```text
/workspace/skills/data-locator/scripts/data_locator.py
```

日志示例：

```bash
python3 /workspace/skills/data-locator/scripts/data_locator.py \
  --source cowdisinfect_logs \
  --start "2026-09-14 03:00:00" \
  --end   "2026-09-14 04:00:00"
```

它只按日志文件名的日期/小时选择相关 `CowDisinfect-*.log[.N]`，不会递归读取其他目录。

图片示例：

```bash
python3 /workspace/skills/data-locator/scripts/data_locator.py \
  --source left_camera_multimodal \
  --kind jpg \
  --start "2026-09-14 13:00:00" \
  --end   "2026-09-14 13:30:00" \
  --max-files 32
```

它直接进入：

```text
/agent-data/left-camera/20260914/13
```

然后按文件名时间戳过滤，不扫描其他日期/小时目录。

Locator 输出 `scopex_role=locator`，属于调查路由信息，不进入 Claim-grade Evidence。

## 4. 大目录访问规则

普通时间窗任务禁止默认执行：

```text
find /agent-data ...
du -a /agent-data ...
grep -R /agent-data ...
rg --files /agent-data ...
```

除非用户明确要求全量 inventory，并且未来为该类任务单独提供资源预算。

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

超过边界时应先缩小时间窗/分段，而不是一次读取全部历史。

## 5. 稳定业务脚本输出

稳定业务脚本 stdout 使用：

```json
{
  "scopex_role": "business_facts"
}
```

目的：stdout 只输出紧凑事实，完整明细写入 `/task-scratch`。

例如编码器：

```text
encoder_health.py --log-dir /agent-data/logs --start ... --end ...
```

一次处理目标小时的所有轮转日志，并跨文件边界计算采样连续性。所有小 `delta<0` 只统计幅度分布，只有显著候选进入 `top_candidates`；完整候选可写 `/task-scratch/encoder-events.json`。

乳头 KPI 同样只把 KPI/质量摘要输出到 stdout，逐牛明细写 `/task-scratch/nipple-details.json`。

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
  scopex_role=business_facts 的稳定结构化结果

User Facts
  UI 中从 Claim-grade Evidence 提取的事实依据
```

当前 Projector 固定规则：

- `/workspace/skills/**` read：Trace-only；
- `/workspace/scopex-data-catalog.json` read：Trace-only；
- `/task-scratch/**` read：Trace-only；
- `scopex_role=locator`：Trace-only；
- `scopex_role=business_facts`：整体作为一条结构化 Evidence，而不是逐行拆成几百条。

## 7. Scheduler 断电语义

设备断电 / ScopeX 不运行期间错过的定时触发**不补跑**。

例如每 30 分钟任务在离线期间错过：

```text
14:30
15:00
15:30
16:00
```

16:20 启动后：

```text
missed_count += 4
last_missed_at = 16:00
next_run_at = 16:30
```

不会创建四个历史 Task。一次性任务若已过期则 `MISSED_OFFLINE` 并禁用。

## 8. 下一步验收

1. Spark 启动 Runtime，确认两个真实 host path 自动挂载；
2. `data_locator.py` 对 3 点日志只返回 3 点小时相关文件；
3. 对 13:00~13:30 LeftCamera 只访问 `YYYYMMDD/13`；
4. 编码器同一小时 `.log/.log.1/...` 一次分析并跨文件连续；
5. nipple KPI 不递归扫描整个 LeftCamera 历史目录；
6. 新任务 Evidence 不再出现 Skill.md / 脚本源码；
7. 重启 Scheduler 后历史触发只计 missed，不创建 Task。
