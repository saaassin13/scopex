# ScopeX

ScopeX 是运行在 NVIDIA DGX Spark 上的本地工业 Agent Runtime。OpenClaw + 本地模型负责自主调查、工具选择、动作和验证；ScopeX 负责 Run/Session、范围/权限、Progress、Stop/Resume/Steering、事实 Evidence、审计、可信结果、时间触发和产品 API/UI。

> **OpenClaw owns execution. ScopeX owns product control and trust.**

## 当前状态

截至 **2026-09-14**：

| 阶段 | 能力 | 状态 |
|---|---|---|
| Runtime MVP | OpenClaw + vLLM + Evidence + Finalizer + Audit | **PASS** |
| Step 6A–6D | Context / Large Data / Budget / Native Loop | **PASS** |
| Step 6E | 综合复杂任务能力 | **CAPABILITY PASS** |
| Step 6F | 600s / 16-request 产品 Gate | **PASS** |
| Step 7 Result | Claim-bounded Product Answer + Result-first UI | **已实现，待当前版本回归** |
| Business V1 | system / image / nipple 2D KPI / encoder / bounded log context | **已实现第一版，待真实业务验收** |
| Product V1 | unified run / schedule / calendar / timing / feedback / export / delete | **已实现第一版，待验收** |
| Spark FastAPI + Vue | 真实产品集成 | **进行中** |

Step 6 是冻结基线，不因后续产品功能变化而重新解释。

## 用户只有一个入口

用户不需要判断自己是在“聊天”还是“执行任务”。手动输入统一走：

```text
POST /runs
   ↓
mode=auto
   ↓
同一个 TaskService / OpenClaw Runtime
   ↓
根据实际执行行为内部收敛结果策略
```

- 没有触碰业务数据/业务能力，OpenClaw 正常回答：内部落成 `conversation`；
- 已读取 `/agent-data`、`/scopex-host`、原图，或执行正式业务脚本：内部落成 `task`；
- 已尝试业务调查但没有形成可信业务事实：**不得降级成聊天回答**；
- Schedule 从一开始就是正式 `task`。

没有独立 Chat Agent、Router Model、Schedule Agent 或 Workflow Engine。

当前单机 V1 仍保持**一个主要执行槽位**；并发将在大目录访问和业务事实层完成真实验收后再扩展。

## 真实业务数据目录

ScopeX 维护统一 Data Catalog：

```text
config/data-catalog.json
```

当前真实宿主机数据源：

```text
/opt/ScalingRobotics/CowDisinfect/Log
  CowDisinfect-YYYYMMDD-HHMMSS.log[.N]

/opt/ScalingRobotics/CowDisinfect/GrabbedImages/LeftCamera
  YYYYMMDD/HH/
    YYYYMMDD-HHMMSSmmm.jpg
    YYYYMMDD-HHMMSSmmm.json
    YYYYMMDD-HHMMSSmmm.pcd
```

Sandbox 固定逻辑路径：

```text
/agent-data/logs
/agent-data/left-camera
```

Runtime 启动时把 Catalog 复制为：

```text
/workspace/scopex-data-catalog.json
```

如果 Catalog 的 host path 存在，Runtime 默认自动只读挂载；额外目录仍可用 `--data-dir` 添加/覆盖。

### 大目录访问原则

- 普通时间窗任务禁止默认递归 `find / grep -R / du -a / rg --files` 整个 `/agent-data`；
- 日志通过 `data-locator` 只选择和目标时间窗重叠的日志文件组；日志文件起始时间可以不是自然整点，例如 `10:23:36` 开始的文件组仍可能覆盖 11 点数据；
- LeftCamera 直接定位目标 `YYYYMMDD/HH`，不扫描历史日期目录；
- PCD 必须先定位具体文件，再读取/降采样；
- 稳定业务脚本 stdout 只输出紧凑 `business_facts`，完整明细写 `/task-scratch`。

详见 `docs/business/02-data-catalog-and-bounded-access.md`。

## 第一批业务能力

默认 Built-in Skills：

```text
data-locator
system-health
image-quality-diagnosis
nipple-recognition-analysis
encoder-health
log-context
```

`data-locator` 是内部支持能力，不下业务结论。

### system-health

只看**当前** DGX Spark 宿主机资源，不保存 CPU/内存/磁盘/GPU 历史。每个 Run 创建时生成一次宿主机快照并只读挂载到 `/scopex-host/current.json`。禁止拿 Sandbox `/proc/free/df/nvidia-smi` 冒充宿主机状态。

### image-quality-diagnosis

原图优先直接 `view_image`。时间窗任务先定位目标小时图片；单图不默认跑复杂 Python。需要量化才使用稳定指标脚本。

### nipple-recognition-analysis

乳头识别 KPI 固定为 **2D 检测框 `NippleNum`**：

- 每头牛物理上限 4 个；
- 3D 坐标、`IsValid`、3D valid count 不参与 KPI；
- JPG/JSON 在失败路径可能不存在，不能作为总牛数；
- 总牛数从 CowDisinfect 日志牛周期恢复；
- 每头牛以 `New cow detecte finished -> LastImgTimeStamp` 对应最终帧 `NippleNum` 为最终 2D 数；
- 不求和、不取周期 max；`>4` 单列过检，KPI cap=4。

### encoder-health

正式路径是：

```text
明确时间窗
  ↓
data-locator 选择相关 .log / .log.N
  ↓
encoder_health.py 一次处理全部显式日志文件
  ↓
compact business_facts
  ↓
仅对显著候选按需补小窗口 log-context
```

所有小 `delta<0` 只做次数/幅度分布，不再逐条制造数百 Evidence；显著异常候选才进入 Top N。

## Evidence 分层

当前明确区分：

```text
Trace / 调查过程
  Skill、脚本源码、Locator、命令、模型请求

Working Data
  /task-scratch 中间结果

Claim-grade Evidence
  原始业务日志行 / 原图 / host snapshot / structured business_facts

User Facts
  页面展示给用户的事实依据
```

因此 `/workspace/skills/**`、Data Catalog 文本、`/task-scratch/**` 和 locator 输出不再成为用户 Evidence。稳定业务脚本的一整个紧凑 `business_facts` 只形成一条结构化 Evidence，不再按 256 行拆碎。

## 定时任务

支持：

```text
每 N 分钟
每天 HH:MM
一次执行
```

Schedule 只保存普通任务内容 + 时间规则，到点调用普通 Task 流程，不编排业务步骤。

### 断电 / 重启

设备离线期间错过的定时触发**全部跳过，不补跑**：

```text
missed_count += N
last_missed_at = 最后错过时间
next_run_at = 下一个未来时间点
```

不会为历史错过时间创建 Task Run。一次性任务若过期则标记 `MISSED_OFFLINE` 并停用。

当前在线到点若已有 Run 占用唯一执行槽位，仍记 `SKIPPED_BUSY`；并发/在线队列作为下一阶段讨论项。

## 执行记录

首页采用：

```text
月份日历
  ↓ 点击日期
当天全部 Run
  ↓
Run 详情
```

日期显示执行总数以及失败/运行状态。Run 记录 `trigger_type / schedule_id / scheduled_for / started_at / finished_at / duration_ms`。

终态 Run 可删除。删除只清理 ScopeX 自有：audit / result / facts / events / scratch / host snapshot / runtime work / review ZIP；**不会删除 `/agent-data` 原始日志、图片、JSON、PCD。**

## 评价与复盘

终态 Run 支持 👍 / 👎、问题标签和说明，并可导出有界 review ZIP，用于让更大的模型区分 Model / Skill / Tool / Runtime / Evidence / Finalizer / UI 哪一层需要优化。默认不打包整份外部业务原始数据。

## 可信输出链

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

Trace / Working Data 留作技术复盘，不直接作为用户事实
```

Product Answer 只做受约束的确定性可读化，不允许增加 Claims 未支持的事实。

## Analysis Sandbox

目标镜像 `scopex-sandbox-analysis:step7`，运行期网络 `none`。OpenClaw Sandbox 已有硬资源保护：默认 512 MiB memory/swap、1 CPU、256 PIDs、单次 exec 30s、read-only root、cap-drop ALL。通用分析包在镜像构建期准备。

对于后续重型 PCD 分析，512 MiB 是否足够必须用真实 PCD 负载验证；不要为了预防性扩容直接提高所有任务的全局资源上限。

## 部署与离线

部署分两层：

```text
Device Base Package
  DGX OS / Docker / NVIDIA Runtime / OpenClaw / vLLM image / model weights

ScopeX Update Bundle
  fixed source / frontend dist / Python wheelhouse / analysis sandbox / Skills / manifest
```

当前已验证 served model id：`qwen3.8-27b-nvfp4`。真正从 0→1 重建仍需补录当前模型真实 `MODEL_REPO + MODEL_REVISION`。

## 文档入口

- `docs/01-requirements.md` — 产品需求基线；
- `docs/02-delivery-and-acceptance.md` — 当前状态 / Gate；
- `docs/architecture/06-openclaw-scopex-boundary.md` — OpenClaw / ScopeX 边界；
- `docs/architecture/07-complex-task-validation-and-next-plan.md` — 当前实施路线；
- `docs/business/01-business-capabilities-v1.md` — 第一批业务能力；
- `docs/business/02-data-catalog-and-bounded-access.md` — 真实数据目录 / Locator / 大数据边界；
- `docs/08-local-usage-and-handoff.md` — 已验收阶段接手基线；
- `docs/09-zero-to-one-build-and-offline-deployment.md` — 0→1 与离线部署；
- `docs/10-chat-tasks-scheduling-and-feedback.md` — 统一 Run / 定时 / 日历 / 评价 / 导出。

## 当前验收顺序

1. 新 Product/Business/Data Catalog 专项测试；
2. Python 全量单测；
3. Vue `npm run build`；
4. Spark ARM64 analysis sandbox/toolbox；
5. 真实两个 Data Catalog 路径自动挂载；
6. 普通能力咨询通过统一 `/runs` 内部收敛为 conversation；
7. 编码器 3 点任务只走 locator + 一次 compact encoder analysis，并完成可信结果；
8. 完整一小时乳头 2D KPI 人工对账；
9. Facts 页面不再出现 Skill.md / 脚本源码；
10. Schedule 重启不补跑历史触发；
11. 月历 / 当天 Run / 安全删除；
12. 评价 + review ZIP；
13. Device Base + ScopeX Update 离线 smoke / rollback；
14. 上述稳定后再进入 bounded concurrency。

在这些 Gate 有实施证据前，状态保持“已实现 / 待验收”，不因为代码存在就自动写成 PASS。
