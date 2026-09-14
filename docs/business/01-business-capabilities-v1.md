# ScopeX 第一批业务能力 V1

状态：**2026-09-14 第一版实现设计**。本文件描述业务目的、数据边界和最小实现，不把历史排查脚本直接当作产品需求。

固定架构边界：

> **OpenClaw owns execution. ScopeX owns product control and trust.**

业务 Skill 提供领域语义、稳定脚本和停止原则；模型仍决定在用户目标范围内如何组合能力。ScopeX 不增加第二套 Workflow Engine。

## 1. 第一批能力范围

| 能力 | 主数据源 | 日志作用 | V1 输出 |
|---|---|---|---|
| `system-health` | Spark 宿主机资源历史 | 必要时解释某资源异常时系统在做什么 | CPU/Load、内存、磁盘、GPU、Docker、关键进程事实 |
| `image-quality-diagnosis` | 原始图片 | 必要时找拍照/相机上下文 | 模糊、起雾、脏污及不确定性 |
| `nipple-recognition-analysis` | 推理 JSON | 解释缺失/下降时间窗 | 牛数、结果覆盖率、乳头数分布、完整四乳头率、乳头识别率 |
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
3. **日志是上下文，不是万能入口**。先有异常时间/业务对象，再捞附近日志。
4. **用户范围优先**。问乳头识别率就不自动跑 CPU、编码器、图片。
5. **证据够即停止**。额外工具调用不等于更高质量。

## 3. system-health

### 业务目的

回答：

- 当前 Spark CPU / 内存 / 磁盘 / GPU 是否有资源压力；
- 某历史时间窗口资源情况如何；
- 哪些进程/容器在占用资源；
- 某业务异常与资源压力是否存在时间重合。

### 为什么不能直接在 Agent Sandbox 里采集

Agent 默认 `exec_host=sandbox`。Sandbox 内 `/proc`、磁盘和进程不是 DGX Spark 宿主机状态。

V1 使用宿主机独立轻量采集器：

```text
systemd timer (30s)
    ↓
scripts/collect_system_metrics.py
    ↓
~/.local/share/scopex/system-metrics/system_metrics.jsonl
    ↓ read-only bind
/scopex-system-metrics/system_metrics.jsonl
    ↓
system-health Skill
```

这样不需要给业务 Agent 宿主机 Shell 权限。

### 采集事实

- CPU utilization、load1/5/15；
- memory total / used / available / swap used；
- 指定 filesystem total / used / free；
- `nvidia-smi` 可用时的 GPU utilization / memory / temperature / power；
- Docker `stats` 快照；
- CPU / RSS top processes；
- 采集器自身错误。

历史 JSONL 默认按文件大小限制，不引入 Prometheus/Grafana/数据库。

### 判断边界

高 CPU/GPU 本身不能证明识别失败。若用户问“7:20 的识别异常是否因为负载”，必须先对齐同一时间窗后再表述为观察事实、时间关联或待验证因果。

## 4. image-quality-diagnosis

该能力沿用现有实现：

```text
指定原图 -> view_image -> 必要时客观 metrics -> 结果
```

V1 重点：

- 单图优先直接视觉，不默认 `exec`；
- 模糊是可观察现象，具体物理原因可未知；
- 起雾/脏污需要跨不同场景仍稳定存在时证据更强；
- 大图片集先筛选，最终结论依赖的原图集合保持有界。

## 5. nipple-recognition-analysis

### 业务目的

例如回答：

> 2026-09-14 07:00–08:00 有多少头牛？每头最终识别到几个乳头？完整四乳头率和乳头识别率是多少？

### 主数据源

推理 JSON。V1 不再从日志里的 `NippleNum` 直接计算产品 KPI。

工具：

```text
skills/nipple-recognition-analysis/scripts/nipple_stats.py
```

真实 JSON schema 尚未冻结，因此 V1 使用显式字段映射：

- `--time-field`；
- 一个或多个 `--cow-field`；
- `--nipple-field`，可以是数字或乳头数组；
- 可选 `--selected-field`；
- 强制显式 `--policy selected|latest|max`。

### 为什么必须先按牛聚合

一头牛可能产生多帧：

```text
2 -> 3 -> 4 -> 4
```

不能把这些帧的乳头数直接相加。必须根据真实业务语义先选出该牛的一条最终业务记录。

推荐优先级：

1. JSON 有可靠 final/selected 标记 -> `selected`；
2. 最新记录就是业务采用结果 -> `latest`；
3. 明确业务定义为“该牛周期最好一次结果” -> `max`。

若只能临时用 `max` 做诊断，结果必须明确 `selection_policy=max`，不能伪装成已经确认的产品口径。

### 先看结果覆盖率，再看识别率

不能把“有可用最终结果的牛”直接当成全部牛，否则缺结果的牛会自动从分母消失，指标虚高。

V1 区分：

- `total_cows`：时间窗内 JSON 中存在有效时间戳 + cow key 的唯一牛数；
- `cows_with_selected_result`：按 configured policy 确实选出可用乳头结果的牛数；
- `selected_result_coverage_rate = cows_with_selected_result / total_cows`；
- `cows_without_selected_result`：JSON 已经证明这头牛存在，但没有形成可用最终结果。

### V1 指标

- `exactly_four_cows`：最终结果恰好 4 个乳头；
- `complete_four_nipple_rate = exactly_four_cows / total_cows`，缺最终结果的牛按“不完整”计；
- `nipple_recognition_rate = Σ min(selected nipple_count, 4) / (total_cows × 4)`，缺最终结果在这个保守主指标中贡献 0；
- 同时输出 `*_selected_only` 版本，便于区分“识别差”与“结果覆盖差”，但 selected-only 指标必须和 coverage 一起看；
- `>4` 单独计 `over_four_cows`，不允许把识别率推到 100% 以上；
- 输出最终乳头数分布和逐牛有效结果。

这个 `total_cows` 仍只是“JSON 中观察到的牛”。**完全没有生成任何 JSON 的真实牛无法靠 JSON 自身发现。** 后续如果日志/RFID/其他独立来源能提供实际经过牛数，应增加跨源 `actual_cows vs json_observed_cows` coverage，而不是假装 JSON 已经看到漏掉的牛。

## 6. encoder-health

### 业务目的

回答编码器**数据本身是否健康**：

- 是否存在读取失败/无效值；
- 是否丢采样或采样周期出现 gap；
- 是否存在 raw 回退；
- 是否存在异常大正跳；
- 是否长时间不变；
- raw / filtered 是否出现明显偏离。

V1 不做牛位漏检推断，不做物理速度/距离 KPI。

工具：

```text
skills/encoder-health/scripts/encoder_health.py
```

### 输出事件语义

- `sampling_gap`：时间戳间隔异常；
- `negative_jump`：观察到 raw 减小；
- `large_negative_jump_candidate`：大幅 raw 减小，可能是 reset / 真实反转 / 故障，尚未定因；
- `positive_delta_outlier_candidate`：相对当前数据分布异常的正向 delta；
- `flat_raw_candidate`：raw 在配置时长内保持不变。

### 阈值原则

历史脚本存在不一致的无效值和经验阈值，因此 V1 不把旧脚本阈值当硬件真理。

当前 CLI 默认值都写入输出 `config`，是**显式分析 profile**。后续确认具体固件/编码器协议后应形成 site/firmware profile，再覆盖默认参数。

V1 特意不使用 `15.717 pulse/mm`、`200 mm/s` 等物理业务阈值，避免未经确认就把历史经验升级成产品规则。

## 7. log-context

公共支持能力：

```text
skills/log-context/scripts/log_context.py
```

输入显式日志文件 + 时间窗/关键词，返回：

- source；
- line number；
- timestamp；
- raw line；
- anchor 标记；
- 少量 before/after 上下文。

实现要求：单遍流式扫描、有界 before buffer、有界输出；不能为了取上下文把整份大型日志先读进内存。`max-lines` 很小时 anchor 优先于 before/after 上下文。

它不做根因诊断。

正确使用方式：

```text
encoder-health
  ↓ 发现 07:21:13 negative jump
log-context(07:21:13 ± 5s)
  ↓ 看到 ResetEncoderValOnSerialPort
Agent
  ↓ 区分“观察到同时/先后出现”与“已经证明因果”
```

## 8. 历史脚本如何处理

用户提供的历史工具继续作为业务理解/回归样本，不直接变成正式产品 API：

- `remote_disk_free.py`：保留“采集事实、不做阈值判定”的职责思想；远端 SSH 能力等网络 topology 明确后再设计；
- `cow_perception.py`：用于理解牛号/帧/NippleNum 等日志语义，但 V1 KPI 改以 JSON 为主；
- `encoder_speed_from_pulses.py` / `segment_encoder_distance.py`：算法和现场经验可作为参考，但阈值不直接继承；
- `suspect_stall_position.py`：实验研究工具，不进入 V1 production Skill；
- `segment_photo_to_exec.py`：包含大量宝贵日志业务知识，但 parser/归属/故障分类耦合较深，V1 不把它当黑盒产品工具。

## 9. 第一批验收

### system-health

- systemd timer 能连续写 JSONL；
- Runtime 只读挂载 metrics；
- “当前负载”和“历史 7 点负载”能区分；
- Agent 不把 Sandbox 自身资源当宿主机资源。

### nipple-recognition-analysis

需要一批真实 JSON 后确认：

- schema mapping；
- cow key；
- selected/latest/max 的真实业务语义；
- `total_cows / selected_result_coverage / KPI` 可人工复算；
- 缺最终结果的牛不会被悄悄从分母删除；
- `>4` 不抬高识别率；
- 缺字段/坏 JSON 不静默吞掉。

### encoder-health

- 人工构造/真实样本可定位 sample gap、negative jump、large negative candidate、flat；
- invalid sample 必须打断连续区间，不能跨失败值制造假 delta；
- 事件保留行号和时间；
- 需要解释时才调用 log-context；
- 不无证据把 candidate 升级成 reset/损坏。

### image-quality

继续沿用真实单图和跨图验收：直接原图、少量请求、尊重范围、证据够即停止。

## 10. 下一步

第一批业务能力稳定后，再按真实需求决定：

1. JSON schema/profile 固化；
2. 编码器 site/firmware profile；
3. 跨源“实际经过牛数 vs JSON 观察牛数”；
4. 资源异常与识别率/编码器事件的跨能力相关分析；
5. 网络 topology 确认后的网络能力。
