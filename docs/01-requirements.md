# ScopeX 产品需求基线

同步：2026-09-16，按 main@cb90d02 的原生回答与 edge Compose 实现更新现行口径。验收状态见 [02](02-delivery-and-acceptance.md)，接手见 [08](08-local-usage-and-handoff.md)，部署操作见 [deployment.md](deployment.md)。用户已确认端侧部署并过夜运行总体正常；本文件不是全部专项验收证明。

## 1. 定位与架构

运行在 NVIDIA DGX Spark 的本地、可交互、可执行、可审计工业 Agent。用户给目标，OpenClaw + 模型自主观察、推理、选择工具、执行业务动作、验证并回答，不是只展示数据的看板。

**OpenClaw owns execution. ScopeX owns product control and trust.** ScopeX 管能力、权限、Run/Session、范围、预算、Evidence、审计、原生结果保存和 FastAPI/Vue。禁止第二套 Agent Loop、业务 Workflow Engine、Router Model 或 Schedule Agent。

模型名称、下载仓库、revision、served id 和实际镜像分开记录，不能凭名称替换已验证资产。当前部署由 edge Compose 管理，旧宿主机/systemd 文档仅作历史参考。

## 2. 通用需求（R01–R15）

| ID | 要求 |
|---|---|
| R01 | 本地 OpenClaw/vLLM/文件与工具/审计链；工具 Sandbox 默认 network=none |
| R02 | 模型自主调查，不要求用户逐步教工具，不将业务流程写死在 Handler |
| R03 | 大数据先定位时间窗，再计算/抽样；有界输出，不全量塞 Context |
| R04 | 图片结论依赖调查时实际查看原图；累计模型图片额度与每次工具额度分开 |
| R05 | 副作用动作受能力/权限控制；只读诊断不隐含重启或机器人动作授权 |
| R06 | 动作后独立验证业务状态；exit code=0 不等于业务恢复 |
| R07 | 使用 OpenClaw 原生上下文管理；不自建压缩/上下文调度器 |
| R08 | 保留 Stop/Resume/Steering 安全边界和审计；追问及跨 Run 恢复改造暂缓 |
| R09 | 请求/时间/Sandbox CPU、内存、PID、exec timeout 有界；预算不是根因判据 |
| R10 | 来源可追溯；工作过程不直接升级为事实；不以固定业务 schema 作为原生文本交付门槛 |
| R11 | 调查模型直接用人话回答；禁止逐业务字段翻译成为主输出路线 |
| R12 | 任务、事件、证据、原生正文、状态、评价可复盘；Claims/旧报告仅历史兼容 |
| R13 | 显式 target/source/scope 有约束力；够证据即停，固定窗口无数据不擅自换窗 |
| R14 | Skill 提供领域语义、稳定脚本和停止原则，不代替 Agent 自主执行 |
| R15 | 基础设备资产与 ScopeX 更新分离；保留配置、运行数据与模型，升级可追溯 |

当前调查 turn 默认600秒、16次请求；端到端还包含准入和环境准备等，不承诺绝对600秒。正常产品不另调 Finalizer/Report 模型。

## 3. 统一入口与原生交付（R16–R17）

用户使用 `POST /runs`，内部 auto，共用 TaskService/OpenClaw/模型/Session/工具/审计。普通咨询和业务任务不是两套 Runtime。业务访问用于内部模式和审计；正常原生回答不因缺少 ScopeX 固定统计 JSON 或 Evidence schema 而被额外报告关卡阻断。

定时始终创建 task。原生失败不能降级聊天成功；有文字/Evidence不能覆盖预算、原生错误或进程失败。可见有效原生文字保留为失败草稿，无文字则不可用。

Run 保存创建/计划、开始、结束、执行/准入等待/总耗时、trigger_type、schedule_id 和状态。错误给可读说明，内部诊断留技术详情。已正常交付不等于每个数字和业务推断均已验证。

## 4. 定时与准入（R18）

配置名称、普通任务内容、时间/周期、启用状态，支持每N分钟、每天HH:MM、一次执行和立即执行。到点仅创建普通 task，不挑选业务步骤。

产品默认2活动任务、16等待项、排队600秒；活动名额可配1–4，队列有上限/超时/取消。排队不创建 Runtime、不提前采资源快照。历史相对窗使用 created_at/scheduled_for，不随排队漂移；当前资源按实际执行时采样。暂停保留逻辑名额。

同一 schedule 已有活动/等待项时跳过新触发；容量与队列均满时明确拒绝，不无限积压、不合并改变时间窗。离线错过全部跳过、不补跑、不逐个生成历史失败 Run。interval保持相位、daily推进到未来、once过期停用；立即执行不改变 recurring 周期。

重启前排队项过期，未完成运行标为中断，不自动重做动作。仅使用一个 API 进程和 data-root 锁，不使用共享内存队列的多个 worker。

## 5. 记录、删除、评价和复盘（R19–R22）

首页月历/当天 Run、全局活动任务、按 schedule_id 的跨日期执行历史共用任务元数据；不能扫描业务目录计算页面数量。定时历史先过滤后分页，现有实现仍枚举文件存储，不宣称有磁盘索引。

仅终态 Run 可删除 ScopeX audit/work/scratch/快照/正文/评价/导出资产；不能删除外部日志、JPG、JSON、PCD或Schedule。暂停不等于终止，不直接删除运行中任务。

评价保存正确/有问题、标签、备注，不自动修改模型或 Skill。review ZIP 默认仅复盘资产，不含全部外部数据/密钥。另有用户显式选择的原始数据收集，默认单包源内容2 GiB、5000文件，缺失/变化/额度不足要如实记录；不将有限包描述为完整现场拷贝。

## 6. 数据目录与有界访问（R23–R24）

唯一语义源 `config/data-catalog.json`：

```text
/opt/ScalingRobotics/CowDisinfect/Log -> /agent-data/logs
  CowDisinfect-YYYYMMDD-HHMMSS.log[.N]
/opt/ScalingRobotics/CowDisinfect/GrabbedImages/LeftCamera -> /agent-data/left-camera
  YYYYMMDD/HH/YYYYMMDD-HHMMSSmmm.jpg|json|pcd
```

Runtime 注入语义摘要，不注入全盘清单；业务挂载只读，目录配置在启动时同步到 workspace/Locator references。edge挂载路径与 Catalog host_path 当前需一同配置，不假定自动联动。

日志按组区间重叠定位，图片直接进入目标小时并按时间代表性选样；日志不能抽样冒充全量统计。禁止普通任务递归所有历史根；稳定脚本预算不等于任意 Shell 全局 I/O 硬限制。

固定窗口定位无数据或零样本，结束并说明，不改窗口/来源、不轮询，除非用户另有明确兜底要求。no_data、source_unavailable和读取/解析错误要区分。已有请求边界的硬结束仅覆盖已知 Locator 的可核验 no_data，不宣称全工具已硬拦截。按已配置数据源时区解释时间，不依据文件是否存在猜 UTC。

## 7. 首批业务（R25–R29）

### R25 当前设备资源

只分析本次宿主机状态，不部署资源历史 timer，不回答无数据支持的历史 CPU/GPU问题。目标入口仍为 `/scopex-host/current.json`，采样在实际准入准备时发生。

资源来自哪一层必须真实。Runtime 容器化后，进程、磁盘挂载与 GPU 字段的观察范围需现场逐项对照；文件名和 source 标签本身不构成完整宿主机可见性证明。缺失明确报告，不从工具 Sandbox 补造。本轮文档同步不调整采集实现/权限，不将待核验项断言为现场故障。

### R26 图片质量

单图直接看原图；多图先有界定位，首轮少量代表原图、为邻近复核留额度。指标只在能影响取样时使用，不以亮度/Laplacian等决定无雾。

每次view_image最多2张，整段Prompt累计受 SCOPEX_MAX_IMAGES_PER_PROMPT 约束；省略/缺失不是已看。原生答案不再增加第二次报告视觉调用。抽样结论限定实际覆盖，可见雾化不证明冷凝物理原因。

### R27 乳头2D识别率

从日志建立命名牛周期，最终 LastImgTimeStamp 对应 NippleNum 为计数。每牛最多4；超过4留原值/过检标记并封顶；不使用3D IsValid，不取多帧max或累加。

完整四乳头率=最终4框牛数/命名牛周期数；总体乳头率=封顶最终框数之和/(周期数×4)。缺最终结果保留分母、单列，不能说观察到0；零牛时比例不可用。物理漏牛、precision/recall需要独立真值。

### R28 编码器运动过程

前进、持续后退、停止、回弹、归零后重新累积都是允许行为。大反向位移、时间长、同窗少见、归零或不知道控制意图本身都不是故障依据。

产品 Skill：Locator明确文件 → 一次 `--motion-report --events-out` → schema 5运动过程报告 → 必要时 `--inspect-events ... --episode M编号`。默认展示3个优先过程，不代表其余正常；旧不带参数的候选schema仅兼容。

优先应用EncoderVal，保留时间/值/dt、过程形态、连续性/局部上下文。无效样本与缺口不跨越连算；离轨并返回是数据筛查，不证明硬件原因。可能归零边界不能当反向位移或已证实的重置意图。反向距离/时长只描述，不评分为统计故障。要判断动作是否符合指令，需要独立控制/业务依据，缺少该依据不等于异常。

### R29 日志上下文

显式日志+精确小窗/关键词，有界返回source/line/time/raw；默认40行、6000字符，可显式1024–12000字符。输出截断需说明，不能以窗口起点的前N行冒充覆盖目标事件。编码器优先查询已存过程，不倾倒原始日志或全部JSON。

## 8. 结果与证据

```text
OpenClaw原生调查与回答 -> cli_outcome
 -> ScopeX正文/来源/执行状态/审计 -> 用户
```

version=2正文及report_meta；postprocess_model_calls=0。预算/原生错误/截断/进程失败保留FAILED与partial或unavailable。源码、Skill、Locator及working_derived是调查材料，不直接升级用户事实。业务JSON投影只压缩空白，超预算明确记录，不剪残JSON。

历史StructuredFinalizer、Claims、ReportComposer、TextReportComposer供专项和独立回放，不是产品交付门槛。不得因旧结果链的状态问题恢复这些调用或逐字段翻译。

## 9. 部署与未做范围

edge Compose运行ScopeX/vLLM，restart均no；操作人员人工启动。业务目录只读，scratch独立可写，Sandbox默认无网络，运行期不装包。通用SandboxCPU/内存/PID/exec预算不是完整I/O限制，重型点云另验。

直接CLI默认关闭主动/完成后compaction，edge Compose显式开启；都使用OpenClaw原生实现。API默认loopback，edge显式可信网络绑定VPN IP，不承诺公网鉴权产品。代码/配置/数据/模型分离，更新不能删除历史。

并发准入/队列已实现，整批加速仍待测；追问与跨Run恢复改造、设备写动作锁、网络诊断、重型点云、完整retention和独立漏牛真值继续后置。已部署过夜运行与专项验收分开记录，不否定已有运行回执，也不扩大其范围。
