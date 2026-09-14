# ScopeX 产品需求基线

状态：**2026-09-14 当前有效基线**。

## 1. 项目目标

在 NVIDIA DGX Spark 上交付本地、可交互、证据可追溯、可执行的工业 Agent Runtime。

当前固定基线：

- Agent Runtime：OpenClaw；
- 当前已验证 served model id：`qwen3.8-27b-nvfp4`；
- 推理：本地 vLLM OpenAI-compatible `/v1`；
- 产品：ScopeX Runtime + FastAPI + Vue；
- 业务数据/日志/图片/审计默认本地处理。

> **OpenClaw owns execution. ScopeX owns product control and trust.**

OpenClaw + 模型负责调查、工具选择、执行、验证和停止；ScopeX 负责 Task/Session、范围、能力、权限、预算、Evidence、审计、可信结果、时间触发和产品 API/UI。不得在 ScopeX 中重做第二套 Agent Loop / Workflow Engine。

## 2. 通用产品需求

| ID | 需求 | 当前验收口径 |
|---|---|---|
| R01 | 本地执行链 | OpenClaw、vLLM、文件/Shell/图片、Evidence、Finalizer 本地完成；Sandbox 默认无网络 |
| R02 | 自主调查 | 用户给目标后模型自主决定调查顺序/工具，不要求逐步教操作 |
| R03 | 大数据工作集 | 大日志/CSV 不直接灌 Context；先筛选/计算/有界摘要 |
| R04 | 多图分析 | 大图片集先筛选；最终视觉结论依赖的原图实际查看且集合有界 |
| R05 | 可执行动作 | 高风险动作必须经过 capability / permission boundary |
| R06 | 动作后验证 | exit code 0 不等于业务成功；动作后验证真实状态 |
| R07 | 上下文持续 | 使用 OpenClaw 原生 compaction；Context 不是原始数据仓库 |
| R08 | 可接管 | Stop / Resume / Steering 保持有效 Session |
| R09 | 防失控 | request/time budget 单一来源；原生 loopDetection 防工具死循环 |
| R10 | 可信输出 | 正式业务事实来自 claim-grade Evidence / 原图重新验证 |
| R11 | 可读结果 | 主视图使用业务语言；原始 Evidence/字段下沉详情；可读化不得增加未支持事实 |
| R12 | 可审计复测 | Task/Trace/Evidence/Claims/Result/评价/关键版本可回归 |
| R13 | 范围与停止 | 显式 target/source/scope 是约束；最短充分证据；够证据即停止 |
| R14 | Skill / 稳定脚本 | Skill 提供业务语义/稳定原语，但不替代 Agent Loop |
| R15 | 离线部署 | Device Base 与 ScopeX Update 分层，可离线安装/回滚 |

## 3. 统一 Conversation / Task Runtime

### R16 Conversation / Task 共用底层

产品上区分 `conversation` 与 `task`，但必须共用：

```text
TaskService -> OpenClaw -> Skill/Tool -> Progress/Audit
```

只允许结果策略不同：

- Conversation：普通问答正常完成时允许没有 Evidence；
- Task：正式业务结论仍要求 Evidence → Finalizer → Claims → Product Answer。

不得为了聊天再建立第二个 Chat Agent / Chat Runtime。

### R17 Run 生命周期

每次执行至少记录：

```text
mode
trigger_type
schedule_id (optional)
scheduled_for (optional)
started_at
finished_at
duration_ms
```

UI 显示开始、结束、耗时、手动/定时触发和任务状态。

## 4. 简单定时触发

### R18 Schedule

定时任务只是普通 Task 的 Trigger，不是 Workflow。

V1 支持：

- 每 N 分钟；
- 每天 HH:MM；
- 一次执行；
- 启用/停用；
- 立即执行；
- 上次执行/状态；
- 下次执行。

到点只调用普通 `TaskService.create_task()`。Scheduler 不决定 Skill、步骤、诊断逻辑。

当前单机只允许一个主要执行槽位；到点时 busy，则本次记 `SKIPPED_BUSY`，不偷偷排队。页面“立即执行”不改变原定周期。

## 5. 评价与复盘

### R19 Task Evaluation

终态 Task 支持：

- 👍 正确；
- 👎 有问题；
- 问题标签：结果错误、分析不完整、范围过大、耗时过长、Skill 错误、工具失败、难以理解、证据不足、其他；
- 可选说明。

评价是优化证据，不自动修改 Prompt/Skill。

### R20 Review Export

Task 可导出有界 review ZIP，包含存在的 Task/Session/Result/Answer/Claims/Evidence/Events/Evaluation/错误审计文件和 manifest。默认不打包整份外部业务日志/图片，避免导出失控。

目标是让更大模型区分：Model / Skill / Tool / Runtime / Evidence / Finalizer / UI 哪一层需要优化，而不是静默重新做业务诊断。

## 6. 第一批业务能力 V1

默认 Built-in Skills：

```text
system-health
image-quality-diagnosis
nipple-recognition-analysis
encoder-health
log-context
```

网络能力暂缓，必须先确认 topology、节点、链路、协议和业务依赖。

### R21 system-health

只分析**当前** DGX Spark 宿主机资源：CPU/load、内存、磁盘、GPU、Docker、关键进程。

实现边界：

```text
Task 创建
  ↓
host current snapshot
  ↓ read-only
/scopex-host/current.json
```

要求：

- 不启用 30 秒资源 timer；
- 不保存 CPU/内存/磁盘/GPU 历史；
- 不做历史资源分析；
- Agent 仍 `exec_host=sandbox`；
- 禁止用 Sandbox `/proc/free/df/nvidia-smi` 冒充 Spark host；
- current snapshot 缺失/某字段采集失败时必须明确 unknown/unavailable。

### R22 image-quality-diagnosis

- 指定原图判断模糊/起雾/脏污等可见现象；
- 单图优先视觉，不默认复杂 Python；
- 需要量化才使用稳定指标脚本；
- 物理原因不足时输出不确定；
- 跨图确认固定污迹时使用有界原图集合。

### R23 nipple-recognition-analysis

统计对象固定为 **2D `NippleNum` 检测框数量**：

- 一头牛固定 4 个乳头；
- 3D 坐标、`IsValid`、3D valid count 不参与 KPI；
- `NippleNum > 4` 单列过检，指标最多计 4；
- JPG/JSON 在失败路径可能不存在，不能用文件数做总牛数；
- 从 CowDisinfect 日志恢复牛周期；
- 每头牛以 `New cow detecte finished -> LastImgTimeStamp` 对应最终帧的 `NippleNum` 为最终 2D 数；
- 不求和、不取整个周期 max。

核心：

```text
expected_nipples = total_cows × 4
capped_2d_detections = Σ min(final_2d_count, 4)
nipple_recognition_rate = capped_2d_detections / expected_nipples
```

输出总牛数、最终 0/1/2/3/4 分布、四乳头牛数/率、乳头识别率、unfinished/missing result/over-detection。JSON/JPG 仅做成功结果辅助核对。

系统完全漏掉且没有命名牛周期的真实奶牛，需要 RFID/视频/其他 ground truth，不能由日志凭空恢复。

### R24 encoder-health

V1 只做数据健康，不做牛位/漏牛推断。至少识别：invalid/read failure、sampling gap、negative jump、large-negative candidate、positive-delta outlier candidate、flat raw、raw/filtered diff。

历史脚本阈值不自动升级成协议事实；invalid sample 必须打断连续 pair。

### R25 log-context

按明确日志 + 时间/关键词返回 bounded 原始上下文，保留 source/line/time/raw；无 anchor 不无限扩大；自身不下根因。

详细业务口径见 `docs/business/01-business-capabilities-v1.md`。

## 7. 数据与 Sandbox

- 业务数据 read-only bind，例如 `/agent-data`；
- 每 Task 有 host-backed `/task-scratch`；
- Scratch 是临时产物，不自动成为原始事实；
- Sandbox 默认 `network=none`；
- 常用依赖镜像 build-time 预装，不在任务里在线安装；
- toolbox：numpy/scipy/pandas/cv2/Pillow/scikit-image/matplotlib/openpyxl/PyYAML/psutil/scikit-learn，Open3D 可选；
- `/opt/scopex/toolbox.json` 记录实际能力；
- 复杂 Python 优先稳定 Skill 脚本，必要时先写 `/task-scratch/*.py` 再直接执行，避免 inline interpreter preflight。

## 8. 可信结果

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

规则：

1. Evidence 是 claim-grade 投影，不是 Transcript；
2. runtime-control warning 不作业务 Evidence；
3. 图片 Evidence 记录原图身份/SHA；
4. Finalizer 只能基于已有 Evidence；
5. Product Answer 不增加 unsupported facts；
6. 文本/命令 observed fact 保持 Evidence 可信边界；
7. Product Answer 可以做确定性业务字段格式化/百分比/单位，但不能增加新诊断；
8. Finalizer 仅在 `finish_reason=length` 时允许同 Evidence 一次无工具长度恢复。

## 9. 产品运行边界

- 单用户；同一时间一个主要执行 Task；
- PAUSED Task 保留 Session；
- Runtime API loopback only；
- 原始数据默认只读；
- 不做多 Agent/Kubernetes/拖拽工作流/独立重型监控；
- Memory Search 暂不开；
- 高风险机器人/设备动作另设权限 capability。

## 10. 端侧部署

现场网络不能是正常运行前提：

```text
Device Base Package
  DGX OS / Docker / NVIDIA Runtime / OpenClaw / vLLM image / model weights

ScopeX Update Bundle
  source / frontend dist / Python wheelhouse / analysis sandbox / Skills / manifest
```

要求 ARM64 一致、SHA256、commit/image 可追溯、Python `--no-index`、现场不 npm install、Docker/vLLM save/load、模型固定 `MODEL_REPO + MODEL_REVISION + served id`、保留旧版本回滚。

完整流程见 `docs/09-zero-to-one-build-and-offline-deployment.md`。

## 11. 当前性能基线

```text
OpenClaw turn timeout = 600 s
model requests / turn = 16
context baseline = 32768
```

Step 6F 在既定复杂任务上约 `371.1 s / 14 requests` PASS。该结果不自动覆盖新的业务任务/模型。

## 12. 当前验收主线

1. Python 专项 + 全量单测；
2. Vue build；
3. Spark ARM64 analysis sandbox；
4. Conversation 无 Evidence 正常完成；
5. system-health current host snapshot / 无 Sandbox fallback；
6. 完整 1 小时乳头 2D KPI 人工对账；
7. 真实编码器异常与 log-context 对账；
8. 单图范围任务；
9. Schedule interval/daily/once、`SKIPPED_BUSY`、立即执行不改周期；
10. started/finished/duration；
11. 评价 + review ZIP；
12. FastAPI + Vue 真实业务闭环；
13. Device Base + ScopeX Update 离线 smoke / rollback；
14. Stop / Resume / Steering / refresh-reconnect。
