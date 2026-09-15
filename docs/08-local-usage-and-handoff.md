# ScopeX 阶段交接与本地使用

更新：**2026-09-15 文本输出 / 独立任务并发 / 全局活动入口，PR #16 实施收口**。`main` 是唯一集成基线。阶段交接仅在大块完成后更新；本次是代码与界面实施收口，不是 Spark 整批加速或全部业务验收 PASS。

## 1. 本轮用户决定与文档优先级

用户确认：

- 结果总结改为一次无工具模型生成可读正文，系统提供来源、业务数据和执行状态；不再强制通过 Claims JSON 和第二次报告 JSON。
- 多个独立任务真正并行，目标是**整批任务全部交付更快**，不是仅能提交/排队。
- 页面提供全局活动任务入口。
- 运行中提问、完成后追问、跨 Run 会话上下文恢复：全部暂缓。不要因为旧接口存在就宣称完整支持。

先读本文件、README、`11-text-results-and-parallel-runs.md`、`acceptance/2026-09-15-output-parallel-status.md`。原 `01-requirements.md`、`02-delivery-and-acceptance.md`、architecture/08 和业务文档中的强制 Claims 主链/单槽位/并发暂缓描述，已被本轮输出与并发方案替代；其他业务和安全约束仍有效。部署制备读 `09-zero-to-one-build-and-offline-deployment.md`。

OpenClaw + 模型拥有自主调查、决策、动作、验证与停止。ScopeX 只管能力、权限、产品生命周期、Evidence、审计、结果和执行准入。不重写 Agent Loop / Workflow Engine，不把业务调查写死在 Handler。

## 2. 当前状态

| 项目 | 结论 |
|---|---|
| Step 6A–6D、6F | 保留历史 PASS，不无证据重做 |
| Step 6E | 保留历史 CAPABILITY PASS |
| PR #14 集成 | 原基线；520项回归，非Spark全部业务PASS |
| PR #15 输出恢复 | 已合入7db4ac4；540项回归，保留为历史兼容 |
| PR #16 文本报告、独立并发、活动入口 | 实施完成；首轮573项Python、Vue构建、卫生检查通过；最终提交看PR/Actions |
| 浏览器检查 | 实际构建前端 + 模拟API检查活动列表/取消排队/文本/草稿/任务切换/移动端/断线提示，无页面异常；不是Spark真实联调 |
| Spark 文本报告语义与图片/编码器/乳头业务正确性 | 待现机验收 |
| Spark 2路/4路整批耗时收益 | 未测，不得称并发性能PASS |
| vLLM 模型配置变更 | 本轮未执行，无容器重建/重启 |

历史失败任务不会因为升级自动变成功。要新建执行或在独立目录对保存的 Evidence 重写报告。

## 3. 新产品主链和实现位置

```text
统一 POST /runs 或定时触发
 -> TaskService：默认2个独立任务名额 + 16个有界等待项
 -> 每任务独立OpenClaw Runtime / Session / Scratch / 审计
 -> 原生调查、执行、验证、停止
 -> 普通回答，或业务Evidence + SHA校验原图
 -> 一次无工具 TextReportComposer
 -> version=2 report_text / report_meta / 来源 / 执行状态
 -> Vue正文与全局活动入口
```

主要文件：

- `scopex/finalizer/text_report.py`：文本编辑器，一次请求，无JSON解析/Claims门槛/自动重试。
- `scopex/api/factory.py`、`scopex/runtime/investigation.py`：产品实际接线与落盘。
- `scopex/api/service.py`：独立任务并发、队列上限/超时/取消、活动摘要、重启中断记录。
- `scripts/runtime_api.py`：并发配置、单进程data-root文件锁、延迟到准入时采集快照。
- `frontend/src/components/ActivityPanel.vue`、`TaskView.vue`：全局活动与正文/不完整草稿。
- `scripts/replay_text_report.py`：从终态任务目录或复盘ZIP独立生成报告，零业务工具重跑。
- `scripts/benchmark_task_batch.py`：固定同一任务集的整批计时、串并行对比，失败不算加速。

旧 StructuredFinalizer / Claim Validator / ReportComposer 不作为新产品默认路径，但保留历史与Step6专项测试兼容。不要为新输出添加逐业务字段翻译表。

报告来源身份不等于语义证明；complete仅表示完整文本返回，不能替代数字/单位/候选与根因/范围的人工验收。空输出unavailable、截断partial，保留依据，不能伪装完整交付。正文目前以安全转义文本展示，Markdown标题不是必须，也不执行模型HTML。

## 4. 业务边界不变

当前资源仅本次宿主机快照，不采历史，不使用Sandbox的proc/free/df冒充宿主机。排队时不采集，实际获得名额后再采集，报告采样时间。

图片必须看JPG原图；指标只筛选，不能用高锐度断言无雾。view_image每次最多2张，完整Prompt累计受 `SCOPEX_MAX_IMAGES_PER_PROMPT` 控制。原图身份校验与重新附图已移到文本报告调用，不得因减少模型调用而省略。

编码器优先应用EncoderVal，识别具体毛刺、连续回退、异常正跳和缺口；给时刻、前后值、delta、dt与恢复窗口。invalid采样是断点，不能删除后跨点连算。候选数不是脉冲数或已确认故障数。

乳头KPI只按最终LastImgTimeStamp对应2D NippleNum，命名牛周期分母，每牛最多4。缺图/JSON/最终结果不删牛，缺失不等于观测0。牛周期去重、小时边界和最终帧仍需一小时人工对账。

目录语义唯一配置为 `config/data-catalog.json`，host只读映射 `/agent-data/logs` 与 `/agent-data/left-camera`，Locator references随Skill在启动时同步。不能递归全历史目录，也不声称任意Shell全局I/O限流已经实现。

## 5. 并发与恢复边界

多个任务可同时执行工具、请求模型、生成报告；没有把所有模型调用串行化的全局锁。vLLM原生批处理负责GPU调度，ScopeX不混合各任务上下文、不起多个模型副本。

默认2活动/16等待/等待600秒，活动名额可配1–4。队列满时明确拒绝；任务在队列中可取消，不创建Runtime/Sandbox。暂停仍保留逻辑名额，未做暂停释放和恢复排队。

定时任务在线忙碌可进入有限队列，同一schedule已有活动/等待项则跳过新触发。离线错过全部不补跑；重启前排队项过期，其他未完成任务记录中断，不自动重做动作。旧孤儿容器仍需按精确身份检查，不按宽泛前缀删除。

一个data-root只允许一个API进程；不要开多个uvicorn worker共享内存队列。CLI文件锁防止第二个新版本进程启动，但升级时仍必须主动停止不带该锁的旧版本服务。

## 6. 最后已确认的现场事实

```text
model repo=unsloth/Qwen3.8-27B-NVFP4
revision=f0b7c9e722f5565102fff8481c99e4d86ae099c7
served id=qwen3.8-27b-nvfp4
endpoint=http://127.0.0.1:18002/v1
image=nvcr.io/nvidia/vllm:26.08-py3
vLLM version=0.27.1+93523f72.nv26.8.64249418
context=32768
image limit configured=12（已收到inspect回执，12张请求成功/全分辨率容量仍需实测）
mm processor cache=0.5GiB
container=scaling-scope-vllm-nvfp4
```

用户此前工作区在business-skills/v1的937d879，曾有未跟踪frontend/package-lock.json；之后已给main同步命令，但不能据此替代新进程/新提交现场回执。

max-num-seqs最后文档值为1，当前值需inspect。要测试模型2路批处理，先确认原Compose/容器管理入口与现有参数，再安排维护窗口仅改相关参数；本轮没有重建/重启容器。显式KV缓存最后记录8G，不盲目调大所有预算。OpenClaw精确版本、原Compose入口、网络topology仍需补录。

## 7. 下一步与更新方法

先等任务结束、停止ScopeX Runtime，保存本地改动。禁止reset --hard / git clean或删除.local。拉main后**本次必须重建frontend dist**，保留原workspace/data-root/额外挂载，启动时显式SCOPEX_MAX_IMAGES_PER_PROMPT=12与--max-active-tasks 2。详细命令在11文档。

先用独立Evidence回放确认文本输出；再新建真实任务检查report_text/meta及语义。然后在相同代码/模型/任务集/预热条件下对比1路与2路至少3组，记录整批从首次提交到最后报告完成的耗时。全部结果质量和覆盖一致后才判断加速；不把失败早退、少看原图、少算牛或输出链减少一次调用当并发收益。

全局活动入口检查跨日期、排队取消、报告阶段、断线后状态未知和任务切换不串数据。Spark重启/离线包/回滚、数据保留策略、任意Shell I/O预算及复杂大窗口资源占用仍是未完成的专项验收。

这组实机结果齐备后再更新下一次大阶段handoff，不按每次小修更新。
