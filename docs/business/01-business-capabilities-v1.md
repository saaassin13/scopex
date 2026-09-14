# ScopeX 第一批业务能力 V1

状态：**2026-09-14 已实现第一版，待 Spark / 真实业务验收**。

固定边界：

> **OpenClaw owns execution. ScopeX owns product control and trust.**

业务 Skill 提供领域语义、稳定脚本、数据源和停止原则；模型仍决定如何组合能力。ScopeX 不增加第二套 Workflow Engine。

真实目录、有界访问、Data Locator 和 Evidence 分层详见：

```text
docs/business/02-data-catalog-and-bounded-access.md
```

## 1. 能力范围

| 能力 | 主数据源 | V1 输出 |
|---|---|---|
| `data-locator` | Data Catalog | 明确时间窗的有界文件集合；不做业务诊断 |
| `system-health` | 每 Run 当前 Spark host snapshot | 当前 CPU/Load、memory、disk、GPU、Docker、process facts |
| `image-quality-diagnosis` | LeftCamera 原图 | 模糊/起雾/脏污等视觉判断与不确定性 |
| `nipple-recognition-analysis` | CowDisinfect 日志 + 可选 JPG/JSON | 牛数、最终2D乳头分布、四乳头率、总体识别率 |
| `encoder-health` | CowDisinfect 编码器采样日志 | 采样连续性、raw变化分布、显著候选、flat候选 |
| `log-context` | CowDisinfect 日志 | 小窗口原始上下文，不独立判根因 |

网络能力不进入 V1；topology 未明确前不做自动网络诊断。

## 2. 真实 Data Catalog

当前固定：

```text
cowdisinfect_logs
  host  /opt/ScalingRobotics/CowDisinfect/Log
  agent /agent-data/logs
  file  CowDisinfect-YYYYMMDD-HHMMSS.log[.N]

left_camera_multimodal
  host  /opt/ScalingRobotics/CowDisinfect/GrabbedImages/LeftCamera
  agent /agent-data/left-camera
  dir   YYYYMMDD/HH
  file  YYYYMMDD-HHMMSSmmm.jpg|json|pcd
```

日志文件名中的时间表示文件组起始时间，可能是 `10:23:36`，因此查询 11:00 时仍可能需要 10:23:36 开始的组。业务 Skill 不应自己按自然小时猜文件；优先使用 `data-locator`。

LeftCamera 目录直接按日期/小时定位，禁止递归扫描整个历史树。

## 3. 总体业务路径

```text
用户目标/时间窗
   ↓
data-locator（需要时）
   ↓
业务 Skill 的主数据源 + 稳定脚本 / 原图
   ↓
compact business_facts / 原始事实
   ↓ 仅需要解释时
bounded log-context
   ↓
OpenClaw 综合判断
   ↓
Claim-grade Evidence -> Finalizer -> Claims -> Product Answer
```

原则：

1. 主数据源先回答核心问题；
2. 不因为其他目录存在就全部扫描；
3. 稳定脚本计算事实/候选，不直接写物理根因；
4. stdout 保持 compact，详细中间数据放 `/task-scratch`；
5. 用户范围优先；
6. 证据够即停止。

## 4. system-health

只回答**当前** Spark host：CPU/load、memory、disk、GPU、Docker、top process。

```text
Run 创建
  ↓
ScopeX host snapshot
  ↓
<task-work>/host/current.json
  ↓ read-only
/scopex-host/current.json
```

固定规则：

- 无 30 秒 timer；
- 无资源 history；
- 不能回答过去某时刻 CPU/GPU；
- snapshot 字段失败时明确 unavailable；
- 禁止 Sandbox `/proc/free/df/nvidia-smi` fallback。

## 5. image-quality-diagnosis

### 单图

```text
用户指定原图 -> view_image -> 必要时客观 metrics -> stop
```

不默认读取日志、JSON、其他图片。

### 时间窗图片

先用 `data-locator` 进入目标 `YYYYMMDD/HH` 并按文件名时间戳筛选。大图片集先抽样/筛查，最终 claim-grade 原图集合最多保持少量。

V1 区分：可观察的模糊/对比下降 与 需要更强证据的起雾/镜头脏污物理原因。

## 6. nipple-recognition-analysis

### 业务定义

- 一头牛固定 **4 个乳头**；
- KPI 识别数量 = 日志 2D `NippleNum[N]`；
- 3D 坐标、`IsValid`、3D transform/valid count 不参与；
- `N > 4` 单列 over-detection，KPI cap=4。

### 总牛数和最终帧

检测/推理失败时 JPG/JSON 可能不保存，所以文件数不能作为分母。

V1 `total_cows` = 请求时间窗内开始命名检测轮的唯一牛周期。

一头牛不能求和或取 max：

```text
Start left camera AI detect
  ImgTimeStamp[T] / CowOccuredCount[C] / DetectingNumCurRound[R]
        ↓
Left camera cow [C] detecting [R] finished ... NippleNum[N]
        ↓
New cow detecte finished ... LastImgTimeStamp[T]
```

最终 2D 数 = `LastImgTimeStamp[T]` 对应帧的 `NippleNum[N]`。

### KPI

```text
expected_nipples = total_cows × 4
capped_2d_detections = Σ min(final_2d_count, 4)
nipple_recognition_rate = capped_2d_detections / expected_nipples
```

另输出 complete-four rate、最终分布、unfinished、missing final result、over-detection。

### 产品调用

1. data-locator 定位目标时间窗相关日志文件组；
2. `nipple_stats.py <explicit files> --start ... --end ... --details-out /task-scratch/nipple-details.json`；
3. stdout 只输出 compact `business_facts`；
4. 只有需要辅助核对时才传 `--artifact-dir /agent-data/left-camera`，脚本只访问目标小时目录。

边界：系统完全漏掉且没有命名周期的物理牛仍需要 RFID/视频等独立 ground truth。

## 7. encoder-health

V1 只判断编码器数据健康，不做牛位/漏牛推断。

### 产品调用

```text
明确时间窗
  ↓
data-locator 选择显式 .log/.log.N
  ↓
encoder_health.py <all selected files> --start ... --end ...
  ↓
compact business_facts
```

脚本把显式文件合并到同一时间序列，因此能检查跨轮转文件边界。

### 输出事实

- samples / valid / invalid；
- median sample dt / sampling-gap count；
- raw decrease count + magnitude median/P95/max；
- significant negative outlier candidates；
- configured large-negative candidates；
- positive-delta outlier candidates；
- flat periods；
- raw/filtered divergence。

所有 `delta_raw < 0` 不再逐条当异常事件。小回退只是统计分布；只有显著候选进入 `top_candidates`。完整候选可写 `/task-scratch/encoder-events.json`。

`invalid_min / large-negative threshold / MAD` 属于明确分析 profile，不自动等同硬件协议事实。

## 8. log-context

`log-context` 是公共辅助能力。给定显式日志 + 时间/关键词，单遍流式返回 bounded source/line/timestamp/raw/anchor/before-after。

需要解释 encoder candidate 时才使用，例如：

```text
encoder significant candidate
  ↓
log-context(±5s)
  ↓
观察 reset / stop / read error / restart
  ↓
Agent 再区分时间关联与因果
```

无 anchor 不无限扩大。

## 9. Evidence 与用户事实

稳定业务脚本 stdout 使用：

```json
{"scopex_role":"business_facts"}
```

Projector 将其整体冻结成**一条**结构化 claim-grade Evidence。

以下只属于 Trace/Working Data，不作为用户事实：

```text
/workspace/skills/**
/workspace/scopex-data-catalog.json
/task-scratch/**
scopex_role=locator
```

UI 的“事实依据”只展示真正业务事实；完整工具过程继续保留在“调查进度/技术记录”和 Review Bundle。

## 10. 第一批真实验收

1. 真实两个 Catalog host path 自动只读挂载；
2. log locator 正确处理非自然整点文件起始；
3. LeftCamera locator 只访问目标 `YYYYMMDD/HH`；
4. 单图任务不读无关数据；
5. nipple 一小时人工复算牛数/最终 `NippleNum`/KPI；
6. encoder 3点任务只需 locator + 一次业务脚本，显著减少工具调用/Evidence；
7. encoder 候选与人工原始行一致；
8. Facts 页面无 Skill.md / script source / locator / scratch；
9. 无业务证据时正式 task 不发布自由回答。
