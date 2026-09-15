# 原生回答与编码器、图片 Skill 修正

状态：2026-09-15，`feature/agent-native-results` 开发分支。首批实现已在 `ae846f6` 提交，用户提供的现场任务确认运行该版本；下述编码器上下文修复仍未提交或部署。用户已批准正常任务直接交付 OpenClaw 答案的方向。

## 现场追加：task-b9ef01e82ab2 上下文溢出

依据为用户提供的复盘 ZIP，以及 workstation 对该任务 `agent.stdout.txt`、`agent.stderr.txt` 和 wire metadata 的只读核对。

- 该任务运行 390 秒，没有产生业务结论。前 7 次模型调用成功，第 8 次 HTTP 400：输入至少 30721 tokens，加请求输出 2048，超过 32768 上限。这里的“至少”是服务端错误原文，不能解释成只超了一个 token。
- Agent 先分析一次，再查询两个保存的事件窗口；随后调用日志工具时误用不存在的 `--files` 参数，两个调用均失败，多花一轮纠错。
- 修正参数后，两次无关键词的 120 行日志输出分别为 15634、15998 字符，且已截断。高频混合日志从窗口起点填满额度，不能保证覆盖目标事件。下一次模型请求即溢出，未观察到成功的原生恢复。之前引用原生文档的恢复能力不能当作该部署已经验证的兜底。
- 框架将英文 overflow 通知放入普通 payload，ScopeX 因其非空误称为“草稿”；任务 FAILED 判定本身正确。

本次修正：日志工具默认 40 行、最多 6000 字符（可显式设 1024–12000），紧凑 JSON，超限删除完整行并标记 `output_limited/truncated`，保留原始行内容与行号；两个 Skill 明确位置参数、精确时间与关键词、逐次复核和截断处理。error envelope 仅在明确提供 `finalAssistantVisibleText` 时保留草稿，普通框架提示不再当回答。

验证：`PYTHONPATH=tests python3 -m unittest test_business_skill_tools test_native_answers test_openclaw_runner_outcome test_encoder_motion_context -q`，31 项通过。只读执行新日志脚本（标准输入，不落远端文件），对 13:28:43.258、13:26:08.741 各 ±0.15 秒、关键词 EncoderVal、20 行，分别输出 4111/4035 字符，均覆盖目标时间且如实标记截断。该片段验证不等于完整运动过程判定。

Codex skill-creator 的通用 frontmatter 校验器拒绝项目原有 OpenClaw `user-invocable` 字段；保留该原生字段，不将这项校验记为通过。脚本回归通过，新增命令参数与实际 argparse 对齐。

未验证：部署后完整 Agent 重跑、业务异常判别准确率、其他长任务的原生 overflow 恢复。字符上限减少本次输入膨胀，不保证任意多轮任务永不溢出。本轮未改模型/上下文/压缩参数，未提交、推送或部署；无持久测试资源，用户 ZIP 与截图原位保留。

本文件覆盖早期文档中“所有业务任务必经 TextReportComposer”与默认主动 compaction 的描述；权限、隔离、时间锚点、离线调度和并发边界不变。现场原因见 [实机复盘](reviews/2026-09-15-workstation-findings-and-proposal.md)。

## 1. 产品主链

```text
POST /runs / 定时触发
 -> TaskService 准入、独立任务
 -> OpenClaw + 已加载业务 Skill 调查、判断、回答
 -> 原生 CLI outcome
 -> ScopeX 保存正文、来源、执行状态和审计
```

- 使用现有 `cli_outcome.answer`，不另建 Agent Loop、SSE 终态解析器或报告模型调用。
- 正常与异常执行统一输出现有 version=2 文本格式。`report_meta.producer=openclaw`、`postprocess_model_calls=0`。
- `valid/complete` 只指原生执行及文本交付完成，不认证业务语义或未观察范围。正常任务不要求工具输出匹配 ScopeX 业务 JSON 才可交付。
- 原生 error/aborted/非终态 stopReason、进程失败或预算中断不能因有文字/Evidence而变成功。可见原生文字保存为 partial 草稿，无文字为 unavailable。
- 已核对 OpenClaw 2026.9.2 的 `meta.stopReason=length` 和 `meta.finalAssistantVisibleText`；截断时保留部分回答，不把附加截断提示当最终结论。
- 不从任意 wire `finish=stop` 抢救“成功”：压缩等辅助调用也会 stop。没有原生 outcome 的中断保留审计和明确失败，不自动再调模型。
- 旧 StructuredFinalizer/TextReportComposer 和相关专项回归保留为历史兼容、独立回放能力，退出产品默认链。

实现位置：`scopex/api/factory.py`、`scopex/api/service.py`、`scopex/runtime/investigation.py`、`scopex/agent/outcome.py`。

## 2. 原生上下文管理与运行参数

产品 LocalRuntimeConfig/CLI 默认 `compaction.enabled=false`，已有 `--disable-compaction` 保留；`--enable-compaction` 可显式恢复主动/完成后维护。

现场 OpenClaw 2026.9.2 文档确认：关闭的是主动阈值压缩与 direct-command post-turn maintenance，preflight/overflow recovery仍由OpenClaw负责。低层历史实验的配置默认不改，不扩大历史Step6验收结论。

不要为一次性独立任务的未来追问预先做数分钟会话维护。未来恢复持续会话时需重新评估配置，不据此声明所有长会话都应关闭主动压缩。

图片额度仍由 `SCOPEX_MAX_IMAGES_PER_PROMPT` 明确设置，并须与服务端匹配。默认4不擅自改12；现场vLLM接受数量12也不等于全分辨率12张已验收。部署时必须保留现有挂载、模型参数与任务存储。

当前没有部署授权，本分支不执行上述现场配置变更，不调整vLLM max-num-seqs。

## 3. Evidence 保真

`OpenClawEvidenceProjector` 对结构化业务输出只压缩JSON空白：

- 不按字段白名单删除 units、limitations、semantics 或新Skill字段。
- 不二次裁减 top_candidates。
- 超过投影预算，记录明确的 capacity_exceeded 和原始输出哈希，标记 working_derived；不切出残缺JSON冒充事实。完整工具结果仍在OpenClaw审计。
- 界面不把这种容量占位记录列为业务事实。结果正文不再依赖这份投影才能交付。

原图来源身份仍保留，但正常答案不再重新附图进行第二次视觉推理。

## 4. 编码器

工具输出 schema=4；events_out schema=3。旧任务文件不改写。

确定性修正：

- 局部正向增量按相同dt归一化，恒速下采样间隔变长不再自动变成正跳候选。
- 反向事件补充持续时间、平均count/s、前后各最多2秒的运动摘要。无效记录/时间断点不能跨越补上下文。
- 恢复检查改为可配置时间窗（默认1000ms），达到80%回补即结束，不吞掉之后独立发生的跳变。recovery_observed_ms反映实际观察时长；不是物理恢复证明。
- 正常恒值区间单列，不争用top候选名额。优先保留最大回退/正跳/采样间隙，再按同类型筛查门槛的相对程度选样，默认6项。
- `anomaly_event_count` 改为 `candidate_event_count`；candidate_events_total不含恒值区间，observed_events_total包含全部观测事件。候选数不是已确认异常数。
- `--inspect-events FILE --start ... --end ... --top-events N` 可查询已保存事件，无需重新扫描日志或临时Python全量打印。

Skill要求结合启动、减速、停转、回弹及周围正常过程解释候选，必要时主动补看局部日志；不再只报候选数并让用户自行核查。

**尚未完成的业务验收**：未用现场标签确定正常回弹包络；没有宣称所有近停回退正常，也没有宣称脚本已经自动分类全部工况。相邻过程合并、可接受性和根因仍由Agent结合证据判断。需要对正常启停/回弹及已确认异常样本做误报漏报核对。

## 5. 图片与取样

- Skill先判断可见退化及影响区域，再区分失焦、运动、雾化、污物、水滴或其他解释。
- 首轮直接看少量原图；指标仅在能改变筛选决策时使用，不默认先跑help/metrics。
- 预留累计图片额度给邻近时点复核；多个样本反复出现与完整连续区间分开，起止只定位到观察边界。
- Locator改为按实际时间网格选邻近文件；密集采集段不再挤掉小时中部。间隙造成多个网格点对应同一图片时不重复填满额度。
- 新增 selected_count 与 largest_selected_gap_ms，帮助判断覆盖。

未增加视觉模型、训练管线、依赖或核心业务工作流。现有模型对8点原图已能识别主要退化，但新Skill的业务效果和总耗时仍需实机对照验证。

## 6. 验证

- 原生交付专项：真实产品Factory/Coordinator/TaskService，替换OpenClaw执行；覆盖完整答案、无业务schema、超时草稿、无outcome、原生错误/length、脱敏、未知引用、普通问答；断言零额外报告调用。
- 业务反例：恒速改变采样间隔、实际跳变、停后回退的静止上下文、不同频率下恢复、恢复后独立跳变、正常停转不挤掉极值、有界事件查询、非均匀图片密度。
- 前端：Vue类型检查与Vite构建通过。真实构建页面+本机合成OpenClaw执行，通过首页提交、正常正文、超时FAILED/草稿及来源展开；不是Spark联调。
- 最终 `python3 -m unittest discover -s tests -q` 执行605项，601项通过，4项既有macOS平台失败；已在未修改HEAD归档中单独复现：3项POC02强制Linux，1项临时路径解析断言。它们没有被跳过、删除或伪装通过。首轮沙箱禁止本机端口导致的错误已在允许本机测试服务的环境复查，不计为通过。
- 尚未执行：Spark新版任务、长输入原生溢出恢复、8/12张原图容量、两项业务人工对账和并发收益验证。当前没有性能改善百分比结论。

## 7. 分支与资源

分支 `feature/agent-native-results`，保留全部未提交修改及两份审查报告；用户只授权开发，未commit/push/部署。

本地前端node_modules/dist由本轮验证生成，保留用于后续开发。现场任务、8张获准原图与核对记录留在 `/private/tmp/scopex-review-20260915`，不提交业务数据。一次性UI验收服务、合成数据和基线临时副本在验收后清理；保留必要测试日志。
