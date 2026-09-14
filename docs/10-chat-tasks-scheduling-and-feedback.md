# ScopeX 统一 Run、定时触发、执行记录与反馈闭环

状态：**2026-09-14 第一版已实现，待 Spark / 前端真实验收**。

> **用户只看到一个 Agent 入口；内部 Conversation / audited Task 共用同一个 Runtime。**

> **定时任务只是 Trigger，不是 Workflow Engine。**

## 1. 统一手动入口

普通用户不需要选择“这是聊天还是任务”。

```text
用户输入
   ↓
POST /runs
   ↓
mode=auto
   ↓
TaskService / OpenClaw / Skills / Tools / Audit
   ↓
依据实际执行行为内部确定结果策略
```

不额外调用 Router Model。

### 自动结果策略

- OpenClaw 正常回答且没有触碰业务数据/业务能力：内部落成 `conversation`；允许没有 Evidence；
- 一旦读取 `/agent-data`、`/scopex-host`、业务原图或正式业务脚本：内部落成 audited `task`；
- 已尝试业务调查但没有形成可信业务事实：不能降级成普通聊天回答；
- 定时触发始终创建 audited `task`。

显式 `/tasks`、`/conversations` 仍保留供兼容、测试和高级调用，但普通 Vue 首页只用 `/runs`。

## 2. 底层仍然只有一套

```text
Manual auto Run / Scheduled Task
        ↓
TaskService
        ↓
OpenClaw + local model
        ↓
Skills / read / exec / process / view_image
        ↓
Trace / Progress / Audit
        ↓
conversation answer
或
Evidence -> Fresh Finalizer -> Claims -> Product Answer
```

没有独立 Chat Agent、Schedule Agent 或 Workflow Runtime。

同一 conversation 的后续消息仍可以复用 OpenClaw `agent_id + session_key`；每轮继续保留独立 Run 记录和耗时。

## 3. 可读业务结果与事实依据

正式业务结果继续保持 Claims/Evidence 可信边界，同时增加确定性业务表达。

例如乳头统计脚本输出 compact `business_facts` 后，页面主结果可以显示：

```text
共统计 438 头牛；
其中 400 头完整识别到 4 个乳头（91.32%）；
总体乳头识别率为 97.83%；
最终2D结果分布：4个乳头 400 头，3个乳头 38 头。
```

编码器可显示：采样数量、有效/无效、采样中位间隔、缺口、raw 下降幅度分布、显著候选数量，而不是把所有 `delta<0` 都称为异常。

页面“事实依据”只展示 Claim-grade business facts / 原始业务日志 / 原图 / host snapshot；Skill 源码、Locator、scratch、普通 Shell 探索属于“调查进度 / 技术记录”。

## 4. Scheduled Task

V1 配置只有：

```text
id
name
message
kind: interval | daily | once
enabled
next_run_at
last_run_at
last_status
last_task_id
missed_count
last_missed_at
```

到点唯一动作：

```text
ScheduleService
    ↓
TaskService.create_task(
  message,
  mode="task",
  trigger_type="schedule",
  schedule_id=...,
  scheduled_for=...,
)
```

Scheduler 不决定 Skill、不编排步骤、不做分析。

支持：每 N 分钟、每天 HH:MM、一次执行。

### 离线 / 断电 / 重启

历史错过触发**永不补跑**。

例如每 30 分钟任务，设备离线错过 14:30 / 15:00 / 15:30 / 16:00，16:20 启动后只更新：

```text
missed_count += 4
last_missed_at = 16:00
next_run_at = 16:30
```

不会生成四个失败/补跑 Task。

- interval 保持原相位推进；
- daily 推进到下一个未来日期；
- once 若过期则 `MISSED_OFFLINE` 并自动禁用。

当前在线时如果唯一执行槽位 busy，触发记 `SKIPPED_BUSY`，不排队。并发/在线队列后续在大目录访问验收稳定后设计。

“立即执行”是额外 Run，不改变 recurring `next_run_at`。

## 5. Run 生命周期

每个 Run 记录：

```text
mode
trigger_type
schedule_id
scheduled_for
started_at
finished_at
duration_ms
```

详情页展示开始、结束、持续时间、手动/定时来源、计划时间。

## 6. 日历执行记录

首页使用：

```text
月历
  ↓ 点击某日
当天 Run 列表
  ↓ 点击一条
Run 详情
```

日期格展示总数以及失败/运行状态。日历只扫描 ScopeX 自己的 Task metadata，不访问 `/agent-data`。

API：

```text
GET /tasks/calendar?month=YYYY-MM
GET /tasks?day=YYYY-MM-DD
```

当前仍是文件系统审计存储；真实 Run 数量未证明需要数据库前，不提前引入 SQLite 任务索引。若长期积累后日历扫描成为瓶颈，再增加轻量索引。

## 7. Run 删除

只允许删除 terminal Run。

删除范围：

```text
ScopeX task audit
ScopeX task work/scratch/host snapshot/runtime artifacts
该 Run 已导出的 review ZIP
```

明确不删除：

```text
/agent-data/logs
/agent-data/left-camera
或任何外部原始业务目录
```

运行中的 Run 必须先停止到 terminal，再删除。

删除 Schedule 与删除某一次 Run 是两个独立操作；Schedule 删除不会自动删除历史 Run。

## 8. 任务评价

终态 Run 支持：

```text
👍 正确
👎 有问题
```

负面标签：`wrong_result / incomplete / scope_too_broad / too_slow / wrong_skill / tool_failed / hard_to_read / insufficient_evidence / other`，以及备注。

评价保存为该 Run 的 `evaluation.json`，不自动修改 Prompt/Skill。

## 9. Review Export

终态页面可导出 bounded review ZIP，默认包含实际存在的：

```text
manifest.json
task.json
session.json
result.json
answer.json
claims.json
evidence.json
events.jsonl
final.txt
evaluation.json
runtime-limit.json
runtime-guard.json
investigation-error.json
worker-error.json
cleanup.json
```

默认不打包整份外部日志、图片、JSON、PCD。manifest 要求复盘模型区分 Model / Skill / Tool / Runtime / Evidence / Finalizer / UI，而不是静默重做业务诊断。

## 10. 当前页面

### 首页

- 一个 Agent 输入框；
- 不显示 Task / Conversation 切换；
- 月历执行记录；
- 点击日期展示当天 Run；
- terminal Run 可安全删除。

### 定时任务页

- 名称 / 普通任务内容；
- interval / daily / once；
- enable / disable / run-now / delete；
- 上次执行 / 下次执行；
- 离线历史跳过次数和最近 missed 时间。

### Run 详情

- 可读 Result；
- 开始/结束/耗时/触发来源；
- “事实依据”；
- “调查进度 / 技术记录”；
- Stop / Resume / Steering；
- 评价；
- Review ZIP。

## 11. 当前验收

代码存在不等于 PASS。至少验证：

1. `/runs` 普通能力咨询正常回答并内部落成 conversation；
2. `/runs` 业务调查一旦触碰业务数据必须进入 audited task；
3. 业务调查失败不能降级成 chat answer；
4. 乳头/编码器 structured facts 主结果可读；
5. Facts UI 不显示 Skill.md / script source / locator / scratch；
6. current system-health 无 Sandbox fallback；
7. interval/daily/once 正确；
8. Runtime 重启时 offline missed 不补跑；
9. run-now 不改 recurring cadence；
10. calendar/day list 正确；
11. terminal delete 只清 ScopeX 数据，不碰原始业务目录；
12. 评价 / review ZIP 正常；
13. Vue build / Python 全量回归通过。
