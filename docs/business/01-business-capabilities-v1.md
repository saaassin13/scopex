# ScopeX 第一批业务能力 V1

状态：**2026-09-14 已实现第一版并进入真实业务迭代，待 Spark 完整验收**。

固定边界：

> **OpenClaw owns execution. ScopeX owns product control and trust.**

业务 Skill 提供领域语义、稳定脚本、数据源和停止原则；模型仍决定如何组合能力。ScopeX 不增加第二套 Workflow Engine。

真实目录、有界访问、Data Locator 和 Evidence 分层详见：

```text
docs/business/02-data-catalog-and-bounded-access.md
```

可信用户报告详见：

```text
docs/architecture/08-trusted-report-composer.md
```

## 1. 能力范围

| 能力 | 主数据源 | V1 业务目标 |
|---|---|---|
| `data-locator` | Data Catalog | 明确时间窗的有界文件集合；不做业务诊断 |
| `system-health` | 每 Run 当前 Spark host snapshot | 当前 CPU、内存、磁盘、GPU 等资源状态 |
| `image-quality-diagnosis` | LeftCamera 原图 | 视觉判断模糊、起雾/雾化、脏污、水珠、运动模糊、失焦 |
| `nipple-recognition-analysis` | CowDisinfect 日志 + 可选 JPG/JSON | 牛数、最终2D乳头分布、四乳头率、总体识别率 |
| `encoder-health` | CowDisinfect application EncoderVal + raw/filtered | 真正毛刺、回退、连续回退、异常正跳、采样缺口 |
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

## 3. 总体业务与产品路径

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
OpenClaw 综合调查
   ↓
Claim-grade Evidence
   ↓
Fresh Structured Finalizer
   ↓
Validated Claims
   ↓
Constrained Report Composer（无工具、无新事实）
   ↓
Report Validator
   ↓
结论 / 事实依据 / 可能性分析 / 下一步 / 数据限制
```

原则：

1. 主数据源先回答核心问题；
2. 不因为其他目录存在就全部扫描；
3. 稳定脚本计算事实/候选，不直接写物理根因；
4. stdout 保持 compact，详细中间数据放 `/task-scratch`；
5. 用户范围优先；
6. 证据够即停止；
7. **模型负责把已验证内容写成人话，代码负责引用/认识论校验；不再用大量字段 label/if-else 作为主产品表达路线。**

Report Composer 失败时保留 deterministic renderer 作为 fallback，不影响已验证业务任务本身。

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
  ↓
system_health.py
  ↓
一条 compact business_facts
```

固定规则：

- 无 30 秒 timer；
- 无资源 history；
- 不能回答过去某时刻 CPU/GPU；
- snapshot 字段失败时明确 unavailable；
- 禁止 Sandbox `/proc/free/df/nvidia-smi` fallback；
- 用户页面不展示 `current.json` 的几百行字段，而展示 Report Composer 整理后的当前资源事实。

## 5. image-quality-diagnosis

### 最核心业务原则

**最终脏污/模糊/起雾/水珠判断必须来自视觉模型直接查看原始 JPG。**

Laplacian、gradient、brightness、contrast、clip ratio 等指标只能用于多图时的快速分区、筛选或参考，不能独立决定：

```text
无起雾
无脏污
图片正常
```

尤其高 Laplacian 并不能排除覆盖在画面上的半透明 veil。

### 单图

```text
用户指定原图
  ↓
view_image 原图
  ↓
判断：模糊 / 起雾雾化 / 脏污 / 水珠 / 运动模糊 / 失焦
  ↓
需要时才用指标做辅助
  ↓
stop
```

不默认读取日志、JSON、PCD 或其他图片。

### 时间窗/多图

```text
时间窗
  ↓
data-locator
  ↓
有界代表性集合
  ↓
可选 metrics 做时间/指标分区
  ↓
从不同时间、不同分区抽原图
  ↓
反复 view_image，每次 <=2 张
  ↓
跨图判断是否持续存在
```

筛选目标是**覆盖不同状态并去重**，不是自动诊断。

视觉重点：

- 起雾/雾化：乳白/半透明 veil、washed blacks、广泛低频对比下降、halo/glare；
- 水珠：局部透明/高光滴状结构和光学畸变；
- 脏污：固定坐标的 smear/spot/streak/blob；
- 运动模糊：方向性拖影；
- 失焦：更均匀的各向软化。

“无起雾”是比“发现起雾”更强的负结论，必须有多个不同时间/场景原图直接视觉覆盖。

如果 `view_image` 明确返回 omitted/truncated，该调用中的图片不能成为 claim-grade Evidence，必须缩小批次重新看。

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

另输出：

- 4 / 3 / 2 / 1 / 0 个乳头牛数；
- 无最终结果牛数；
- complete-four rate；
- unfinished；
- over-detection。

### 产品调用

1. data-locator 定位目标时间窗相关日志文件组；
2. `nipple_stats.py <explicit files> --start ... --end ... --details-out /task-scratch/nipple-details.json`；
3. stdout 只输出 compact `business_facts`；
4. 只有需要辅助核对时才传 `--artifact-dir /agent-data/left-camera`，脚本只访问目标小时目录。

JSON/JPG 是结果辅助核对，不是 KPI 分母。

用户报告应优先回答：

> 本时间窗共多少头牛、4/3/2/1/0 分布、完整四乳头率、总体乳头识别率，以及是否有明显 missing/unfinished 数据质量问题。

边界：系统完全漏掉且没有命名周期的物理牛仍需要 RFID/视频等独立 ground truth。

## 7. encoder-health

目标是回答：

> **编码器累计数值中是否存在真正的毛刺、回退、异常跳变或采样缺失？具体发生在哪里？**

不是回答“有多少个 delta<0”。

### 产品调用

```text
明确时间窗
  ↓
data-locator 选择显式 .log/.log.N
  ↓
encoder_health.py <all selected files> --start ... --end ...
  ↓
compact business_facts + top_candidates
```

脚本把显式文件合并到同一时间序列，因此能检查跨轮转文件边界。

### 主要业务序列

优先：

```text
EncoderVal [N], TurnTableSpeed [V mm/s]
```

作为应用实际消费的累计编码器值。

如果有：

```text
Get EncoderVal, raw[R], filtered[F]
```

则用于交叉检查低层异常是否被滤掉或传播到应用层。没有应用序列时才退回 raw 作为主序列。

### 事件定义

- `sampling_gap`：真实时间间隔明显异常；
- `reverse_glitch_candidate`：孤立明显回退，随后短时间 catch-up/recovery；
- `reverse_interval_candidate`：连续多个负增量形成回退区间；
- `reverse_step_candidate`：单次明显回退，但短时间未确认恢复；
- `positive_spike_candidate`：相对附近正常增量明显偏大的正向跳变，同时考虑真实 `dt`；
- `flat_count_candidate`：计数长时间不变，仅作候选，不自动当异常。

小幅 `-1/-2/...` 若低于局部正常变化阈值，只做 telemetry，不逐项升级为异常。

异常报告应给出：

```text
时间
前值 / 当前值
signed delta
采样间隔
附近正常增量
是否快速恢复
raw/filtered 是否同时异常（若有）
```

数据异常与物理根因必须分开。只有用户进一步问“为什么/是否影响业务”时，才围绕真正候选调用 bounded `log-context`。

不使用未经验证的 pulses/mm、counts/rev 做物理距离/相位推断。

## 8. log-context

`log-context` 是公共辅助能力。给定显式日志 + 时间/关键词，单遍流式返回 bounded source/line/timestamp/raw/anchor/before-after。

需要解释 candidate 时才使用，例如：

```text
encoder candidate
  ↓
log-context(±几秒)
  ↓
观察 reset / stop / read error / restart
  ↓
Agent 区分观察事实、时间关联、待验证根因
```

无 anchor 不无限扩大。

## 9. Evidence、Claims 与用户报告

稳定业务脚本 stdout 使用：

```json
{"scopex_role":"business_facts"}
```

Projector 将其整体冻结成**一条**结构化 claim-grade Evidence。

以下属于 Trace/Working/Internal 层，不是用户事实：

```text
/workspace/skills/**
/workspace data catalog
/task-scratch/**
scopex_role=locator
```

用户结果不再直接枚举 Evidence Catalog：

```text
Evidence
  ↓
Validated Claims
  ↓
Constrained Report Composer
  ↓
结论
事实依据
可能性分析
下一步
数据限制
```

原始 Evidence 只在“查看原始依据/技术记录”和 Review Bundle 中用于审计。

## 10. 第一批真实验收

1. 真实两个 Catalog host path 自动只读挂载；
2. log locator 正确处理非自然整点文件起始；
3. LeftCamera locator 只访问目标 `YYYYMMDD/HH`；
4. 单图任务不读无关数据；时间窗图片以代表性多图视觉判断为最终依据；
5. omitted/truncated 的 view_image 不形成 claim-grade image Evidence；
6. nipple 一小时人工复算牛数/最终 `NippleNum`/KPI；
7. encoder 任务只需 locator + 一次业务脚本，显著减少工具调用/Evidence；
8. encoder 真正候选与人工原始行一致，报告给出具体时间/count/delta/recovery；
9. Report Composer 只引用有效 C/E，用户事实不显示 JSON/Skill/脚本字段；
10. Report Composer 故障时 deterministic fallback 仍可用；
11. 无业务证据时正式 task 不发布自由回答。
