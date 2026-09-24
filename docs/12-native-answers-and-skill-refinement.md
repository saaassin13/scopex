# 原生回答与业务 Skill：当前实现契约

同步：2026-09-16；代码基线main@cb90d02。原feature/agent-native-results内容及后续修正已合入main。用户确认已部署端侧服务器并过夜运行总体正常，不再把整轮改动写成未提交/未部署。

本文件是当前实现快照。[2026-09-15逐次开发与复盘记录](https://github.com/saaassin13/scopex/blob/cb90d028773b4fac4eabf4b429ecb3fed97f1c51/docs/12-native-answers-and-skill-refinement.md)保留在固定提交；其中未提交/未部署、schema4、压缩关闭等叙述只代表各次记录当时的状态，不能覆盖下面的当前定义。

## 1. 唯一默认结果链

```text
POST /runs / 定时触发
 -> TaskService准入
 -> OpenClaw + 模型 + 已加载Skill自主调查、判断和回答
 -> 原生CLI outcome
 -> ScopeX正文、来源、执行状态与审计
```

Factory启用native_answers=True和concise_terminal_handoff=False，TaskService优先走finish_native_answer。除脱敏、长度约束与元数据封装外不另调模型重写；postprocess_model_calls=0。

StructuredFinalizer、Claim Validator、ReportComposer、TextReportComposer保留历史/专项兼容及独立回放，但不参与产品默认交付。不要求工具输出匹配固定业务schema或ClaimsJSON才可交付。也不从任意wire响应的stop推断任务成功，辅助压缩调用不是原生最终答案。

## 2. 状态与落盘

| 原生结果 | 产品状态 |
|---|---|
| 原生执行正常结束且正文完整 | COMPLETED；execution_status=completed；report_meta.status=complete |
| 预算/运行保护/原生错误/截断/进程失败，有有效原生文字 | FAILED；execution_status=incomplete；status=partial，保留草稿 |
| 同类失败没有有效原生文字 | FAILED；execution_status=incomplete；status=unavailable |
| 已知Locator确认固定窗口无数据并正常结束 | 可正常完成无数据说明；producer=scopex_no_data，不当业务正常诊断 |

error envelope只有明确的finalAssistantVisibleText可作为草稿；普通英文框架错误通知不作为答案。length保留原生部分正文，不取附加截断通知。有Evidence不覆盖失败；complete只表示执行与交付完整，不认证数字、单位、范围或物理原因全部正确。

result.json使用version=2、report_text、report_meta、execution_status、investigation_reasons；另存report.md/final.txt/report-meta.json。通常producer=openclaw；no_data单独标记来源。保留真实来源编号、未知引用警告和必要脱敏。

旧版“预算结束后另写报告，再因报告完整标完成”已不在默认分支，不是当前需要重做的架构问题。不以状态问题为由恢复任何报告模型链。

## 3. 上下文与实际部署参数

LocalRuntimeConfig/CLI默认compaction=false；--enable-compaction显式开启，--disable-compaction保留。**端侧deploy/edge/compose.yaml已经传入--enable-compaction**，不能把CLI默认关闭当作端侧有效配置。

使用OpenClaw原生上下文机制，不自建压缩器或子Agent。Runtime Dockerfile固定OpenClaw2026.9.2。edge 配置同时启用 `compaction.enabled` 和原生 `compaction.midTurnPrecheck.enabled`，让工具结果追加后、下一次模型调用前进入 OpenClaw 的 compaction/recovery 路径。ScopeX 在每次模型请求登记并保存原始 wire 后，使用版本化的保守预算估算检查 messages、工具定义、历史结果、图片预留、输出预算和安全余量；超限请求本地 `fail-fast`，返回 `context_budget_exceeded`，不改写请求或盲目重试。上游明确的 context overflow 记录为 `context_overflow`；普通业务文本、HTTP 400 或 `stopReason=length` 不单独视为输入溢出。

工具投影保留完整原始审计，并在可见 Evidence 上记录原始哈希、展示/遗漏行数与字符数、单行截断和投影状态。结构化事实超容量仍整块标为 `capacity_exceeded`，不剪残 JSON。当前不自动调用新的 `run_turn()` 做 overflow recovery，不把 conversation continuation 当作恢复；只有原生 OpenClaw 已自行恢复且最终 outcome 完整时才记录 `context_recovered`。长任务实际触发、压缩后证据保留及真实完成率/性能仍需专项 A/B 核验；历史溢出复盘不是当前所有任务失败的证明，也不能用短任务/过夜运行替代该专项。

edge图片SCOPEX_IMAGE_LIMIT默认12同时进入vLLM和ScopeX；通用图片默认4，每次view_image最多2。端侧restart均no，模型、路径、额度由deployment.md描述；本次不改这些配置。

## 4. Evidence、取样和无数据

结构化业务输出投影只压缩JSON空白，不按白名单删除units/limitations/semantics或新字段，也不二次截top候选。超预算明确capacity_exceeded与原始哈希并标working_derived，不剪残JSON冒充事实；原生正文不以此投影为必经关卡。

原图身份用于审计，视觉判断在调查调用中完成，不再次附图给报告模型。Locator按实际时间网格选代表图，避免密集采集段挤掉小时中部，不重复图片填满额度；保留selected_count/largest_selected_gap_ms。

固定窗无数据应立即说明并结束，不换窗/来源/猜UTC，不默认无限等待。现有请求边界仅对可核验的已知Locatorno_data合同硬结束，匹配工具/命令source/start/end/结果窗口与零匹配空文件列表；用户停止优先。后续请求不再发上游模型，系统来源单列。不撤回此前同批工具，不声称所有业务零样本均已硬拦截。source_unavailable/读取错误与no_data分开。

## 5. 编码器：raw-only schema 6（2026-09-20 本地改造，未部署）

仅分析 `Get EncoderVal, raw[...]`；不使用 filtered 判定 raw 有效性，不回退到应用 EncoderVal。当前工作区入口：

```bash
python3 {baseDir}/scripts/encoder_health.py scan /agent-data/logs/<实际文件> \
  --start "YYYY-MM-DD HH:MM:SS:000" --end "YYYY-MM-DD HH:MM:SS:000" \
  --out /task-scratch/encoder-raw.json
python3 {baseDir}/scripts/encoder_health.py inspect /task-scratch/encoder-raw.json --candidate R1
python3 {baseDir}/scripts/encoder_health.py report /task-scratch/encoder-raw.json
```

scan 保存原始证据、内容哈希、时间索引和全部候选；inspect 支持实际 R ID 或任意已覆盖时间区间、上下文扩展和原始点分页，并保存检查结果；report 汇总确认、未决和未查看状态。确认一处 raw 异常后建议停止后续候选深挖，不推断总数或频率。日常正反向、启停和近零量化不因方向或次数确证异常。

第一版对局部速率偏离、持续速率变化作筛查。自动确证仅使用有明确来源的可选设备 profile：计数合法范围，或具有时间误差及计数连续性依据的速率上限。无 profile 时仍可完整筛查/查看，但形状疑点不自动升级为异常。原始整数打印可能涉及有符号/无符号表示，未知格式不擅自转换。详见 [raw 契约](../skills/encoder-health/references/raw-contract.md)。

原始时间倒退、相同时间记录、解析错误和控制事件保留为边界/问题；缺口不解释成运动。一次扫描默认限 256 MiB 日志与 250,000 raw 样本，超限明确失败，不输出半份正常结果。已读样本和原始行保存后，局部查看无需原日志仍存在。

原 --motion-report 与 schema 3–5 查看入口已退出新工具；既有历史结果不改写，重新分析须 scan。位置文件调用仍等价于 raw scan，--events-out 仅为 --out 别名。未维护第二套旧判定。现场仍可能运行先前 schema 5，不能把此处本地改造当作部署回执。

## 6. 图片与日志

图片先实际看少量原图，区分可见退化、影响区域及可能解释；指标仅辅助选样，预留额度给邻近复核。重复出现不等于全时段连续存在，观察边界不证明物理起止或原因。没有新增视觉模型/训练流程。

日志上下文采用位置文件参数，不是--files；默认40行/6000字符，可显式1024–12000字符。保留完整原始行/行号，截断如实标记，不用高频窗口起点前N行冒充目标覆盖。

## 7. 产品新增与验证

已实现按schedule_id跨日期历史（前端50条分页，APIlimit1..200、offset，先过滤再分页），以及独立显式原始数据收集入口。历史记录、数据包、反馈与终态删除仅操作ScopeX资产，不修改源数据。

代码基线[Actions 35043132700](https://github.com/saaassin13/scopex/actions/runs/35043132700)为650项Python、compileall、Vue构建、卫生检查通过。原生专项使用真实Factory/Coordinator/TaskService并替换OpenClaw执行，验证超时草稿、原生错误/length、无schema、零额外报告调用及no_data来源；不冒充真实GPU语义验收。

用户已部署并过夜运行总体正常。独立专项还包括业务人工对账、全分辨率图片容量、长输入压缩、容器资源来源和整批并发收益；没有据此宣告故障，也没有新增性能百分比。详见[验收状态](02-delivery-and-acceptance.md)。
