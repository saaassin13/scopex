# ScopeX 对话、任务、定时触发与反馈闭环

状态：**2026-09-14 当前设计基线**。

本文件定义产品层如何同时支持正常对话、可审计业务任务、简单定时触发、执行记录、任务评价和复盘导出。

固定边界：

> **对话和任务在产品上区分，但底层只走一套 ScopeX Task / OpenClaw Runtime。**

> **定时任务只是 Trigger，不是 Workflow Engine。**

## 1. 统一底层流程

所有入口最终都进入同一条执行链：

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

不维护独立 Chat Agent、Schedule Agent 或 Workflow Runtime。

产品入口只附加少量元数据：

```text
mode: conversation | task
trigger_type: manual | schedule
schedule_id: optional
triggered_at: optional
```

OpenClaw 的调查、工具、Skill、Session、预算和审计路径保持一致。

## 2. Conversation 与 Task 的区别

区别只在**结果验收策略**，不在 Runtime。

### Conversation

适合：

- “你现在支持哪些能力？”
- “解释一下这个结果。”
- “乳头识别率怎么算？”
- 讨论设计方案。

规则：

- 可以使用 Skill / Tool；
- Evidence 有则保留；
- 不要求每一句普通回答都必须产生 claim-grade Evidence；
- 模型正常完成但 Evidence 为空时，不能因为 `investigation_completed_without_evidence` 判失败；
- 结果以自然语言对话为主。

### Task

适合：

- “检查 3 点乳头识别率”；
- “分析过去 30 分钟编码器是否有毛刺/回退”；
- “检查这批图片是否有脏污/起雾”；
- “检查当前磁盘使用”。

规则：

- 业务结论必须有 Evidence；
- 走 Fresh Finalizer / Claims / Product Answer；
- 无证据时不能把模型自由回答伪装成正式业务结果；
- 结果保留 Audit / Evidence / Trace。

## 3. 可读性结果

可信结果不能直接把原始 JSON/日志当用户主结论。

推荐链路：

```text
Evidence
  ↓
Validated Claims / Structured Facts
  ↓
Constrained Answer Composer
  ↓
自然语言结果
```

Answer Composer 只允许：

- 把字段名翻译成业务语言；
- 数字转成百分比/单位；
- 合并重复事实；
- 按“结论 / 说明 / 异常 / 建议”组织；

不允许：

- 调工具；
- 新增调查；
- 增加 Claims 没支持的事实；
- 把时间相关升级为因果。

例如原始事实：

```text
nipple_recognition_rate = 0.978311
total_cows = 438
complete_four_nipple_cows = 400
3-nipple cows = 38
```

产品展示应接近：

```text
3:00–4:00 共统计 438 头牛，其中 400 头完整识别到 4 个乳头，
38 头最终识别到 3 个乳头。乳头整体识别率为 97.83%，
完整四乳头识别率为 91.32%。
```

原始字段、Evidence 和日志放在可展开详情中。

## 4. Scheduled Task

V1 不做复杂编排。

一个定时配置只需要：

```text
id
name
message
schedule
enabled
created_at
updated_at
```

到点执行：

```text
Scheduler fires
    ↓
TaskService.create_task(message, metadata)
    ↓
完全复用普通 Task 流程
```

Scheduler 不决定调用哪个 Skill、不组织步骤、不写诊断流程。

### 示例

```text
任务：检查当前磁盘使用
周期：每天 08:00
```

```text
任务：分析过去 30 分钟编码器是否有丢数、毛刺、回退
周期：每 30 分钟
```

```text
任务：检查过去 30 分钟图片是否存在模糊、脏污、起雾趋势
周期：每 30 分钟
```

其中“过去 30 分钟”以本次 `triggered_at` 为时间基准，并写入 Task metadata / runtime context，避免由模型猜当前时间。

V1 页面只提供常用触发：

- 一次；
- 每 N 分钟/小时；
- 每天固定时间；
- 高级 Cron 可后置，不作为第一版主交互。

## 5. System Health 收缩

V1 不持续收集 CPU / memory / disk / GPU history。

删除：

```text
30 秒 system metrics timer
system_metrics.jsonl 历史库
历史 CPU/GPU 资源分析
```

保留：

```text
current host snapshot
```

例如定时任务“每天检查当前磁盘”在任务执行时读取当前宿主机快照。

重要边界：

- Agent 默认在 Sandbox；
- 不能用 Sandbox `/proc/free/df/nvidia-smi` 冒充 Spark host；
- 当前 host snapshot 通过明确只读 capability 提供；
- capability 不可用时返回“宿主机资源状态当前不可用”，不得 fallback。

宿主机 current-snapshot capability 的安全接线需要单独实现/验收，但不再引入历史采集器。

## 6. Task Run 生命周期

每一次手动或定时触发都产生一个普通 Task/Run。

Task 至少增加：

```text
mode
trigger_type
schedule_id (optional)
triggered_at
started_at
finished_at
duration_ms
```

页面应展示：

```text
开始时间
结束时间
持续时间
触发方式
任务状态
```

定时配置和每次执行记录必须分开：

```text
Schedule
 ├─ Task Run 001
 ├─ Task Run 002
 └─ Task Run 003
```

## 7. 任务评价

每个 Task Run 可以评价：

```text
positive | negative
```

negative 时可以选择：

```text
结果错误
分析不完整
范围过大
耗时过长
Skill 使用不正确
工具执行失败
结果难理解
证据不足
其他
```

并保存可选说明。

评价不自动修改 Agent prompt/Skill；它是后续人工/大模型优化的证据。

## 8. Review Export

每个 Task Run 支持导出 review bundle，用于交给更大模型或开发工具复盘。

默认 `review` 模式包含：

```text
manifest.json
task.json
schedule.json (if any)
evaluation.json
result.json
answer.json
claims.json
evidence.json
final.txt
events.jsonl
runtime-limit.json / runtime-guard.json / investigation-error.json (if any)
environment.json
```

`environment.json` 至少记录：

```text
ScopeX commit
OpenClaw version
served model id
vLLM version/image
built-in Skill names + version/hash
```

默认不打包整小时原始图片/日志，避免 review bundle 数 GB；只保存引用和 claim-grade Evidence。需要完整原始数据时另选 `full` 导出。

## 9. 页面建议

V1 页面可收敛为：

```text
对话
任务
定时任务
执行记录
```

任务详情：

```text
结果
开始 / 结束 / 持续时间 / 触发方式
执行进度
Evidence
评价
导出复盘包
```

定时任务页：

```text
名称
任务内容
触发规则
启用/停用
上次执行
下次执行
最近结果
```

不在 V1 做拖拽工作流、节点编排、复杂依赖关系。

## 10. 实施顺序

建议按以下顺序落地：

1. Conversation / Task 共享 Runtime，但修正无 Evidence 对话被判失败的问题；
2. Constrained Answer Composer，提高 Task 结果可读性；
3. Task lifecycle 增加 started/finished/duration/trigger metadata；
4. 简单 Schedule CRUD + Trigger -> `TaskService.create_task()`；
5. Schedule / Run UI；
6. Task evaluation；
7. Review export bundle；
8. current host snapshot capability；
9. 再继续扩业务 Skill。

这些都是产品控制层能力，不改变 OpenClaw 对调查/工具/Skill 的执行所有权。
