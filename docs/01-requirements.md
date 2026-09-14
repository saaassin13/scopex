# ScopeX 产品需求基线

状态：**2026-09-14 当前有效基线**。

## 1. 项目目标

在 NVIDIA DGX Spark 上交付一个本地、可交互、证据可追溯、可执行的工业 Agent Runtime。

当前固定实现基线：

- Agent Runtime：OpenClaw；
- 当前已验证 served model id：`qwen3.8-27b-nvfp4`；
- 推理服务：本地 vLLM OpenAI-compatible `/v1`；
- 产品层：ScopeX Runtime + FastAPI + Vue；
- 默认本地运行，业务数据、日志、图片、审计记录不依赖公网。

核心边界：

> **OpenClaw owns execution. ScopeX owns product control and trust.**

OpenClaw + 模型负责自主调查、工具选择、动作、动作后验证和停止；ScopeX 负责能力/数据挂载、任务范围、权限、生命周期、预算、Evidence、可信 Finalizer、审计和产品 API/UI。

不得在 ScopeX 中重做第二套 Agent Loop / Workflow Engine / Decision Engine / Action Engine。

## 2. 通用产品需求

| ID | 需求 | 当前验收口径 |
|---|---|---|
| R01 | 本地任务链 | OpenClaw、vLLM、文件/Shell/图片、Evidence、Finalizer 在 Spark 本地完成；Sandbox 默认无网络 |
| R02 | 自主调查 | 用户给目标后模型自主决定调查顺序与工具，不要求用户逐步教操作 |
| R03 | 大数据工作集 | 大日志/CSV 不直接灌入 Context；先用 Shell/Python 筛选、计算、有界摘要 |
| R04 | 多图分析 | 大图片集可先筛选；最终结论依赖的只读原图必须实际查看且集合有界 |
| R05 | 可执行动作 | 高风险业务动作只能经过明确 capability / permission boundary |
| R06 | 动作后验证 | exit code 0 不等于业务成功；动作后必须验证真实业务状态 |
| R07 | 上下文持续 | 长任务使用 OpenClaw 原生 compaction；Context 不是原始数据仓库 |
| R08 | 任务可接管 | Stop / Resume / Steering 保留同一 Session 的有效上下文 |
| R09 | 防失控 | Hard request/time budget 单一来源；OpenClaw 原生 loopDetection 防工具死循环 |
| R10 | 可信输出 | 用户可见事实来自 claim-grade Evidence / 原图重新验证，不把模型自身旧话术反向当证据 |
| R11 | 结果优先 | UI 主视图优先结论、说明、执行情况、建议；Evidence 可展开 |
| R12 | 可审计/可复测 | Task/Trace/Evidence/Claims/Result/版本/关键测试可重复回归 |
| R13 | 范围与停止 | 用户明确 target/source/scope 是约束；走最短充分证据路径，够证据即停止 |
| R14 | Skill / 稳定脚本 | 业务语义和稳定分析通过 Skill/脚本提供，但不替代 Agent Loop |
| R15 | 离线部署 | ScopeX update bundle 可离线安装；Device Base 与 ScopeX 小版本分层管理 |

## 3. 第一批业务能力 V1

当前正式业务能力：

```text
system-health
image-quality-diagnosis
nipple-recognition-analysis
encoder-health
log-context（公共上下文能力）
```

网络能力不进入 V1；必须先确认端侧 topology、节点、链路、协议和业务依赖。

### R16 system-health

必须能够：

- 查看 Spark CPU/load、内存、磁盘、GPU、Docker、关键进程；
- 回看历史时间窗口，而不是用“现在的 top”解释过去问题；
- 区分采集失败与真实资源异常；
- 不把 Sandbox 自身资源冒充宿主机资源；
- 只有资源异常与业务问题时间重合且证据足够时，才形成更强解释。

宿主机资源通过独立只读历史数据提供，不要求 Agent 获得全局 host shell。

### R17 image-quality-diagnosis

必须能够：

- 对用户指定原图判断模糊/起雾/脏污等可见现象；
- 单图任务优先直接视觉，不默认运行复杂 Python；
- 需要量化时使用稳定指标脚本；
- 具体物理原因不足时输出不确定；
- 跨图确认固定污迹时使用有界原图集合。

### R18 nipple-recognition-analysis

产品统计对象是 **2D 乳头检测框**，不是 3D 乳头坐标有效数。

固定业务口径：

- 一头牛限定 4 个乳头；
- 乳头识别数量来自日志 `NippleNum[N]`；
- 每头牛最多按 4 个计，`N > 4` 单独报告 over-detection；
- 3D 坐标、`IsValid`、3D 转换成功数、3D valid count 不参与识别率。

由于检测/推理失败时 JPG/JSON 可能不会保存，JPG/JSON 文件数不能作为总牛数或识别率分母。主 KPI 必须从 CowDisinfect 日志恢复牛周期。

一头牛多轮检测时，不能取 `max(NippleNum)`，也不能把所有帧相加。最终结果必须按实际采用帧绑定：

```text
Start left camera AI detect ... ImgTimeStamp[T], CowOccuredCount[C], DetectingNumCurRound[R]
        ↓
Left camera cow [C] detecting [R] finished ... NippleNum[N]
        ↓
New cow detecte finished ... LastImgTimeStamp[T]
```

因此该牛最终 2D 识别数为 `LastImgTimeStamp[T]` 对应帧的 `NippleNum[N]`。

至少输出：

- `total_cows`：时间窗内开始命名检测周期的唯一牛周期数；
- 最终 2D 数量分布；
- `complete_four_nipple_cows`；
- `complete_four_nipple_rate`；
- `capped_2d_detections = Σ min(final_count,4)`；
- `expected_nipples = total_cows × 4`；
- `nipple_recognition_rate = capped_2d_detections / expected_nipples`；
- unfinished / missing final result / over-detection 数据质量计数。

保存的 JPG/JSON 只作为辅助结果证据；若 artifact 目录完整，可核对 `LastImgTimeStamp` 是否存在对应文件，以及 JSON 的 2D marker `1..4` 是否与日志最终 `NippleNum` 一致。

边界：系统完全漏掉、从未进入命名检测周期的真实奶牛不能由当前日志凭空恢复，后续需 RFID/视频/其他独立 ground truth。

### R19 encoder-health

V1 只判断编码器数据健康，不做漏牛/牛位业务推断。

至少输出：

- invalid/read failure；
- sampling gap；
- negative raw jump；
- large negative jump candidate；
- positive delta statistical outlier candidate；
- flat raw candidate；
- raw/filtered 差异摘要。

历史脚本阈值不自动升级成协议事实；无效值、物理速度/距离、reset 语义后续按真实设备/固件形成 profile。

### R20 log-context

日志是公共上下文，不是万能入口。

要求：

- 由主业务能力先给时间/对象/异常候选；
- 再按显式日志 + 小时间窗/关键词捞原始行；
- 保留 source / line_no / timestamp / raw；
- 无 anchor 时返回无证据，不无限扩大窗口；
- log-context 本身不下根因结论。

详细口径见 `docs/business/01-business-capabilities-v1.md`。

## 4. 数据与 Sandbox

- 业务数据以只读 bind 暴露，例如 `/agent-data`；
- 每 Task 有 host-backed、task-local、可写 `/task-scratch`；
- Scratch 用于临时脚本和有界中间产物，不自动成为原始事实；
- Runtime Sandbox 默认 `network=none`；
- 通用依赖在镜像 build 阶段准备，不在任务中在线安装；
- analysis sandbox 预装 numpy/scipy/pandas/cv2/Pillow/scikit-image/matplotlib/openpyxl/PyYAML/psutil/scikit-learn；Open3D 可选；
- `/opt/scopex/toolbox.json` 记录实际工具箱；
- 复杂 Python/Node 分析优先使用已有 Skill 脚本，或先写 `/task-scratch/<file>.py` 再直接执行，不默认用 inline heredoc/复杂 interpreter pipeline。

## 5. Task 范围与停止

1. 用户明确的一张图、一个日志、一个时间窗、一个设备或“不要查日志”等要求属于范围约束；
2. 主数据源优先；
3. 当前范围不足时才最小扩展；
4. 不因为 `/agent-data` 有更多数据就自动扫完；
5. 证据够后停止工具调用；
6. 允许 `unknown/待验证`，不能以无限调查替代不确定性。

## 6. Skill 与稳定脚本

默认内置：

```text
system-health
image-quality-diagnosis
nipple-recognition-analysis
encoder-health
log-context
```

旧 `cow-disinfect-diagnosis` 继续保留作历史/专项回归，但不再默认加载。

Skill 可包含：业务术语、证据边界、推荐方法、停止条件、稳定脚本入口。

稳定脚本必须：

- 只做确定性读取/计算/变换或显式标注“candidate”；
- 输入范围明确；
- 输出紧凑可审计；
- 不默认扫描整个数据目录；
- 不自行联网安装依赖；
- 不把统计异常直接等同于物理根因。

## 7. Evidence / Final Result

```text
Raw Tool Result / Original Image
        ↓
Evidence Snapshot
        ↓
Fresh Structured Finalizer
        ↓
Validated Claims
        ↓
Claim-bounded Product Answer
        ↓
Result-first UI

Deterministic Renderer = audit/trust fallback
```

关键规则：

1. Evidence 是 claim-grade 投影，不是 Transcript；
2. runtime-control warning/guard 文本不能当业务 Evidence；
3. 图片 Evidence 记录原图身份和 SHA，Fresh Finalizer 重新解析；
4. Finalizer 只能基于现有 Evidence 形成 Claims；
5. Product Answer 不能增加 Claims/Evidence 不支持的事实；
6. 文本/命令 observed fact 的用户可见事实必须来自原始 Evidence，而不是模型 topic 的自由改写；
7. Finalizer 输出有界；仅 `finish_reason=length` 时允许同 Evidence 一次无工具长度恢复。

## 8. 产品运行边界

v0.1：

- 单用户，同一时间一个主要 Task；
- PAUSED Task 保留 Session；
- Runtime API 只绑定 loopback；
- 原始数据默认只读；
- 不做多 Agent 编排、Kubernetes、工作流编辑器、独立重型监控平台；
- Memory Search 暂不启用；
- 高风险机器人/设备动作必须另有 capability/权限策略。

## 9. 端侧部署要求

现场网络不能是正常运行前提。

部署分两层：

```text
Device Base Package
  DGX OS / Docker / NVIDIA runtime / OpenClaw / vLLM image / model weights

ScopeX Update Bundle
  fixed source / frontend dist / Python wheelhouse / analysis sandbox / Skills / manifest
```

要求：

- ARM64 架构一致；
- ScopeX bundle 有 commit/架构/image ID/SHA256；
- Python 用 `--no-index --find-links` 离线安装；
- 现场不执行 npm install；
- Docker/vLLM image 用 save/load；
- 模型记录 `MODEL_REPO + MODEL_REVISION + served model id`；
- vLLM image 固定 tag/digest；
- 旧版本代码、Sandbox、vLLM/model 资产保留可回滚。

完整流程见 `docs/09-zero-to-one-build-and-offline-deployment.md`。

## 10. 性能与复杂任务 Gate

当前默认：

```text
OpenClaw turn timeout = 600 s
model requests / turn = 16
context baseline = 32768
```

Step 6F 已在当前本地模型路径上以 `371.1 s / 14 requests` 通过既定综合任务。该结果证明当前基线可用，不代表任何新模型或未知业务任务自动满足同样 Gate。

## 11. 完成定义

一个任务正确完成至少满足：

- 使用真实主数据源；
- 不无理由越过用户范围；
- 事实、推断、未知分开；
- 动作前后证据完整（如有动作）；
- Fresh Finalizer / Claim Validator 有效；
- Answer 未增加不受支持事实；
- 原始输入未被修改；
- 证据足够时停止；
- 业务脚本输出可以独立复算/测试。

## 12. 当前验收主线

1. Python 全量单测 + Business Skill 专项测试；
2. Vue build；
3. Spark ARM64 analysis sandbox build；
4. system metrics timer + system-health 真实历史窗口；
5. 真实一小时轮转日志人工复算 `total_cows / final 2D NippleNum / nipple_recognition_rate`，并用保存的 JSON/JPG 做辅助抽查；
6. 真实编码器日志验证 candidate 事件和 log-context；
7. 单图范围任务；
8. FastAPI + Vue 真实业务闭环；
9. Device Base + ScopeX Update 双层离线 smoke；
10. Stop / Resume / Steering / refresh-reconnect。
