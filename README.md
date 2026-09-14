# ScopeX

ScopeX 是运行在 NVIDIA DGX Spark 上的本地工业 Agent Runtime。OpenClaw + 本地模型负责自主调查、工具选择、动作和验证；ScopeX 负责 Task/Session、范围/权限、Progress、Stop/Resume/Steering、Evidence、审计、可信结果、定时触发和产品 API/UI。

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
| Business V1 | system / image / nipple 2D KPI / encoder / log-context | **已实现第一版，待真实业务验收** |
| Product V1 | Conversation / Task / Schedule / timing / feedback / export | **已实现第一版，待验收** |
| Spark FastAPI + Vue | 真实产品集成 | **进行中** |

Step 6 是冻结基线，不因后续产品功能变化而重新解释。

## 产品执行模型

所有入口共用同一底层 Runtime：

```text
Conversation / Manual Task / Scheduled Trigger
        ↓
TaskService
        ↓
OpenClaw + local model
        ↓
Skills / read / exec / process / view_image
        ↓
Progress / Trace / Audit
        ↓
Result policy
```

区别只在产品结果策略：

- `conversation`：普通问答允许没有 Evidence；
- `task`：正式业务结论必须经过 Evidence → Fresh Finalizer → Validated Claims → Product Answer；
- `schedule`：只负责到点创建一个普通 `task`，不是 Workflow Engine。

当前单机 V1 仍保持**一个主要执行槽位**。定时触发时已有任务运行，则本次记录 `SKIPPED_BUSY`，不偷偷排队。

## 第一批业务能力

默认 Built-in Skills：

```text
system-health
image-quality-diagnosis
nipple-recognition-analysis
encoder-health
log-context
```

旧 `cow-disinfect-diagnosis` 仍保留做历史/专项回归，但不再默认加载。

### system-health

只看**当前** DGX Spark 宿主机资源，不保存 CPU/内存/磁盘/GPU 历史。

每个 Task 创建时 ScopeX 在宿主机生成一次快照并只读挂载：

```text
<task-work>/host/current.json
        ↓
/scopex-host/current.json
```

Agent 仍运行在 Sandbox；禁止拿 Sandbox `/proc/free/df/nvidia-smi` 冒充宿主机状态。当前快照不可用时必须明确返回不可用。

### image-quality-diagnosis

原图优先直接 `view_image`。单图不默认跑复杂 Python；需要量化时才使用稳定指标脚本。模糊可作为观察事实，起雾/脏污等物理原因证据不足时允许 unknown。

### nipple-recognition-analysis

乳头识别 KPI 固定为 **2D 检测框 `NippleNum`**：

- 每头牛物理上限 4 个；
- 3D 坐标、`IsValid`、3D valid count 不参与 KPI；
- 检测/推理失败时 JPG/JSON 可能不存在，因此文件数量不能作为总牛数；
- 总牛数从 CowDisinfect 日志牛周期恢复；
- 每头牛由 `New cow detecte finished -> LastImgTimeStamp` 回挂到最终采用帧的 `NippleNum`；
- 不简单取一头牛全部帧的 max；
- `NippleNum > 4` 单列过检，KPI 最多计 4。

### encoder-health

V1 只做编码器数据健康：invalid/read failure、sampling gap、negative jump、large-negative candidate、positive-delta outlier candidate、flat raw、raw/filtered diff。历史经验阈值不自动升级成硬件事实。

### log-context

只按明确日志 + 时间/关键词提供 bounded raw context，不独立下根因。

详细业务口径见 `docs/business/01-business-capabilities-v1.md`。

## 产品 V1

### 对话与任务

首页支持 Task / Conversation 切换，但两者底层走同一个 TaskService/OpenClaw Runtime。正常对话如“当前有哪些 Skill”不再因为 Evidence 为空而失败。

### 定时任务

支持：

```text
每 N 分钟
每天 HH:MM
一次执行
```

例如：

```text
每天 08:00     检查当前磁盘使用
每 30 分钟     检查过去30分钟编码器
每 30 分钟     检查过去30分钟图片质量
```

### 执行记录

Task 记录：`mode / trigger_type / schedule_id / scheduled_for / started_at / finished_at / duration_ms`，UI 展示开始、结束、耗时和触发来源。

### 评价与导出

终态任务支持 👍 / 👎、问题标签和说明；并可以导出 review ZIP，用于交给更大模型分析 Model / Skill / Tool / Runtime / Evidence / Finalizer / UI 哪一层需要优化。默认不打包整份外部原始图片/日志。

详细设计与实现状态见 `docs/10-chat-tasks-scheduling-and-feedback.md`。

## 可信输出链

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

Product Answer 当前增加了受约束的确定性可读化，例如把 `nipple_recognition_rate=0.978311` 展示为“乳头识别率为 97.83%”，但不允许增加 Claims 未支持的事实。

## Analysis Sandbox

目标镜像：

```text
scopex-sandbox-analysis:step7
```

运行期网络 `none`，build 阶段预装：numpy / scipy / pandas / OpenCV / Pillow / scikit-image / matplotlib / openpyxl / PyYAML / psutil / scikit-learn；Open3D 为 ARM64 可选项。实际能力写入 `/opt/scopex/toolbox.json`。

Dockerfile build-time APT/PyPI 使用清华 TUNA；不修改 Spark 宿主机系统源。

## 部署与离线

部署分两层：

```text
Device Base Package（低频）
  DGX OS / Docker / NVIDIA Runtime / OpenClaw / vLLM image / model weights

ScopeX Update Bundle（高频）
  fixed source / frontend dist / Python wheelhouse / analysis sandbox / Skills / manifest
```

当前已验证 served model id：

```text
qwen3.8-27b-nvfp4
```

真正从 0→1 重建还必须补录当前模型实际 `MODEL_REPO + MODEL_REVISION`。完整 Docker、vLLM、模型下载、离线搬运、systemd 和回滚见 `docs/09-zero-to-one-build-and-offline-deployment.md`。

## 文档入口

- `docs/01-requirements.md` — 产品需求基线；
- `docs/02-delivery-and-acceptance.md` — 当前状态 / Gate；
- `docs/architecture/06-openclaw-scopex-boundary.md` — OpenClaw / ScopeX 边界；
- `docs/architecture/07-complex-task-validation-and-next-plan.md` — 当前实施路线；
- `docs/business/01-business-capabilities-v1.md` — 第一批业务能力；
- `docs/08-local-usage-and-handoff.md` — 本地使用 / 接手；
- `docs/09-zero-to-one-build-and-offline-deployment.md` — 0→1 与离线部署；
- `docs/10-chat-tasks-scheduling-and-feedback.md` — 对话 / 定时任务 / 评价 / 导出。

## 当前验收顺序

1. Python 专项 + 全量单测；
2. Vue `npm run build`；
3. Spark ARM64 analysis sandbox/toolbox；
4. Conversation 无 Evidence 正常完成；
5. 当前 system-health 使用 `/scopex-host/current.json` 且无 Sandbox fallback；
6. 完整一小时乳头 2D KPI 人工对账；
7. 真实编码器异常与 log-context 对账；
8. 定时任务 interval/daily/once、`SKIPPED_BUSY` 和立即执行不改周期；
9. Task started/finished/duration；
10. 评价 + review ZIP；
11. FastAPI + Vue 真实业务闭环；
12. Device Base + ScopeX Update 离线 smoke / rollback。

在这些 Gate 有实施证据前，状态保持“已实现 / 待验收”，不因为代码存在就自动写成 PASS。
