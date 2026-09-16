# ScopeX

ScopeX 是 NVIDIA DGX Spark 上的端侧工业 Agent。用户提出目标，OpenClaw + 本地模型负责自主调查、决策、执行、验证、停止与最终回答；ScopeX 提供能力、权限、任务生命周期、Evidence、审计和产品界面。

> **OpenClaw owns execution. ScopeX owns product control and trust.**

## 当前基线与现场状态

文档同步：2026-09-16；代码核对基线为 `main@cb90d028773b4fac4eabf4b429ecb3fed97f1c51`。原生回答、业务 Skill 修正和端侧 Compose 已进入 `main`，不再描述为未合入的开发分支。`main` 是唯一集成基线。

**2026-09-16 用户确认：已部署到端侧服务器，过夜运行总体正常。** 这是有效的现场运行回执；不再笼统标为“未部署”。本次没有独立读取端侧运行 SHA、镜像或逐项验收数据，不将该回执扩大为所有专项已 PASS，也不因同步文档操作现场服务。

代码基线的 [Actions 35043132700](https://github.com/saaassin13/scopex/actions/runs/35043132700) 已通过：650 项 Python 测试、Python 编译、Vue 构建和仓库卫生检查。历史 Step 6A–6D、6F PASS 与 6E CAPABILITY PASS 保留原范围。

阅读顺序：[阶段交接](docs/08-local-usage-and-handoff.md) → [现行结果与 Skill 契约](docs/12-native-answers-and-skill-refinement.md) → [部署与更新](docs/deployment.md)。需求和验收分别见 [01](docs/01-requirements.md)、[02](docs/02-delivery-and-acceptance.md)。

## 唯一产品默认执行链

```text
统一用户输入 / 定时触发
 -> TaskService 有界准入与独立任务
 -> OpenClaw + 模型 + Skill 自主调查、判断、执行、验证和回答
 -> 原生 CLI outcome
 -> ScopeX 保存正文、来源、执行状态和审计
 -> Vue 展示
```

正常任务直接交付原生回答。除脱敏、长度约束和状态/来源封装外，不另调模型改写结果。`native_answers=True`，`report_meta.postprocess_model_calls=0`；不要求 Claims JSON、嵌套报告 JSON 或固定业务 schema 才可交付。

预算中断、原生错误和截断不能因为有文字或 Evidence 变成成功：有有效可见文字时为 `FAILED + partial` 草稿，无文字时为 `FAILED + unavailable`。完整原生执行与文本交付才标为 complete；这不是业务语义正确率认证。旧版“中断后再生成完整报告从而显示 COMPLETED”的路径不再是产品默认路径，不为旧问题恢复报告后处理链。

`result.json` 保留 version=2、report_text、report_meta、execution_status、investigation_reasons；另存 report.md/final.txt。通常 producer=openclaw；已核验的 Locator 无数据终态为 producer=scopex_no_data，不能冒充模型诊断。

StructuredFinalizer / Claim Validator / ReportComposer / TextReportComposer 仅供历史、专项兼容及独立回放。旧结构化架构见 [历史报告说明](docs/architecture/08-trusted-report-composer.md)，不是当前主链。

## 业务边界

- 当前资源：目标是本次宿主机状态，不做历史采集、不用工具 Sandbox 状态冒充宿主机。Runtime 容器部署下进程、磁盘、GPU 等来源范围仍需逐字段现场对照；这不是已经确认的现场故障。
- 图片：调查阶段直接看原始 JPG；指标仅辅助筛选，抽样不外推整小时正常。不再额外调用报告模型重新看图。
- 编码器：前进、持续后退、停止、回弹和归零后累积均是允许行为；反向距离/时长、归零或少见本身不证明异常。产品使用 `--motion-report` 的 schema 5 运动过程报告，按时间、数值、形态和连续性解释，不能把过程数当故障数。
- 乳头 KPI：命名牛周期分母、最终采用帧 2D NippleNum、每牛最多4；缺最终结果保留分母且不伪装成观察到0。

具体口径见 [业务能力](docs/business/01-business-capabilities-v1.md) 和 [数据目录](docs/business/02-data-catalog-and-bounded-access.md)。唯一目录语义配置为 `config/data-catalog.json`；业务目录只读，task scratch 独立可写，Skill 和 Locator references 在 Runtime 启动时同步。

固定窗口无数据应明确结束，不改时间、来源或猜时区。硬结束目前只覆盖已知 Locator 的可核验 no_data 契约，其他工具零样本仍受提示约束，不宣称全工具硬拦截。

## 并发、部署与配置

产品默认2活动任务、16等待项、排队600秒；活动名额可配1–4。不同任务上下文/Runtime/Scratch 独立，排队不提前构建 Runtime 或采快照。暂停保留名额；同一 schedule 已有活动/等待项时跳过新触发。离线错过不补跑，重启不自动重做未完成任务。

端侧使用 `deploy/edge/compose.yaml`，操作入口是 [docs/deployment.md](docs/deployment.md)。ScopeX Runtime 和 vLLM 都运行于容器；二者 `restart: "no"`，不擅自开启自动重启。旧宿主机/systemd 方案只供历史参考，不与 Compose 混用。

| 项目 | 程序默认值与端侧配置 |
|---|---|
| 原生 compaction | 直接运行 CLI 默认关闭；端侧 Compose 显式 `--enable-compaction` 开启 |
| 图片额度 | 通用默认4；edge 配置默认 `SCOPEX_IMAGE_LIMIT=12` 同时传给模型和 ScopeX；每次 view_image 最多2张 |
| API 监听 | CLI 默认 loopback；edge 通过显式可信网络参数绑定 VPN IP |
| vLLM 并发参数 | 仓库 Compose 的 max-num-seqs=1；2任务准入不等于 GPU 批处理加速 |
| 存储 | app/config/data/model 分离；代码更新不应覆盖配置、历史任务和模型 |

served id 为 `qwen3.8-27b-nvfp4`，模型接口为本机 `18002/v1`；Runtime Dockerfile 固定 OpenClaw 2026.9.2。以上是仓库配置，不代替端侧进程参数核验。12张配置、压缩开关和过夜运行回执都不自动证明全分辨率容量、长上下文恢复或整批吞吐收益。

## 验证与后续

```bash
python3 -m unittest discover -s tests -v
(cd frontend && npm run build)
```

`scripts/replay_text_report.py` 是保存 Evidence 的独立历史报告回放，不代表当前原生答案主链；不自动改写旧任务。

`scripts/benchmark_task_batch.py` 对比同版代码、同样本的1路/2路整批耗时；必须同时核对结果质量和范围，不能把少一次报告调用或失败早退算作并发收益。方法见 [并发与批次测量](docs/11-text-results-and-parallel-runs.md)。

追问/跨 Run 会话恢复改造、并发设备写动作锁、网络诊断和重型点云仍不在本轮。当前已部署运行，不重做或撤销已有结果链优化；专项待验收项见交付状态文档。

## 任务结果标签

结果判定沿用任务说明中的自然语言条件，不需要业务模板或独立规则引擎。开启时由原 Agent 在同次最终回答附短标签；正文交付与执行状态不变，自动路径不追加报告/证据复核模型调用。
定时任务默认开启可关闭，普通任务可主动选择；列表支持执行状态、结果状态和推送建议筛选。手动事后文本归类须明确授权，且与原任务调用记录分开。平台推送尚未接入。
实现、接口和验收边界见 [任务结果判定](docs/13-task-result-assessment.md)。
