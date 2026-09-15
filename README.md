# ScopeX

ScopeX 是 NVIDIA DGX Spark 上的端侧工业 Agent。用户提出目标，OpenClaw + 本地模型负责自主调查、决策、执行、验证和停止；ScopeX 提供能力、权限、任务生命周期、Evidence、审计和产品界面。

> **OpenClaw owns execution. ScopeX owns product control and trust.**

## 当前阶段：文本结果、独立任务并发、全局活动入口

`main` 是唯一集成基线。先读 [阶段交接](docs/08-local-usage-and-handoff.md) 和 [本轮方案与实机验证](docs/11-text-results-and-parallel-runs.md)。

2026-09-15 用户明确批准：结果输出做减法；多个独立任务真正并行，目标是整批总耗时缩短；所有追问/续问/上下文恢复改造暂缓。**并发代码可运行，不等于 Spark 吞吐已经验收通过。**

| 能力 | 当前状态 |
|---|---|
| Step 6A–6D、6F | 保留历史 PASS，不扩大原范围 |
| Step 6E | 保留历史 CAPABILITY PASS |
| 单次文本报告 | 产品默认路径，不要求 Claims JSON 或嵌套报告 JSON |
| 独立任务并发与有限队列 | 默认2活动任务、16等待项；名额可配1–4，推理请求和报告不全局串行 |
| 全局活动入口 | 顶部显示运行/等待/暂停；支持查看、暂停单项及取消排队 |
| 仓库回归 | PR #16 首轮573项 Python测试、Vue构建、卫生检查通过；最终提交以PR检查为准 |
| 实机整批加速、图片/编码器/乳头业务正确性 | 待 Spark 真实样本验收 |
| 追问/上下文恢复、网络诊断、重型点云、并发设备写动作锁 | 本轮不做 |

## 统一执行与输出

```text
统一用户输入 / 定时触发
  -> TaskService 有界准入、独立执行
  -> OpenClaw + 模型自主调查、动作、验证
  -> 普通回答，或业务 Evidence + 重新校验的原图
  -> 一次无工具 TextReportComposer
  -> 可读正文 + 系统提供的来源/时刻/结构化数据/报告状态
```

模型只负责中文表达，不要求填写 kind/relation/confidence 等内部协议。正文标题或分节不同不阻断交付；空内容、传输失败和截断仍明确区分 unavailable/partial，保留依据，不伪装业务全部完成。

`result.json` 的新输出为 version=2、report_text、report_meta、execution_status、investigation_reasons；另存 report.md/final.txt。来源身份可核对不等于自动证明语义正确，数字、单位、因果和覆盖范围仍需评测。

旧 StructuredFinalizer / Claim Validator / JSON ReportComposer 仅保留历史记录和 Step 6 专项兼容，不再是产品默认必经关卡。[旧报告架构](docs/architecture/08-trusted-report-composer.md) 中强制 Claims 的主链被本轮方案取代。

## 首批业务口径不变

- 当前资源：只使用本次实际执行时生成的宿主机快照，不采集历史，不拿 Sandbox 状态冒充宿主机。
- 图片质量：最终判断必须查看原始 JPG；亮度、锐度等仅辅助筛选；抽样不外推整小时正常。
- 编码器：定位具体毛刺、回退、跳变和采样缺口，保留时间、前后值、signed delta、dt与局部恢复；候选不是硬件根因。
- 乳头 KPI：命名牛周期分母、最后采用帧2D NippleNum，每牛最多4；缺结果保留分母且不伪装成观察到0。

工具按用户指定范围访问，够证据即停，不递归摸索所有数据根。业务语义见 [两份业务说明](docs/business/01-business-capabilities-v1.md) 和 [数据目录](docs/business/02-data-catalog-and-bounded-access.md)。

唯一目录语义配置为 `config/data-catalog.json`：

```text
/opt/ScalingRobotics/CowDisinfect/Log -> /agent-data/logs
/opt/ScalingRobotics/CowDisinfect/GrabbedImages/LeftCamera -> /agent-data/left-camera
```

业务目录只读，task scratch独立可写；内置Skill和Locator references目录在Runtime启动时同步。CPU/内存/PID/工具超时有限，但不声称有覆盖任意Shell的全局I/O硬限流。

## 并发、调度与恢复

两项任务可同时执行工具或发起模型/报告请求，使用同一个vLLM服务，由vLLM做批处理。ScopeX不重写模型调度器、不复制模型，也不混合任务Prompt。超出执行名额的任务进入有界队列。

排队时不创建Runtime或采集资源快照。历史相对时间窗使用原始创建/计划时刻；执行、等待和整批总耗时分别计量。暂停保留逻辑执行名额，暂不做暂停释放/恢复排队改造。

定时到点只是创建普通task，在线忙碌可排队；同一schedule已有运行或排队项时跳过新的触发，不无限堆积。离线错过全部跳过、不补跑；重启前排队项过期，未完成项记录中断，不自动重做。删除终态记录只清理ScopeX资产，不删除外部日志、图片或JSON。

产品只运行一个API进程；data-root锁防止多进程争用状态。不同任务Runtime/Scratch/审计独立；停止和清理只作用于对应任务。

## 模型与部署

最后用户确认的现机：vLLM `0.27.1+93523f72.nv26.8.64249418`，served id `qwen3.8-27b-nvfp4`，接口 `http://127.0.0.1:18002/v1`，图片配置count=12，mm缓存0.5GiB。

`SCOPEX_MAX_IMAGES_PER_PROMPT=12` 必须随实际Runtime启动配置生效。配置12并不等于12张全分辨率任务已验收。每view_image最多2张，完整Prompt累计额度与原图/token/请求体积预算分别核对。

vLLM `max-num-seqs` 最后文档值为1，需实机inspect；若仍为1，须确认原Compose/容器管理方式后在维护窗口改为2，再验证模型批处理。**本次仓库更新不重建、不重启、不修改现机vLLM。**

更新时保留现有workspace/data-root/额外挂载，重建前端dist并重启ScopeX Python服务。Sandbox依赖与模型镜像未变，不需要因本轮Python/Vue修改重建它们。完整命令见 [本轮运行说明](docs/11-text-results-and-parallel-runs.md)；基础离线部署继续参考 [部署文档](docs/09-zero-to-one-build-and-offline-deployment.md)。

## 检查与验收工具

```bash
python3 -m unittest discover -s tests -v
(cd frontend && npm run build)
```

`scripts/replay_text_report.py`：从已保存Evidence独立重写报告，零工具重跑，不改旧任务。

`scripts/benchmark_task_batch.py`：同一代码、同一固定任务集，对比1路/2路整批完成时间；失败或不完整报告不算加速，未人工核对质量只报告计时结果。目标是实际总耗时缩短，不是页面同时显示多个运行中。

[验收记录](docs/acceptance/2026-09-15-output-parallel-status.md) 将仓库测试、浏览器模拟接口检查与Spark真实业务/性能验证分开记录。合入main不是宣告现场全部PASS。
