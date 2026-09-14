# ScopeX 产品需求基线

状态：**2026-09-14 当前有效基线**。

## 1. 项目目标

在 NVIDIA DGX Spark 上交付本地、可交互、证据可追溯、可执行的工业 Agent Runtime。

固定基线：

- Agent Runtime：OpenClaw；
- 当前已验证 served model id：`qwen3.8-27b-nvfp4`；
- 推理：本地 vLLM OpenAI-compatible `/v1`；
- 产品：ScopeX Runtime + FastAPI + Vue；
- 业务数据、模型调用、审计默认在端侧完成。

> **OpenClaw owns execution. ScopeX owns product control and trust.**

OpenClaw + 模型负责调查、工具选择、执行、验证和停止；ScopeX 负责 Run/Session、范围、能力、权限、预算、Facts/Evidence、审计、可信结果、时间触发和产品 API/UI。不得在 ScopeX 中重做第二套 Agent Loop / Workflow Engine。

## 2. 通用产品需求

| ID | 需求 | 当前验收口径 |
|---|---|---|
| R01 | 本地执行链 | OpenClaw、vLLM、文件/Shell/图片、Evidence、Finalizer 本地完成；Sandbox 默认无网络 |
| R02 | 自主调查 | 用户给目标后模型自主决定调查顺序/工具，不要求逐步教操作 |
| R03 | 大数据工作集 | 大日志/CSV/图片/点云不直接全量进入 Context；先定位、筛选、计算、有界摘要 |
| R04 | 多图分析 | 大图片集先筛选；最终视觉结论依赖的原图实际查看且集合有界 |
| R05 | 可执行动作 | 高风险动作必须经过 capability / permission boundary |
| R06 | 动作后验证 | exit code 0 不等于业务成功；动作后验证真实状态 |
| R07 | 上下文持续 | 使用 OpenClaw 原生 compaction；Context 不是原始数据仓库 |
| R08 | 可接管 | Stop / Resume / Steering 保持有效 Session |
| R09 | 防失控 | request/time budget 单一来源；原生 loopDetection；Sandbox 有 CPU/memory/PID/exec-time 硬限制 |
| R10 | 可信输出 | 正式业务事实来自 claim-grade Evidence / 原图重新验证 / 稳定 structured business facts |
| R11 | 可读结果 | 主视图使用业务语言；Trace/原始技术字段下沉；可读化不得增加 unsupported facts |
| R12 | 可审计复测 | Run/Trace/Evidence/Claims/Result/评价/关键版本可回归 |
| R13 | 范围与停止 | 显式 target/source/scope 是约束；最短充分路径；够证据即停止 |
| R14 | Skill / 稳定脚本 | Skill 提供业务语义/稳定原语，但不替代 Agent Loop |
| R15 | 离线部署 | Device Base 与 ScopeX Update 分层，可离线安装/回滚 |

## 3. 用户统一入口与内部结果策略

### R16 Unified Run

用户不需要选择 Conversation / Task。手动输入统一创建：

```text
mode=auto
  ↓
TaskService -> OpenClaw -> Skill/Tool -> Progress/Audit
  ↓
依据实际执行行为内部落成 conversation 或 task
```

禁止额外增加一个 Router Model 只用于分类。

内部规则：

- 没有访问业务数据/业务能力，OpenClaw 正常回答：落成 `conversation`，允许 Evidence=0；
- 已访问 `/agent-data`、`/scopex-host`、原图或正式业务脚本：落成 `task`；
- 已尝试业务调查但未形成可信业务事实：不得降级为普通聊天回答；
- Schedule 永远创建 audited `task`。

显式 `/tasks`、`/conversations` API 可保留做兼容/测试，但普通 UI 只使用统一 `/runs`。

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

UI 显示开始、结束、耗时、手动/定时触发和状态。

## 4. 简单定时触发

### R18 Schedule

Schedule 只保存普通任务内容 + 简单时钟规则，不是 Workflow。

V1 支持：

- 每 N 分钟；
- 每天 HH:MM；
- 一次执行；
- 启用/停用；
- 立即执行；
- 上次执行/状态；
- 下次执行；
- missed count / last missed time。

到点只调用普通 `TaskService.create_task(... mode="task", trigger_type="schedule")`。Scheduler 不决定 Skill、步骤、诊断逻辑。

### 离线/重启语义

设备断电或 ScopeX 未运行期间错过的历史触发**全部跳过，不补跑**：

- interval：统计 missed 次数，直接推进到原相位的下一个未来时间；
- daily：历史日期只计 missed，推进到下一个未来 daily；
- once：已过期则 `MISSED_OFFLINE` 并停用；
- 不为离线历史时间点创建 Task Run。

当前单机仍只有一个主要执行槽位；在线到点时 busy 记 `SKIPPED_BUSY`，不偷偷排队。“立即执行”不改变 recurring `next_run_at`。

## 5. 执行记录、删除、评价与复盘

### R19 Calendar Run History

执行记录主页面按月显示日历：

```text
月历日期格 -> 当日 Run 数量/失败状态
点击日期 -> 当天全部 Run
点击 Run -> Result / Facts / Progress / Feedback
```

日历只基于 ScopeX Run 元数据，不扫描业务原始目录。

### R20 Run Delete

终态 Run 可删除。删除必须级联清理 ScopeX 自有：

- audit/result/claims/evidence/events/evaluation；
- task work / scratch / host snapshot / runtime artifacts；
- 已导出的该 Run review ZIP。

删除函数禁止触及外部只读业务数据源 `/agent-data`。运行中/暂停中/Finalizing Run 不可直接删除。

### R21 Task Evaluation

终态 Run 支持 👍 / 👎、问题标签、可选说明。评价是优化证据，不自动修改 Prompt/Skill。

### R22 Review Export

Run 可导出有界 review ZIP，包含存在的 Task/Session/Result/Answer/Claims/Evidence/Events/Evaluation/错误审计文件和 manifest。默认不打包整份外部业务日志/图片/点云。

目标是让更大模型区分：Model / Skill / Tool / Runtime / Evidence / Finalizer / UI 哪一层需要优化，而不是静默重新做业务诊断。

## 6. 真实 Data Catalog 与有界访问

### R23 Data Catalog

统一配置：

```text
config/data-catalog.json
```

当前真实源：

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

Runtime 启动时把 Catalog 复制到 `/workspace/scopex-data-catalog.json`，声明目录存在时默认只读挂载；显式 `--data-dir` 可追加/覆盖 agent target。

Catalog 是语义目录，不是启动时构建的全量文件索引。

### R24 Data Locator

默认内部支持 Skill `data-locator` 负责按明确时间窗定位文件，不下根因结论。

要求：

- 普通任务禁止递归 `find / grep -R / du -a / rg --files` 整个 `/agent-data`；
- LeftCamera 直接进入目标 `YYYYMMDD/HH`；
- 日志文件名是**文件组起始时间**，可能是 `10:23:36` 这样的非自然整点；查询 11:00 时仍可能需要 `10:23:36` 组；Locator 必须按相邻文件组起始区间重叠选择，而不是简单匹配小时字符串；
- PCD 先定位具体文件，单次默认最多少量文件，禁止全历史 load。

详细见 `docs/business/02-data-catalog-and-bounded-access.md`。

## 7. 第一批业务能力 V1

默认 Built-in Skills：

```text
data-locator
system-health
image-quality-diagnosis
nipple-recognition-analysis
encoder-health
log-context
```

网络能力暂缓，必须先确认 topology、节点、链路、协议和业务依赖。

### R25 system-health

只分析**当前** DGX Spark 宿主机资源：CPU/load、内存、磁盘、GPU、Docker、关键进程。

每 Run 创建时生成 host current snapshot 并只读挂载 `/scopex-host/current.json`。不启用历史资源 timer，不做历史 CPU/memory/GPU 分析；禁止 Sandbox fallback。

### R26 image-quality-diagnosis

- 单图优先原图视觉；
- 时间窗图片先由 data-locator 定位目标小时；
- 不默认扫描 sibling 日志/JSON/历史图片；
- 需要量化才使用稳定指标脚本；
- 物理原因不足时输出 unknown；
- 最终 claim-grade 原图集合有界。

### R27 nipple-recognition-analysis

统计对象固定为 **2D `NippleNum` 检测框数量**：

- 一头牛固定 4 个乳头；
- 3D 坐标、`IsValid`、3D valid count 不参与 KPI；
- `NippleNum > 4` 单列过检，指标最多计 4；
- JPG/JSON 失败路径可能不存在，不能用文件数做总牛数；
- 从 CowDisinfect 日志恢复牛周期；
- `LastImgTimeStamp` 对应最终帧 `NippleNum` 为最终 2D 数；
- 不求和、不取周期 max。

稳定脚本 stdout 只输出 compact `business_facts`；逐牛明细放 `/task-scratch/nipple-details.json`。

### R28 encoder-health

产品路径：

```text
requested time window
 -> data-locator selects explicit rotated files
 -> encoder_health.py analyzes all selected files once
 -> compact business_facts
 -> optional bounded log-context for important candidates
```

要求：

- invalid sample 打断连续 pair；
- 跨显式轮转文件检查连续性；
- 所有小 raw decrease 只统计次数/幅度分布，不逐条提升为异常 Evidence；
- 显著 negative/positive、large negative、gap、flat 才形成 bounded candidates；
- 旧脚本阈值不自动升级成硬件事实。

### R29 log-context

按明确日志 + 时间/关键词返回 bounded 原始上下文，保留 source/line/time/raw；无 anchor 不无限扩大；自身不下根因。

详细业务口径见 `docs/business/01-business-capabilities-v1.md`。

## 8. Evidence / Trace 分层

必须区分：

```text
Trace
  Skill / script source / locator / shell / model requests

Working Data
  /task-scratch intermediate data

Claim-grade Evidence
  raw business log lines / original images / host facts / structured business_facts

User Facts
  UI-visible factual basis derived from claim-grade Evidence
```

固定规则：

1. `/workspace/skills/**` read 不进入 Claim-grade Evidence；
2. `/workspace/scopex-data-catalog.json` read 不进入 Claim-grade Evidence；
3. `/task-scratch/**` read 不进入 Claim-grade Evidence；
4. `scopex_role=locator` 是 Trace-only；
5. `scopex_role=business_facts` 整体成为一条结构化 Evidence，不按 stdout 每行拆成几百 E refs；
6. UI 默认展示“事实依据”，不是 Agent 调查材料；
7. Trace 继续用于技术调查和 Review Bundle。

可信链：

```text
Raw Business Source / Original Image / Stable Business Facts
        ↓
Claim-grade Evidence
        ↓
Fresh Structured Finalizer
        ↓
Validated Claims
        ↓
Claim-bounded Product Answer
        ↓
Result-first UI + User Facts
```

## 9. Sandbox / 资源边界

- 业务数据只读 bind；
- 每 Run 有 `/task-scratch`；
- Runtime network=none；
- 默认 Sandbox limits：512 MiB memory / 512 MiB swap / 1 CPU / 256 PIDs / exec 30s；
- toolbox 在 build 阶段准备；
- 重型 PCD 若真实 workload 超过 512 MiB，不直接全局放大所有任务资源；应基于实测再设计 capability-specific resource profile。

## 10. 端侧部署

现场网络不能是正常运行前提：

```text
Device Base Package
  DGX OS / Docker / NVIDIA Runtime / OpenClaw / vLLM image / model weights

ScopeX Update Bundle
  source / frontend dist / Python wheelhouse / analysis sandbox / Skills / manifest
```

要求 ARM64 一致、SHA256、commit/image 可追溯、Python `--no-index`、现场不 npm install、Docker/vLLM save/load、模型固定 `MODEL_REPO + MODEL_REVISION + served id`、保留旧版本回滚。

## 11. 当前性能基线

```text
OpenClaw turn timeout = 600 s
model requests / turn = 16
context baseline = 32768
```

Step 6F 在既定复杂任务上约 `371.1 s / 14 requests` PASS。该结果不自动覆盖新的业务任务/模型。

## 12. 当前验收主线

1. Data Catalog / Locator / Evidence 分层专项测试；
2. Python 全量单测；
3. Vue build；
4. Spark ARM64 analysis sandbox；
5. 真实 Data Catalog 两个 host path 自动挂载；
6. `/runs` 普通咨询内部落成 conversation；业务调查内部落成 task；
7. 3点编码器使用 locator + 单次 compact analysis 完成；
8. 1小时乳头 2D KPI 人工对账；
9. Facts UI 不显示 Skill/source/scratch；
10. Schedule 重启不补跑历史 missed；
11. Calendar/day list/delete；
12. 评价 + review ZIP；
13. FastAPI + Vue 真实业务闭环；
14. Device Base + ScopeX Update 离线 smoke / rollback；
15. 以上稳定后再进入 bounded concurrency。
