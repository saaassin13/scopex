# ScopeX 产品需求基线

状态：2026-09-14 阶段收口。当前实现/验收状态见 `02-delivery-and-acceptance.md`，接手状态见 `08-local-usage-and-handoff.md`。

## 1. 定位与架构

交付运行在 NVIDIA DGX Spark 的本地、可交互、可执行、可审计工业 Agent。用户给目标，模型自主观察、推理、选择工具、执行业务动作并验证结果，不是只展示数据的看板。

**OpenClaw owns execution. ScopeX owns product control and trust.** OpenClaw + 模型负责调查、决策、执行、验证和停止。ScopeX 提供业务能力、权限、Run/Session、范围、预算、Evidence、审计、报告和 FastAPI/Vue。禁止第二套 Agent Loop / Workflow Engine、Router Model、Schedule Agent。

当前实机模型标识与部署参数见 `09-zero-to-one-build-and-offline-deployment.md`；模型名称、下载仓库、revision、served id 必须分开记录，不能凭名称替换已验证模型。

## 2. 通用需求（R01–R15）

| ID | 要求 |
|---|---|
| R01 | 本地 OpenClaw / vLLM / 文件与工具 / 审计链；Sandbox 默认 network=none |
| R02 | 模型自主调查，用户不需逐步教工具，不把完整业务流程写死在 Handler |
| R03 | 原始大数据不全量塞 Context；先定位时间窗，再计算/抽样，有界输出 |
| R04 | 多图最后结论依赖实际看过的原图，累计模型图片额度和每次工具额度分别管理 |
| R05 | 有副作用的动作受能力/权限控制；当前只读诊断不能隐含授权重启或机器人动作 |
| R06 | 动作后验证业务状态；命令 exit code=0 不是业务成功证明 |
| R07 | 复用 OpenClaw 原生 compaction；不自建第二个上下文调度器 |
| R08 | Stop/Resume/Steering，安全模型请求边界接管，保留审计 |
| R09 | 请求/时间/Sandbox CPU、内存、PID、exec timeout 有界；预算不是根因判据 |
| R10 | 正式结论可追溯至业务依据/可复验原图；不把工作过程当事实 |
| R11 | 所有主结论、事实说明、建议用人话；模型负责表达，代码负责约束，禁止逐字段翻译成为主路线 |
| R12 | 任务/事件/证据/Claims/报告/评价可复盘，区分运行失败和业务结果错误 |
| R13 | 用户显式 target/source/scope 有约束力；最短充分路径，够证据即停 |
| R14 | Skill 提供领域语义、稳定脚本和停止原则，不代替 Agent 的自主执行 |
| R15 | Device Base 与 ScopeX Update 分层离线部署、校验、版本切换/回滚 |

600 秒、16 次请求是冻结的调查 turn 配置，不应把额外 Finalizer / Report 调用隐藏成“端到端一定 600 秒”。业务新增后的性能需重新计量。

## 3. 统一入口与生命周期（R16–R17）

用户只有一个输入入口，`POST /runs` 创建 auto Run，底层所有任务共用 TaskService / OpenClaw / 模型 / Session / 工具 / 审计。

未触及业务数据、正常回答可作为 conversation 完成，不强制 Evidence。已经尝试业务调查却无依据，必须报告失败/不可用，不得降级成聊天成功。定时触发始终是正式 task。兼容 API 不是用户必须选择的模式。

Run 保存开始、结束、耗时、trigger_type、schedule_id、scheduled_for 和状态。运行失败也要给可读原因；内部错误码放技术详情。

## 4. 定时触发（R18）

配置只需要名称、普通任务内容、时间/周期、启用状态。支持每 N 分钟、每天 HH:MM、一次执行、立即执行。

到点只创建普通 task，绝不编排业务步骤。例如当前磁盘、过去 30 分钟编码器、过去 30 分钟图片。相对窗口使用 planned/scheduled_for 为基准，不能在排队/重启后悄悄改变统计时间。

设备离线/断电期间错过的触发全部跳过，不补跑，也不为每个错过时间生成失败 Run。interval 保持原周期相位；daily 推进到未来日期；once 过期则停用并标记 MISSED_OFFLINE。立即执行不改变 recurring 周期。

当前单执行槽位在线 busy 记 SKIPPED_BUSY。并发/在线队列是后续设计，不改变“离线历史不补跑”。

## 5. 记录、删除、评价与复盘（R19–R22）

首页：月份日历显示每天数量/状态，点日期显示当天所有 Run，再进详情。统计只读取 ScopeX 元数据，不扫描业务目录。

终态 Run 可删除 ScopeX audit/work/scratch/快照/报告/评价/导出包，不得删除 `/agent-data` 外部日志、图片、JSON、PCD。非终态先安全停止，不直接删除；暂停与真正终止要区分。

任务评价：正确/有问题、问题标签、备注；不能根据用户评价自动改变模型/Skill。复盘导出包含存在的任务/报告/依据/技术错误和版本信息，默认不含整份外部数据或密钥。目标是定位 Model / Skill / Tool / Runtime / Evidence / Finalizer / Report / UI 的问题，不静默重做诊断。

## 6. 数据目录（R23–R24）

唯一源为 `config/data-catalog.json`：

```text
/opt/ScalingRobotics/CowDisinfect/Log -> /agent-data/logs
  CowDisinfect-YYYYMMDD-HHMMSS.log[.N]

/opt/ScalingRobotics/CowDisinfect/GrabbedImages/LeftCamera -> /agent-data/left-camera
  YYYYMMDD/HH/YYYYMMDD-HHMMSSmmm.jpg|json|pcd
```

Runtime 将语义摘要注入模型，不注入全盘文件清单；存在的 host path 自动只读挂载。机器 Catalog 随 data-locator Skill provision 到 `skills/data-locator/references/data-catalog.json`。不能假定 workspace 根文件一定映射到 Sandbox `/workspace`。

日志组文件名时间可能非整点，Locator 用组区间重叠定位；LeftCamera 直接进目标日期/小时；超预算图片分散时间抽样，不只取前半小时。日志不抽样冒充完整统计。PCD 先定位，再加载/降采样。

禁止普通任务递归扫描数据根。Catalog/Skill 说明与稳定脚本预算不能被宣传为任意 Shell 的全局 I/O 硬限制；尚未实现的 guard/索引明确记录。

## 7. 首批业务（R25–R29）

### R25 当前设备资源

只分析当前宿主机快照 `/scopex-host/current.json`。不部署历史资源 timer，不做历史 CPU/内存/GPU 分析。源不可用必须说明不可用，不允许 Sandbox fallback。一次 snapshot 的高负载不足以证明业务故障。

### R26 图片质量

单图直接看原图；多图先有界定位，再按时间/场景及可选指标分区抽样，多批视觉判断脏污、模糊、起雾、水珠、运动模糊、失焦。Laplacian/亮度/对比度/clip ratio 只供筛选和参考，不能决定“没有起雾”。

每次 view_image 最多 2 张，完整请求按 `SCOPEX_MAX_IMAGES_PER_PROMPT` 计累计附件。缺失/省略的图片不是已查看依据。负结论限定实际覆盖；看到雾化特征不等于证明冷凝水物理原因。

### R27 乳头 2D 识别率

从日志建立命名牛周期，最终 `LastImgTimeStamp` 对应帧的 `NippleNum` 为识别数。每头最多 4，超过 4 保留原值/过检标记并封顶。3D 坐标/IsValid 不参与，不取多帧 max、不多帧累加。

图片和 JSON 在失败路径可能没有，不作为总牛数分母。完整四乳头率=完整四框牛数/总牛数；总体乳头率=最终封顶框数之和/(总牛数×4)。缺最终结果保留于分母且单列，不能称为观察到 0；需要说明这是含缺失的保守统计。日志无命名周期的物理漏牛需要独立真值。

### R28 编码器事件

Locator -> 显式相关轮转文件 -> 一次稳定分析 -> 具体异常事件 -> 必要时小范围上下文。优先应用层累计值；raw/filtered 作为对照，无应用流需声明改用 raw。

检查毛刺/快速恢复、连续回退、单步回退、异常正跳、采样缺口。恒值可能是正常停止，小幅负值不自动判故障。增量要结合实际 dt 和局部正常变化；无效数据、复位、时间异常必须谨慎处理，不能删除断点再连算。结果列时间、前后值、增量和恢复，不以数千次负增量代替异常定位。候选不是硬件根因。

### R29 日志上下文

显式日志+时间/关键词，返回有界 source/line/time/raw。用于回答具体原因或补足证据，不能无锚点无限扩大或无请求扫描其他系统。

## 8. 结果与证据

```text
Trace / Working Data / Internal working_derived
  -> 供调查审计，不直接展示成用户事实

业务依据 / 原图 / structured business_facts
  -> Evidence -> Fresh Finalizer -> Validated Claims
  -> 无工具 Report Composer -> 引用/分类校验
  -> 结论 / 人话事实依据 / 可能性 / 下一步 / 限制
```

Skill、源码和 Locator 是工作过程；scratch 有界派生读取可作为 working_derived 内部 Evidence 保留 Step 6 兼容，不能直接当作用户事实。结构化统计整体是一份 Evidence，可以支持不同命题，不能因共用 E 就误判重复。

Composer 只组织已支持内容，无工具、不增加证据、不重新诊断；未知必须保留，因果假设不能改成事实。校验只保证当前实现的结构、引用和分类，不能宣称自动证明一切语义/数值。故障保留安全 fallback 并明确降级，不把 JSON 当正常报告。

## 9. 部署和未做范围

业务数据只读，task scratch 独立可写，Sandbox 无网络，包在构建期预装。默认资源为 512 MiB memory/memory+swap 上限、1 CPU、256 PID、exec 30s；这些不是完整 I/O 预算。重型点云按实测再设计独立资源配置。

离线包区分 Device Base / ScopeX Update；镜像架构、模型 revision、文件 SHA256、版本目录均可核对。可变任务数据不能放在升级会替换的 release 内。保留旧版本，不自动修改/删除现机其他服务。

并发、网络 topology、独立漏牛真值、完整历史清理策略和业务动作锁仍为后续范围。判断已完成必须依据测试或真实执行证据，不能以文档/代码存在替代验收。
