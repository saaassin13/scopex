# 交付、验收与当前状态

状态：**2026-09-14 当前有效版本**。严格区分：

- **PASS**：已有真实实施证据；
- **已实现**：代码已落地，但仍需当前版本回归/真实环境验证；
- **待完成**：尚未完成。

## 1. 当前产品形态

```text
Conversation / Manual Task / Scheduled Trigger
        ↓
FastAPI + TaskService
        ↓
OpenClaw + qwen3.8-27b-nvfp4
        ↓
Business Skills + read / exec / process / view_image
        ↓
Progress / Trace / Audit
        ↓
Conversation result
或
Evidence -> Fresh Finalizer -> Claims -> Product Answer
        ↓
Vue Result-first UI
```

对话、手动任务、定时任务共用同一个 Runtime。Scheduler 只负责到点创建普通 Task，不参与调查/编排。

## 2. 已验证冻结基线

| 能力 | 状态 | 说明 |
|---|---|---|
| Runtime MVP | **PASS** | Task → OpenClaw → Tool → Evidence → Finalizer → Result |
| Stop / Resume / Steering | **PASS** | 同 Session、安全请求边界 |
| Evidence-Calibrated Output | **PASS** | E refs、Claim Validator、deterministic renderer |
| 6A Context / Compaction | **PASS** | OpenClaw 原生 compaction |
| 6B Large Data / Multi-Image | **PASS** | 120k CSV、48 图、有界 working set、task scratch |
| 6C Hard Budget | **PASS** | request/time budget + partial finalization |
| 6D Native Loop Convergence | **PASS** | native loopDetection + Evidence filtering |
| 6E Complex Task Capability | **CAPABILITY PASS** | 多源调查 + constrained action + verification |
| 6F Product-default Gate | **PASS** | 约 `371.1 s / 14 requests`，进入 `600 s / 16 requests` |

Step 6A–6F 继续冻结。

## 3. Step 7 / Product V1

| 能力 | 当前实现 | 状态 |
|---|---|---|
| Product Answer | Claims/Evidence 重新校验 + deterministic readability | **已实现 / 待回归** |
| Result-first UI | 结论/说明/建议置顶；技术错误下沉详情 | **已实现 / 待回归** |
| Conversation mode | 同一 TaskService/OpenClaw；允许正常无 Evidence 对话 | **已实现 / 待验收** |
| Run timing | started/finished/duration/trigger/schedule metadata | **已实现 / 待验收** |
| Simple schedules | interval/daily/once → ordinary Task | **已实现 / 待验收** |
| Feedback | 👍/👎 + tags + note | **已实现 / 待验收** |
| Review export | bounded ZIP，不默认带完整业务原始数据 | **已实现 / 待验收** |
| Spark FastAPI + Vue | 真实产品集成 | **进行中** |

当前 Product Answer 已能把常见业务标量转成正常语言，例如 `nipple_recognition_rate=0.978311` → “乳头识别率为 97.83%”，但仍保持 Claims/Evidence 边界，不进行第二次诊断。

## 4. 第一批业务能力 V1

默认 Skills：

```text
system-health
image-quality-diagnosis
nipple-recognition-analysis
encoder-health
log-context
```

### 4.1 system-health — 已实现 / 待 Spark 验收

当前设计已经从“30 秒历史采集”收缩为**当前快照**：

```text
Task 创建
  ↓
ScopeX host snapshot
  ↓
<task-work>/host/current.json
  ↓ read-only bind
/scopex-host/current.json
```

已删除资源 history timer/service、`system_metrics.jsonl` 和历史 summary。Agent 仍运行在 Sandbox，禁止 fallback 到 Sandbox `/proc/free/df/nvidia-smi`。

待验收：Spark 上 CPU/内存/磁盘/GPU/Docker/进程字段真实形态和采集错误路径。

### 4.2 image-quality-diagnosis — 已实现 / 继续真实验收

原图 direct `view_image` + bounded metrics；单图任务不默认 `exec`、不扫无关数据、证据够停止。

### 4.3 nipple-recognition-analysis — 已按真实业务纠正 / 待 1 小时对账

正式口径：

- 一头牛固定 4 个乳头；
- KPI 统计 2D `NippleNum`，不统计 3D nipple validity；
- JPG/JSON 在失败路径可能不存在，不能做总牛数分母；
- 从日志恢复牛周期；
- `New cow detecte finished -> LastImgTimeStamp` 回挂最终采用帧的 `NippleNum`；
- 不取周期 max；
- `>4` 单列 over-detection、指标最多计 4；
- unfinished cycle 不从保守分母消失。

工具：`skills/nipple-recognition-analysis/scripts/nipple_stats.py`。

### 4.4 encoder-health — 已实现 / 待真实日志验收

invalid/read failure、sample gap、negative jump、large-negative candidate、positive delta outlier candidate、flat raw、raw/filtered diff；invalid sample 打断连续 pair。

### 4.5 log-context — 已实现

单遍流式、有界 raw context；anchor 优先；不独立判根因。

## 5. 定时任务 V1

一个 Schedule 仅保存普通任务消息 + 简单时间规则：

```text
interval
每天 HH:MM
once
```

到点调用 `TaskService.create_task(... trigger_type="schedule")`。已有任务运行时本次记录 `SKIPPED_BUSY`，不排队。页面“立即执行”是额外执行，不改变既定 `next_run_at`。

典型：

- 每天检查当前磁盘；
- 每 30 分钟检查过去 30 分钟编码器；
- 每 30 分钟检查过去 30 分钟图片。

## 6. 评价与 Review Export

终态 Task 可记录：rating、问题 tags、note。Review ZIP 包含存在的 task/session/result/answer/claims/evidence/events/evaluation 和运行时错误审计；默认不塞入整小时原始日志/图片。

Review manifest 明确要求大模型区分 Model / Skill / Tool / Runtime / Evidence / Finalizer / UI，而不是悄悄重做业务诊断。

## 7. Analysis Sandbox / 部署

目标镜像：`scopex-sandbox-analysis:step7`，预装 numpy/scipy/pandas/OpenCV/Pillow/scikit-image/matplotlib/openpyxl/PyYAML/psutil/scikit-learn，Open3D 可选。

部署分：

```text
Device Base Package
  DGX OS / Docker / NVIDIA Runtime / OpenClaw / vLLM / model

ScopeX Update Bundle
  source / frontend dist / wheelhouse / analysis sandbox / Skills / checksum
```

`docs/09-zero-to-one-build-and-offline-deployment.md` 已覆盖 Docker、vLLM、模型 repo/revision、hf download、离线 save/load、TUNA、systemd、rollback。

当前事实缺口仍是现用 `qwen3.8-27b-nvfp4` 的真实 `MODEL_REPO + MODEL_REVISION` 尚未补录。

## 8. 当前验收 Gate

只有实际证据后才改 PASS：

1. 专项 Python tests；
2. Python 全量 tests；
3. Vue `npm run build`；
4. Spark ARM64 sandbox/toolbox；
5. 普通 Conversation 无 Evidence 正常完成；
6. system-health current host snapshot 且无 Sandbox fallback；
7. 完整一小时乳头 2D KPI 人工复算；
8. 真实编码器 candidate 与原始行一致；
9. 单图范围任务；
10. schedule interval/daily/once、busy skip、run-now cadence；
11. started/finished/duration；
12. 评价 + review ZIP；
13. FastAPI + Vue 真实业务 Task；
14. Stop / Resume / Steering + refresh/reconnect；
15. Device Base + ScopeX Update 离线 smoke + rollback。

## 9. 当前已知缺口

- Product V1 新代码尚未跑当前版本完整 Python/Vue 回归；
- current host snapshot 尚需 Spark 实测；
- 完全漏检且无命名检测周期的牛缺 ground truth；
- 编码器 invalid/reset/物理阈值需要 firmware/site profile；
- 网络 topology 未确认；
- current model 的真实 `MODEL_REPO + MODEL_REVISION` 未补录；
- frontend 暂无 npm lockfile；
- Open3D ARM64 非强制；
- task scratch retention/cleanup 仍需真实复测。

## 10. 验收原则

- 不扩大 budget 掩盖行为问题；
- 不降低任务要求换通过率；
- 不在 Runtime Handler 写固定业务 Workflow；
- 不把旧脚本经验阈值当协议事实；
- 不把模型话术当原始 Evidence；
- 不把 3D nipple 混入 2D KPI；
- 不把 Sandbox 资源冒充 host；
- 用户范围优先，够证据即停；
- 代码已实现 ≠ PASS。
