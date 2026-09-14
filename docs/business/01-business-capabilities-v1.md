# ScopeX 第一批业务能力 V1

状态：**2026-09-14 已实现第一版，待 Spark / 真实业务验收**。本文件描述业务目的、数据边界和最小实现，不把历史排查脚本直接当作产品需求。

固定架构边界：

> **OpenClaw owns execution. ScopeX owns product control and trust.**

业务 Skill 提供领域语义、稳定脚本和停止原则；模型仍决定在用户目标范围内如何组合能力。ScopeX 不增加第二套 Workflow Engine。

## 1. 第一批能力范围

| 能力 | 主数据源 | 上下文作用 | V1 输出 |
|---|---|---|---|
| `system-health` | 每次任务启动时生成的 Spark 当前宿主机快照 | 不保存资源历史 | 当前 CPU/Load、内存、磁盘、GPU、Docker、关键进程事实 |
| `image-quality-diagnosis` | 原始图片 | 必要时找拍照/相机日志上下文 | 模糊、起雾、脏污及不确定性 |
| `nipple-recognition-analysis` | CowDisinfect 日志中的牛周期 + 最终 2D `NippleNum` | JSON/JPG 仅辅助核对 | 牛数、最终 2D 乳头数分布、四乳头率、乳头识别率 |
| `encoder-health` | 编码器原始采样日志 | 解释 reset/stop/通信/生命周期上下文 | 丢数、gap、回退、跳变、长时间不变等候选事件 |
| `log-context` | 业务日志 | 公共上下文能力 | 小范围原始日志窗口，不独立给根因 |

网络能力暂不进入 V1。网络 topology、节点、协议、端口和业务依赖未确认前，不设计自动网络诊断流程。

## 2. 总体设计

```text
用户问题
   ↓
对应业务 Skill
   ↓
主数据源上的确定性脚本 / 原始图片
   ↓
结构化事实 / 候选异常
   ↓ 仅当需要解释
log-context(小时间窗/关键词)
   ↓
OpenClaw + 模型综合判断
   ↓
Evidence -> Fresh Finalizer -> Validated Claims -> Product Answer
```

原则：

1. **主数据源先回答核心问题**。不要因为日志/图片/JSON 都可见就全部扫描。
2. **脚本计算事实，不写根因**。例如 `raw` 回退是事实，“编码器损坏”不是。
3. **用户范围优先**。问乳头识别率就不自动跑 CPU、编码器、图片质量。
4. **日志按业务锚点使用**。需要解释异常时再取小范围上下文。
5. **证据够即停止**。额外工具调用不等于更高质量。

## 3. system-health

### 业务目的

只回答**当前** Spark 宿主机资源状态：

- 当前 CPU / load；
- 当前内存；
- 当前磁盘；
- 当前 GPU；
- 当前 Docker / 关键进程。

V1 不持续采集资源历史，因此不能回答“昨天 3 点 CPU 是否过高”这类历史资源问题。

### 数据边界

Agent 默认 `exec_host=sandbox`，因此不能把 Sandbox 内 `/proc`、`free`、`df`、`nvidia-smi`、Docker 状态当成 DGX Spark 宿主机状态。

当前实现：

```text
普通 Task 创建
    ↓
ScopeX host side 生成一次 current snapshot
    ↓
<task-work>/host/current.json
    ↓ read-only bind
/scopex-host/current.json
    ↓
system-health Skill
```

当前快照包含 CPU/load、memory、`/` disk、GPU、Docker stats、top process 和显式采集错误。

关键规则：

- 不启用 30 秒 timer；
- 不保存 `system_metrics.jsonl`；
- 不做历史 CPU/内存/磁盘/GPU 分析；
- `/scopex-host/current.json` 不可用或字段采集失败时，直接报告对应宿主机指标不可用；
- **绝不 fallback 到 Sandbox 自己的资源数据。**

## 4. image-quality-diagnosis

沿用当前实现：

```text
指定原图 -> view_image -> 必要时客观 metrics -> 结果
```

V1 重点：

- 单图优先直接视觉，不默认 `exec`；
- 模糊是可观察现象，具体物理原因可未知；
- 起雾/脏污跨不同场景仍固定存在时证据更强；
- 大图片集先筛选，最终结论依赖的原图集合保持有界。

## 5. nipple-recognition-analysis

### 5.1 业务定义

用户要的是 **2D 乳头检测识别率**，不是 3D 乳头坐标有效率。

固定口径：

- 一头牛限定 **4 个乳头**；
- 识别数量 = 2D 检测框数量 `NippleNum[N]`；
- `N > 4` 属于过检，统计时最多按 4 个计；
- 3D 坐标、`IsValid`、3D 转换成功数、3D valid count **全部不参与乳头识别率**。

### 5.2 为什么日志成为主数据源

真实现场数据确认：检测/推理失败时图片和 JSON 可能不会保存。因此：

```text
JSON/JPG 数量 != 总牛数
```

不能以 JSON 文件数作为乳头识别率分母。V1 由日志建立牛周期和最终采用帧；保存的 JSON/JPG 仅作为成功结果辅助证据。

工具：

```text
skills/nipple-recognition-analysis/scripts/nipple_stats.py
```

### 5.3 一头牛到底取哪个 NippleNum

一头牛有多轮图片，例如：

```text
0 -> 2 -> 4 -> 4 -> 3
```

不能求和，也不能简单取 `max=4`。

日志存在最终采用帧关系：

```text
Start left camera AI detect
  ImgTimeStamp[T]
  CowOccuredCount[C]
  DetectingNumCurRound[R]
        ↓
Left camera cow [C] detecting [R] finished ... NippleNum[N]
        ↓
New cow detecte finished ... LastImgTimeStamp[T]
```

因此：

> **该牛最终 2D 识别数 = `LastImgTimeStamp[T]` 对应那一帧的 `NippleNum[N]`。**

### 5.4 总牛数

V1 的 `total_cows` 定义为：请求时间窗口内开始了命名检测轮 `DetectingNumCurRound` 的唯一牛周期数。

窗口按 `first_detect_ts` 归属。脚本会跨多个轮转日志文件连续恢复周期，并通过 `CowOccuredCount` 降值切 epoch。

边界：真实奶牛若被系统**完全漏掉**、从未进入命名检测周期，则当前日志本身也不能证明它存在。未来需要 RFID、视频或其他独立 ground truth，不能由 Agent 猜测。

### 5.5 KPI

```text
expected_nipples = total_cows × 4
capped_2d_detections = Σ min(final_2d_count, 4)
nipple_recognition_rate = capped_2d_detections / expected_nipples
```

同时输出：

- `complete_four_nipple_cows`；
- `complete_four_nipple_rate`；
- 最终 2D 数量分布；
- unfinished cycle；
- final 2D result missing；
- `>4` over-detection；
- 逐牛 `LastImgTimeStamp / final NippleNum / line` 证据。

牛周期已开始但没有最终结果时，主识别率按保守口径贡献 0，并单独报告质量问题。

### 5.6 JSON/JPG 的角色

如果传入完整 `--artifact-dir`，脚本可辅助核对：

- `LastImgTimeStamp` 是否存在对应 JPG/JSON；
- JSON `Markers.Rect` 中乳头标签 `1..4` 数量是否与最终日志 `NippleNum` 一致。

JSON 中的 3D 字段不参与 KPI。仅凭文件缺失不能自动判定失败，除非明确知道目录覆盖完整时间窗。

## 6. encoder-health

回答编码器**数据本身是否健康**：

- 读取失败/无效值；
- sample gap；
- raw 回退；
- 异常大正跳候选；
- 长时间不变；
- raw / filtered 明显偏离。

V1 不做牛位漏检推断，不做物理速度/距离 KPI。

工具：

```text
skills/encoder-health/scripts/encoder_health.py
```

输出：`sampling_gap`、`negative_jump`、`large_negative_jump_candidate`、`positive_delta_outlier_candidate`、`flat_raw_candidate`。

历史脚本里的 `15.717 pulse/mm`、`200 mm/s`、不同 READ_FAIL 阈值等先视为现场经验，不直接升级成产品协议事实。无效采样必须打断相邻样本关系，不能跨无效值制造假的回退或跳变。

## 7. log-context

公共支持能力：

```text
skills/log-context/scripts/log_context.py
```

输入显式日志文件 + 时间窗/关键词，返回 source、line number、timestamp、raw line、anchor 和少量 before/after 上下文。

实现采用单遍流式扫描、有界 before buffer 和有界输出。`max-lines` 很小时 anchor 优先。它本身不做根因诊断。

典型方式：

```text
encoder-health
  ↓ 发现 07:21:13 negative jump
log-context(07:21:13 ± 5s)
  ↓ 看到 ResetEncoderValOnSerialPort
Agent
  ↓ 区分“时间上相邻”与“已经证明因果”
```

## 8. 历史脚本如何处理

用户提供的历史工具继续作为业务理解/回归样本，不直接变成正式产品 API：

- `remote_disk_free.py`：保留“采集事实、不做阈值判定”的思想；远端 SSH 等网络 topology 明确后再设计；
- `cow_perception.py`：帮助确认 `CowOccuredCount / DetectingNumCurRound / NippleNum` 日志语义；
- `encoder_speed_from_pulses.py` / `segment_encoder_distance.py`：算法和现场经验供参考，阈值不直接继承；
- `suspect_stall_position.py`：实验研究工具，不进入 V1 production Skill；
- `segment_photo_to_exec.py`：复用 `LastImgTimeStamp` 归属思想，但整个复杂分段/故障分类不作为乳头 KPI 的前置黑盒。

## 9. 第一批验收

### system-health

- Task 创建时能生成 `/scopex-host/current.json` 对应的宿主机快照；
- CPU/内存/磁盘/GPU/Docker 字段在 Spark 上可解析；
- 不存在长期资源 timer/history；
- Agent 不把 Sandbox 自身资源冒充宿主机；
- collector 某字段失败时结果明确“不知道”，而不是 fallback。

### nipple-recognition-analysis

- 用真实轮转日志恢复每头牛；
- `LastImgTimeStamp` 稳定映射对应 `NippleNum`；
- 不使用 3D valid count；
- 一头牛最多计 4 个；
- 早期 4 乳头但最终 2/3 乳头时必须以最终帧为准；
- 图片/JSON 缺失不会把牛从分母删除；
- KPI 可人工抽样复算；
- 完全漏检牛的 ground truth 边界明确。

### encoder-health

- 真实样本可定位 gap、negative jump、large negative candidate、flat；
- invalid sample 不跨段制造假 delta；
- 事件保留行号/时间；
- 需要解释时才调用 log-context；
- candidate 不无证据升级成 reset/损坏。

### image-quality

继续真实单图和跨图验收：直接原图、少量请求、尊重范围、证据够即停止。

## 10. 产品运行方式

业务 Skill 既可被手动 Task 使用，也可由简单 Schedule 到点触发一个普通 Task。Scheduler 不决定 Skill/步骤，不是 Workflow Engine。

Conversation 和 Task 也共用同一套 TaskService / OpenClaw Runtime；区别只是普通 Conversation 允许没有 Evidence，正式业务 Task 仍要求可信 Evidence。

详细见 `docs/10-chat-tasks-scheduling-and-feedback.md`。

## 11. 下一步

1. 当前宿主机快照在 Spark 的真实字段验收；
2. 乳头 KPI 完整 1 小时人工对账；
3. 编码器真实异常数据对账；
4. Conversation / Task / Schedule / 评价 / 导出产品闭环验收；
5. 网络 topology 确认后的网络能力。
