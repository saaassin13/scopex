# 原生回答与编码器、图片 Skill 修正

状态：2026-09-15，`feature/agent-native-results` 开发分支；尚未提交、合入或部署。用户已批准正常任务直接交付 OpenClaw 答案的方向。

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
