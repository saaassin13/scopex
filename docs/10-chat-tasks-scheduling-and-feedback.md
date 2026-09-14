# ScopeX 对话、任务、定时触发与反馈闭环

状态：**2026-09-14 第一版已实现，待 Spark / 前端真实验收**。

本文件定义并记录产品层如何同时支持正常对话、可审计业务任务、简单定时触发、执行记录、任务评价和复盘导出。

固定边界：

> **对话和任务在产品上区分，但底层只走一套 ScopeX Task / OpenClaw Runtime。**

> **定时任务只是 Trigger，不是 Workflow Engine。**

## 1. 统一底层流程

```text
用户对话 / 手动任务 / 定时触发
        ↓
Task Envelope
        ↓
TaskService
        ↓
OpenClaw + Local Model
        ↓
Skills / read / exec / process / view_image
        ↓
Trace / Progress / Audit
        ↓
Result policy
        ↓
产品结果
```

没有独立 Chat Agent、Schedule Agent 或 Workflow Runtime。

Task 元数据包括：

```text
mode: conversation | task
trigger_type: manual | schedule
schedule_id: optional
scheduled_for: optional
started_at
finished_at
duration_ms
```

## 2. Conversation 与 Task

两者使用同一个 `TaskService.create_task()` 和同一个 OpenClaw / Skill / Tool / Session / Budget / Audit 路径。

### Conversation

适合普通问答、能力咨询、解释结果、讨论方案。

当前实现：

- POST `/conversations`；
- OpenClaw 正常完成且没有 Evidence 时，允许直接发布其最终可见回答；
- 有工具/Evidence 时仍保留相同审计轨迹；
- 不再因为 `investigation_completed_without_evidence` 把正常对话判失败。

### Task

正式业务分析继续要求 Evidence：

```text
Evidence -> Fresh Finalizer -> Validated Claims -> Product Answer
```

没有 Evidence 的业务任务不能把模型自由回答伪装成正式业务结论。

## 3. 可读性结果

当前 Product Answer 保持 Claims/Evidence 可信边界，同时增加确定性业务字段格式化：

- KPI 小数转百分比；
- 常见字段翻译成业务语言；
- 牛数/乳头分布转成正常句子；
- 宿主机快照不可用时直接说明不可用，而不是展示 Shell 错误作为主结论。

例如：

```text
"nipple_recognition_rate": 0.978311
"total_cows": 438
"complete_four_nipple_cows": 400
"3": 38
```

主界面可展示为：

```text
乳头识别率为 97.83%。
共统计 438 头牛，其中 400 头完整识别到 4 个乳头；
38 头最终识别到 3 个乳头。
```

这一层不调用新模型、不调工具、不增加 Claims 不支持的事实。后续若引入模型语言润色，也必须继续受 Claims 约束并保留 deterministic fallback。

## 4. Scheduled Task

V1 定时配置只有普通任务内容 + 简单时间规则：

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

### 支持规则

- 每 N 分钟；
- 每天 HH:MM；
- 一次执行。

例如：

```text
每天 08:00 -> 检查当前磁盘使用
每 30 分钟 -> 检查过去30分钟编码器
每 30 分钟 -> 检查过去30分钟图片质量
```

“过去30分钟”由任务本身表达，`scheduled_for` 给出稳定时间基准。

### 单执行槽位

V1 仍只有一个主要执行槽位。定时触发时若已有 Task 在运行：

```text
SKIPPED_BUSY
```

记录本次跳过，不偷偷排队，不改变下一周期。

页面“立即执行”只是额外触发一次，也**不会改变原定 `next_run_at`**。

## 5. System Health

V1 已取消连续 CPU / memory / disk / GPU 收集：

```text
无 30 秒 timer
无 system_metrics.jsonl
无历史资源分析
```

每个 Task 创建时，ScopeX 在宿主机生成一次：

```text
<task-work>/host/current.json
```

然后只读挂载为：

```text
/scopex-host/current.json
```

因此“检查当前磁盘/内存/GPU”可以使用当前宿主机事实，同时 Agent 仍保持 `exec_host=sandbox`。

如果当前宿主机字段采集失败，Skill 必须报告不可用；禁止使用 Sandbox `/proc/free/df/nvidia-smi` 替代。

## 6. Task Run 生命周期

Task 已记录：

```text
mode
trigger_type
schedule_id
scheduled_for
started_at
finished_at
duration_ms
```

UI 顶部展示：

- 开始时间；
- 结束时间；
- 持续时间；
- 手动/定时触发；
- 计划执行时间（定时任务）。

Schedule 配置和每次执行记录保持分离：

```text
Schedule
 ├─ Task Run 001
 ├─ Task Run 002
 └─ Task Run 003
```

## 7. 任务评价

终态 Task 支持：

```text
👍 正确
👎 有问题
```

负面评价标签：

```text
wrong_result
incomplete
scope_too_broad
too_slow
wrong_skill
tool_failed
hard_to_read
insufficient_evidence
other
```

以及可选说明，保存为该 Task 的 `evaluation.json`。

评价只作为后续优化证据，不自动修改 Prompt/Skill。

## 8. Review Export

终态页面提供 review ZIP 导出。默认包含实际存在的：

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

默认**不打包整份外部图片/日志业务数据**，避免导出包失控。`manifest.json` 明确告诉大模型：目标是分析 ScopeX 为什么得到该结果，并区分 Model / Skill / Tool / Runtime / Evidence / Finalizer / UI 问题，而不是静默重新做一次业务诊断。

环境版本清单后续会进一步补强；当前主要版本仍由 `task.json`、仓库 commit 和部署资产共同追踪。

## 9. 页面

当前第一版入口：

```text
任务与对话
定时任务
```

首页：

- Task / Conversation 模式切换；
- 同一执行记录列表；
- 显示模式、触发方式、开始时间、耗时。

定时任务页：

- 名称；
- 普通任务内容；
- interval/daily/once；
- 启用/停用；
- 立即执行；
- 上次执行/状态；
- 下次执行；
- 删除。

Task 详情：

- 可读 Result；
- 开始/结束/耗时/触发来源；
- Progress；
- Evidence；
- 评价；
- 导出复盘包。

## 10. 当前验收

代码已实现，不等于 PASS。需要依次验证：

1. 普通对话“当前有哪些 Skill”可以正常完成且不要求 Evidence；
2. 正式业务 Task 无 Evidence 仍不能发布可信结果；
3. 乳头 KPI 结果可读，不再直接展示裸字段；
4. 当前宿主机资源从 `/scopex-host/current.json` 获取且无 Sandbox fallback；
5. started/finished/duration 正确；
6. interval/daily/once 调度正确；
7. busy 时 `SKIPPED_BUSY`；
8. “立即执行”不改变周期；
9. 评价可保存/修改；
10. review ZIP 可下载并用于更大模型复盘；
11. Vue build / Python 全量回归通过。
