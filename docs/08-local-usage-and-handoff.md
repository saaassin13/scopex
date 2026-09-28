# ScopeX 阶段交接与本地使用

最新更新：2026-09-28，用户修正目标并明确本轮范围：**编码器筛查准确且可排查、任务页面清晰、相机连续三张脏污或模糊判异常**；牛滑落、喷枪断裂与机器人未动作先暂停。当前需求与待实现边界见第 7 节。

2026-09-28 后续现场操作：用户要求关闭 RTSP 视频监控，已停止专用监控容器与 SSH 隧道并禁用自动启动。ScopeX、vLLM 和定时任务保留运行；停用回执见第 7.6 节。

发布回执：2026-09-28，代码 `8c6a8f9` 已部署到 `yanlan@10.200.0.2` 的现有仓库与 Runtime，目标 Linux 831 项回归全部通过；详情见第 7.5 节。

后续发布：同版功能已按用户授权部署至 `scaling@10.200.0.7`，两个服务运行正常、RTSP 保持关闭、原四个定时计划已恢复；流程核对及验收见第 7.7 节。

视觉抽帧 POC 已停止，关键结论见 [FINAL-CONCLUSION.md](video-monitoring/FINAL-CONCLUSION.md)；其后开发的固定视角牛滑落方案也已于 2026-09-28 被用户判定不再适用：准备更换云台、视角会移动，且已有初步验证表明可靠性不足。历史部署与测试记录保留，不再据此将该方案列为待观察后即可验收的当前路线。方案停止推进不等于现场服务已经停机；本轮只读访问 workstation 的历史任务 API，未修改现场服务。

本次核对开发分支 `feature/video-inference-validation@f767dfb`，较本地 main 多 29 个提交；本轮延续本对话的未提交文档修改，完成前端、只读结果接口、编码器证据呈现和图片连续性工具改动；未修改现场配置、模型或服务，未提交或推送。下列 2026-09-16 主线记录保留原基线与证据范围。

## 1. 主线历史基线与证据（2026-09-16）

当时 main 集成及用户端侧运行回执收口，代码核对基线为 `cb90d028773b4fac4eabf4b429ecb3fed97f1c51`，不代表当前开发分支或现场运行版本。

`main` 是唯一集成基线。原 `feature/agent-native-results` 的原生回答及本轮业务改动已经进入 main，不再是“未提交/未合入”。

2026-09-16 用户确认：**已经部署到端侧服务器，过夜运行总体正常。** 记录为用户现场运行回执，不再统一写“未部署”。本次没有独立核验端侧运行 SHA、镜像和任务明细，不能直接把 main SHA 标为经过 inspect 核对的现场版本。

| 项目 | 当前记录 |
|---|---|
| 代码基线 | main@cb90d02，已包含原生回答、运动过程分析、no_data 终态、定时历史及端侧部署资产 |
| 仓库回归 | Actions 35043132700：650 tests in 31.437s，OK；编译、Vue 构建、卫生检查通过 |
| 端侧部署与运行 | 用户已确认部署并过夜运行总体正常 |
| 历史 Step 6 | 6A–6D、6F PASS，6E CAPABILITY PASS，保留原范围 |
| 精确现场版本与专项指标 | 本次未独立读取；没有新增逐项 PASS、故障率或吞吐百分比 |

CI 证据：[main verify](https://github.com/saaassin13/scopex/actions/runs/35043132700)。旧 PR #14/#15/#16/#17 及本地验证是历史证据，不拿旧测试数代替本基线结果，也不删除原始历史结论。

## 2. 现行文档入口

先读本文件、README、[12 原生回答与 Skill](12-native-answers-and-skill-refinement.md)、[02 交付与验收](02-delivery-and-acceptance.md)。部署操作只读 [deployment.md](deployment.md)。

[11](11-text-results-and-parallel-runs.md) 保留并发/队列与批次计时方法，其旧 TextReportComposer 方案是历史背景；[architecture/08](architecture/08-trusted-report-composer.md) 是旧结构化报告设计。[09](09-zero-to-one-build-and-offline-deployment.md) 的宿主机/systemd 步骤仅供历史基础环境参考，不覆盖现行 Compose。POC/reviews/带日期验收文件按其原版本解释，不自动继承到当前业务。

阶段交接在大块完成后更新，不因每个小修反复重写。此次是 main 集成与已部署回执的阶段同步。

## 3. 不改变的架构与结果链

OpenClaw + 模型拥有调查、决策、执行、验证、停止和最终回答。ScopeX 只管能力、权限、生命周期、Evidence、审计、产品输出和准入。不自建 Agent Loop/Workflow，不把调查步骤写死在业务 Handler。

```text
POST /runs 或定时触发
 -> TaskService 准入
 -> 每任务独立 OpenClaw Runtime / Session / Scratch / 审计
 -> 原生调查、判断和回答
 -> cli_outcome
 -> ScopeX 原生结果保存
 -> Vue 正文、来源、状态与技术详情
```

产品 Factory 设置 `native_answers=True`、`concise_terminal_handoff=False`。TaskService 优先调用 `finish_native_answer()`，不进入旧的“预算到达后再调用 Finalizer/Composer”分支。结果 version=2，保留 report_text/report_meta/execution_status/investigation_reasons，`postprocess_model_calls=0`。

预算中断、原生 error/aborted/length、进程失败不能因 Evidence 或文字非空转为成功。有有效原生文字时 `FAILED + partial`，无文字时 `FAILED + unavailable`；异常框架提示不当作模型草稿。正常交付 `COMPLETED + complete` 只说明执行和文本完整，不认证全部业务语义。

**旧版“预算中断但完整报告使任务显示完成”不再列为当前默认链待修复问题。** 不为它恢复 TextReportComposer、Claims、第二次模型总结或其他架构改造。相关回归在 `tests/test_native_answers.py`，包括超时有 Evidence 仍为草稿失败、无额外报告调用。

普通回答和业务任务共用 Runtime 与原生交付路径；业务访问用于内部模式和审计，不以固定业务 schema 作为文本交付门槛。当前 producer 通常为 openclaw；已核验 Locator 无数据终态标为 scopex_no_data。历史报告与独立回放保留，不自动重写历史任务。

## 4. 当前业务边界

2026-09-24 当前分支继续收敛编码器改造：工具仅分析 raw，使用 `scan / plot / inspect / report` 和保存的 schema 6；停止、正反转本身不构成异常，单次分析覆盖用户指定的整个非重叠窗口，不因确认一处而停止后续候选复核。`plot` 从同一快照生成 raw 累计、采样间隔、signed Δraw 和 raw-derived speed 四面板图；不显示或使用 `filtered`、`TurnTableSpeed`。原始行、计数格式、时间边界、可选设备 profile、确认/未决/未查看状态详见[工具方案与验证记录](architecture/09-encoder-analysis-proposal.md)。旧 schema 5 的现场回执仍属历史版本，不套用于本次改造；旧结果不改写。复用现有 OpenClaw/ScopeX 原生交付，不新增模型、服务或报告后处理。

图片由调查 Agent 实际查看原图，指标仅辅助筛选；预留累计额度给必要复核，未观察时段不外推。原生回答不再追加报告模型重新附图。

乳头 KPI 仍用命名牛周期、最终 LastImgTimeStamp 的2D NippleNum、每牛封顶4；缺最终结果保留分母并单列，不伪装成观察到0。

资源目标仍是本次宿主机状态，不引入历史采样。Runtime 已容器化，`host_snapshot.py` 在调用进程环境采集，进程/挂载/GPU等来源范围需要逐字段现场对照。此项是静态审查后的专项核验，不等于已经确认端侧故障，更不据此否定用户过夜运行回执。本次不改采集实现或容器权限。

固定窗口无数据结束，不换窗口、来源或猜 UTC。请求边界硬终态只适用于已知 Locator 的匹配 no_data 契约；其余零样本仍由指令约束。不可用/读取错误不等于无数据。

## 5. 部署与参数：区分默认值、配置和现场回执

现行入口是 `deploy/edge/compose.yaml`，命令见 deployment.md。ScopeX Runtime/vLLM 容器的 restart 均为 no；设备重启或退出后人工启动，不能按历史 systemd 自恢复方案修改它。

| 配置 | 当前仓库事实 |
|---|---|
| 原生 compaction | CLI/LocalRuntimeConfig 默认 false；edge Compose 显式 --enable-compaction |
| OpenClaw | Runtime Dockerfile 安装版本2026.9.2；现场有效版本仍以运行资产为准 |
| 上下文窗口 | vLLM `--max-model-len` 与 ScopeX `--context-window` 均为 98304（2026-09-24 晚，实测全窗口任务需 46k prompt、~28 次调用后从 65536 再上调）；OpenClaw mid-turn prompt 预算恒为 `窗口-20000`（reserve 硬下限、调优项已退役），现值 78304；模型原生 262144，KV cache 实测约 121k tokens（98304 为上限内的安全值），扩容不增加显存 |
| 图片 | edge.env 默认 SCOPEX_IMAGE_LIMIT=12，同时供 vLLM 和 ScopeX；通用默认4，每次view_image最多2 |
| 并发/等待 | 2活动、16等待、600秒排队；活动名额范围1–4 |
| 调查预算 | edge turn 1800秒、模型请求48（CLI 默认仍 16/600秒）、每次输出2048 tokens；1800 容纳一次 600秒原生 compaction 加余下调查，不是端到端绝对耗时承诺 |
| 模型 | served id=qwen3.8-27b-nvfp4，本机18002/v1；Compose max-num-seqs=1 |
| API | CLI默认loopback；edge显式绑定VPN IP及可信网络参数；不是任意公网开放 |
| 目录 | app/代码、config/设备配置、data/运行资产、model/权重分离 |

已有镜像、模型、额外挂载和数据目录均不因文档同步自动变化。不要照旧宿主机命令另起一个服务，也不要按旧说明关闭 edge 已显式开启的压缩。长任务压缩实际触发和恢复效果仍需独立证据。

## 6. 并发、调度与记录

任务 Runtime/Scratch/审计独立；排队不创建 Runtime 或提前采快照，准入后才准备。历史相对窗口用创建/计划时刻；资源用实际采样时刻。暂停保留逻辑名额；队列有上限、超时和取消。

定时仅创建普通 task，在线可有限排队；同一 schedule 已有活动/等待项时跳过，不无限积压。离线错过不补跑，重启前排队项过期，其他未完成项记中断，不重做动作。一个 data-root 一个 API 进程。

全局活动入口、月历、按 schedule_id 的跨日期历史、评价、review ZIP 和显式原始数据收集入口均已实现。终态删除仅清理 ScopeX 资产，不删除外部业务源。

追问/跨 Run 会话恢复改造仍暂缓；保留旧接口不等于新增完整会话产品。并发写设备锁、网络诊断、重型点云不纳入本轮。

## 7. 后续验证与交接边界

2026-09-28 用户最终修正：编码器要求**准确**，不是减少上报数量；大量无法解释的候选不构成可用交付。页面要清楚展示任务标题与结果。相机条件明确为同一相机连续三张原图有脏污或模糊即报异常。第三组暂不开发。

| 范围 | 已确认目标 | 本轮状态 |
| --- | --- | --- |
| 编码器 | 准确筛查，并给出可理解、可追溯、可行动的排查依据 | 已补全比较与来源、纠正数值和状态措辞、接入页面曲线；检测准确率未完成现场验收，不以隐藏候选或提高阈值替代 |
| 任务页面 | 明确区分标题、结果、执行状态，减少同时铺开的信息 | 已实现并在本地浏览器走通列表、搜索、筛选、分页、新建、详情与独立定时历史；2026-09-28 已部署至 10.200.0.2 |
| 相机脏污/模糊 | 30/60 分钟窗口内，连续三张原图存在可见脏污或模糊即判异常 | 已实现邻帧定位、连续性核验与事件合并，并接入原图判断 Skill；无真实标注样本，视觉识别准确率未验收 |
| 牛滑落、喷枪断裂、机器人未动作 | 先不做 | 暂停，不新增方案开发或现场操作 |

### 7.1 编码器：准确与可排查

历史任务 `task-149b8b45c37d` 和本地用户日志副本已只读复核：R6 在 `2026-09-22 04:15:48:267–04:15:48:326` 的 59ms 内原始计数增加 51，单步峰值约每秒 1238 个计数；前/后邻近参考速率中位数分别为 0 / 23.81。该疑点已被现有检测器发现，工具结论仍为 unresolved；用户此前确认为问题数据。用户本轮进一步确认：**转盘当时在动，但读数变化不符合实际速度或位置**。这是该案例的现场判断依据，不能用“允许启停”排除；同样不能把 raw 平台段直接解释成转盘物理停止。工具尚无独立运动参考，不能据此宣称其他窗口已能自动确诊或准确率已达标。

本轮保留原有候选、阈值和完整窗口检查，不按数量筛掉疑点。`report` 现在为已检查候选携带 `review`（前后比较、端点残差、检查依据）和原始计数、时间、文件行号；不必再从大段 trace 中找关键点。修正 Skill 与兼容显示中两项错误表达：`compatible_motion` 不等于证实正常；`deviation` 是相对局部波动尺度的偏离倍数，不是比正常速度快几倍。要求原生回答明确“观察到了什么、为何可疑、仍不能排除什么、具体再查哪项证据”，不泛泛要求用户提供 profile。

任务详情可直接选看保存的总览及局部 PNG，中文说明四个面板及疑点时间的读法。图仍是同一 raw 快照的派生辅助，未经确认的邻近曲线不是正常基线。历史任务正文和判定不改写，也不补调报告模型。

剩余验收：以 R6 为已标注正例，并用人工确认的正常启停、反转及异常片段核对错误上报和漏检；已确认判据需要比较实际运动与读数；下一步核对可取得的独立速度、位置或运动状态来源，不能用同一编码器派生的速度自证。仅修展示和措辞不能证明判定精度提升，本轮没有宣称这一点。

### 7.2 页面：先看到标题与结果

首页改为结果列表，独立展示标题、结果摘要/正文摘录、执行时间及中文执行状态；保留全部任务，另提供需要关注、定时、手动入口，日期、内容搜索、状态筛选及每页 30 条分页。服务端先筛选再分页，只为请求的当前页读取结果摘录；不新增模型摘要，未完成结果保留未完成身份。

定时页先展示已配置检查，新建表单与长指令收起；每个检查有独立执行历史。详情保留完整原始任务说明、按段落/标题展示原生报告、直接预览编码器曲线；评价、原始数据和技术记录默认收起。报告按 Vue 文本节点呈现，不执行正文 HTML。终态结果加载期间明确显示“正在读取”，不误显示没有结果。旧监控/标定入口收在“历史监控”下，未停止已有容器。

新增只读接口：`GET /tasks` 可选 `query/trigger_type/attention/include_preview`；`GET /tasks/{id}/plots` 及 `/plots/{index}` 预览本任务 PNG，校验目录归属、文件类型与快照哈希，拒绝越界路径、符号链接与内容变化。历史数据无需迁移；现有任务/调度/结果链保持原归属。

### 7.3 相机：连续三张脏污或模糊

用户明确没有标注样本，但给出业务条件：**连续三张存在脏污或模糊即可报异常**，取代之前“频繁门槛待定”的讨论。工具 `image_quality_window.py` 支持：

- `neighbors`：在指定相机和 30/60 分钟窗口内列出可疑原图前后最多五张相邻留存原图，支持跨小时目录；只定位，不把返回路径当已查看。
- `summarize`：根据 Agent 实际看过原图后记录的 degraded / clear / uncertain，核对目录中的真实相邻顺序。三个相邻 degraded 触发；非相邻抽样、重复查看、夹有未查看/清晰/不确定图片都不能凑成三张。连续五张合为一个事件，不重复报三个事件。
- 原图肉眼判断为主，低清晰度指标不能直接充当脏污/模糊标签。工具核验邻接与计数，不独立认证 Agent 的图像判断。输出事件时间、张数、依据原图、相邻留存帧的时间间隔及覆盖分布，不从三张推算整窗模糊百分比或持续时间。

连续性基于 `left_camera_multimodal` **已保存原图的顺序**，不等于相机每次曝光都已保存。失败路径可能无 JPG，空目录、漏存、未查看范围不能判为清晰；未找到三连但仍有未查看图时明确证据不足。当前累计看图额度保持不变，Skill 先跨时段抽样、发现可疑处后留额度看邻帧；因此尚不能保证发现任意未抽到的短三连，真实召回仍待验证，不把工具规则测试当作视觉召回验收。

### 7.4 本轮验证、资产与下一步

- 本地 Python 3.12.7/macOS 全量回归 831 项，828 项通过；3 项仍因既有 Spark/Linux 前置条件阻断（`test_complete_main_with_mock_runtime_and_docker`、`test_completed_answer_without_gate_cannot_pass`、`test_positive_path_with_fake_native_helpers`），**全量门禁不记为通过**。本轮新增覆盖任务组合筛选、只读摘录、图表哈希/路径边界、相邻/非相邻/重复/打断/跨小时图片规则和编码器来源信息。
- Vue/TypeScript 构建、Python compileall、`git diff --check` 通过。浏览器在本地验收副本走主流程，验证真实 R6 曲线能加载、技术记录默认收起；列表窄屏无横向溢出。没有创建现场任务、调用现场模型或发布。
- 有效验收资产保留于 `.local/acceptance-20260928/`：原生任务结果只读副本、用户日志生成的 raw 快照与 R6 结果/曲线、本地 UI 验收脚本与截图、全量回归日志。用户输入 `/Users/yanlan/Downloads/scopex-data-task-faa99e9987d8/logs/source/CowDisinfect-20260922-040000.log.2` 保持不变。此目录是忽略的本地验收资产，不是生产部署目录。
- 本地预览只使用验收副本及无模型的创建桩；收尾停止预览进程。代码未提交、未推送、未部署。后续先补编码器现场判据与正常/异常对照，再验证相机视觉识别；不推进第三组。

### 7.5 2026-09-28 提交与部署回执

用户明确授权提交并部署到 `yanlan@10.200.0.2`，复用 `/opt/ScalingRobotics/scopex/app` 和原有服务。代码提交 `8c6a8f9`（优化任务结果页面并补充异常复核规则）；目标现有仓库对齐到 `feature/video-inference-validation`，原 main 引用保留。现场 `styles.css`、`CalibrationView.vue`、`MonitorView.vue` 三处差异保留为工作区改动，前端按实际部署源码构建；原有 `scopex-edge-images/` 保留。

部署前目标 Linux/Python 3.12 全量回归 **831 项全部通过**；宿主 Node 18 不符合构建要求，使用已有 Runtime 镜像中的 Node 24.21.0 完成 Vue/TypeScript 构建，没有改全局 Node。调用原 `deploy/edge/update.sh`，仅重建 `scopex-runtime`。更新后 `/health`、结果摘录、关注筛选、图表接口及实际前端资源哈希核对通过；29 条历史任务 ID/状态保持一致，edge.env 与 monitor.json 校验不变，vLLM/monitor 容器身份保持原样。实际 Runtime/Python 3.11 内新增专项 10 项通过。

回滚资产及验收回执保留在服务器 `/opt/ScalingRobotics/scopex/deploy-backups/20260928-8c6a8f9/`，本地 `.local/deploy-20260928-8c6a8f9/` 保留发布包与 Linux 回归证据。未推送 GitHub。此回执更新发布状态，不改变第 7.1/7.3 节关于业务识别准确率尚未验收的边界；第三组仍暂停。

### 7.6 2026-09-28 RTSP 监控停用回执

用户明确要求关闭 RTSP。停用前只读核实：`scopex-monitor` 实际仍在运行并拉流（帧龄约 0.36 秒），`cow-fall-tunnel.service` 为 active/enabled；不是从未接入。

已在 workstation `yanlan@10.200.0.2` 完成：`config/edge.env` 的 `SCOPEX_MONITOR_ENABLED=0`；`config/monitor.json` 的 `auto_start=false`；经控制面停止检测后，将 `scopex-monitor` 的重启策略设为 `no` 并停止容器；`systemctl --user disable --now cow-fall-tunnel.service` 停止并禁用专用隧道。没有删除容器、镜像、录像、标定、事件档案或凭据。

验证：监控容器 exited/退出码 0，隧道 inactive/disabled/MainPID=0，回环 8554/8790 均无监听。`/health` 为 ok；runtime 与 vLLM 容器 ID/启动时间不变，历史任务的 ID/状态及定时任务配置未变。视频监控 `/monitor/status` 返回预期的 503；编码器日志分析与相机保存原图分析不依赖该 RTSP 链路。

配置备份及前后回执：`/opt/ScalingRobotics/scopex/deploy-backups/20260928-rtsp-disabled-041502/`（目录 0700，配置及回执 0600）。除上述两个配置键外，edge.env 与 monitor.json 其余内容核对未变。今后恢复视频监控须明确重新启用配置、容器重启策略与专用隧道，不能按旧“已上线挂机”记录自动恢复。本轮本地仅补交接文档，未提交或推送。

### 7.7 2026-09-28 scaling@10.200.0.7 部署回执

先核对部署文档与现场：目标为旧主线应用，ScopeX/vLLM 原本均已停止，不能把第 7.5 节 workstation 的回执套给该设备。随后按既有部署授权，使用文档第 6/7 节的备份、维护停用、同步、启动和恢复流程，发布 `8c6a8f9` 功能及 `086e53f` 文档基线，并保留本对话文档更新。只同步 `app/`，没有迁移配置、删除数据或重传模型；RTSP profile 明确关闭。

完成证据：336 个发布文件哈希匹配；目标 Runtime 38 项与 Sandbox 编码器 70 项回归通过；真实编码器一分钟窗口读取 3,305 个 raw 样本；相机原生任务 `task-1783a079dd87` 在 226.303 秒内 COMPLETED/complete，记录三张实际查看的原图及连续性判定；Chrome 已实际打开列表和任务结果。此次部署验证不改变编码器实际运动对照缺失、相机识别准确率未独立验收的边界。

历史保留：原 257 条任务的 2,443 个文件哈希全部未变。四个原计划先暂停，验收后恢复原启用状态、任务内容和规则；下一次分别为 13:09:04/13:39:04（30/60 分钟，Asia/Shanghai），不补跑离线历史。ScopeX/vLLM 使用原镜像、当前 Compose 参数，均为 running 且 restart=no；vLLM healthy；8554/8790 未监听。另一台 `10.200.0.2` 的服务没有因此变更。

目标备份及回执：`/opt/ScalingRobotics/scopex/deploy-backups/20260928-edge7-8c6a8f9/`。本地有效发布/验收产物：`.local/deploy-20260928-edge7/`；包含旧应用只读备份、发布清单、测试日志、`input_changed` 的失败探针记录、成功探针与业务回执、实际页面截图。临时发布 stage 可在核验后清理；不清理回滚与验证产物。部署文档第 6 节已修正 `update.sh` 的旧描述，完整操作记录见该文档末尾。本轮仅补文档，未新增提交或推送。

## 4.1 牛掉落检测：历史实现与部署记录（2026-09-26—27）

以下保留当时的开发、部署、测试与现场回执。其“下一步”“待观察”等历史安排已被 2026-09-28 第 7 节的重新评估方向取代，不作为当前开发计划。

**场景定义（用户确认）**：牛都在转盘上，两个监控视角都看不到整牛——监控1（全景，用于检测）可见机器人（黄色外衣）、转盘和牛腿/牛屁股部分；监控2 是消毒作业近景。异常 = 牛从转盘上滑落（后腿/屁股先掉），终态半悬挂或完全落地；首报越快越好无硬性时限；不做告警。

**方案关键转折**：COCO cow 检测需要整牛形态，实测牛屁股近景 cow 零检出——主判据不能依赖 cow 检测。定为 **禁区前景检测为主**：转盘外走道正常无物体，MOG2 背景 ∩ 禁区多边形出现持续前景即触发；YOLO cow 为禁区占用时的辅助标注。并发契约：检测循环永不因事件处理阻塞；同一异常分析期间去重合并（空间 300px 内），空间分离第二事件独立触发；异常消失 ≥10s 事件关闭。

**离线回放验证（2026-09-26 完成，替代 RTSP 联调）**：生产暂不能接 RTSP，改为复用 POC 遗留在端侧 `/home/scaling/scopex-video-capture-20260923/runs/` 的 4 组 camera-96 全景录像（已复制到 workstation `/opt/ScalingRobotics/scopex/video-replay/`，~760M；端侧只读复制未动源）。用 `scripts/scan_foreground.py` 全速扫描 + CLI `--video` 实时回放（`FileReplayCapture`，ffmpeg `-re`，时间语义与直播一致）：

- 50 分钟三段录像（smoke/10m/20m/空转盘 20m）正常场景**零误报**：瞬态全部来自机器人机械臂（周期作业最长 4s；空转盘悬停最长 15.5s、前景比 0.37），已确认正常段（转盘停止、吸奶器阻挡）无持续占用；
- **机器人臂与掉落区在图像上重叠，几何挖除不可行 → 时间尺度分离**：`trigger_sustain_s` 4→30s（掉落牛被处理前持续存在，30s 首报，无硬性时限）；风险保留：机器人故障驻留 >30s 会触发（宁多勿漏，VLM 确认兜底）；
- **禁区必须用显式多边形**（`forbidden_zones`）：牛体高出转盘平台、转盘旋转使上部产生持续前景；"转盘外全部"模式只在无其他运动结构的视图成立；
- 105 秒真实片段（含 15.5s 机器臂悬停）CLI 端到端回放零事件、正常退出；
- **异常召回未验证**（无掉落历史样本）：触发链路已由合成视频集成测试覆盖，真实召回待部署后积累或模拟物测试。

**本轮修复的关键 bug**：① capture `_read_loop` 的 `stdout.read(65536)` 等满 64KB 才返回，低码率流帧延迟无界（合成测试暴露）→ `read1`；② `load_session_config` 漏解析 `forbidden_zones`（CLI 回放首跑实际用"转盘外全部"模式，修复后重验）；③ detector 前处理三连（letterbox/RGB/**COCO cow=19 非 20**），已与 ultralytics 官方逐位对齐。

**对抗性审查与修复（提交前，2026-09-26）**：25 agent 三镜头审查确认 14 项缺陷，全部处置——修复 11 项：CLI 默认 duration 路径 TypeError、`FallSession._stop` 类属性共享（曾使 quiet 回归测试假通过）、tracker pending 空间接力凑满 sustain 与并入 open event 后 stale pending 提前触发（各补回归测试）、检测线程无异常防护（静默失明）、ffmpeg 退出静默停摆（`stopped_at` + session 收尾 + main 轮询提前退出）、foreground `reset()` 不重置 warmup、`forbidden_zones` 无多边形校验、事件 meta 永停 open（补 `_mark_closed`）、SIGTERM 不清理 ffmpeg、扫描生成器泄漏 ffmpeg、CLI 测试弱断言；已知限制 3 项接受：同 chunk 多帧共享到达时间戳（30s 阈值下偏差 <1s）、follow-up 全量重扫 meta（事件量小）、标定默认值多处字面量（语义一致）。

**实时接入路径（已预验证）**：workstation 到相机 40 网段不可达（ping/554/80 全不通，仅端侧可路由）；**ssh 隧道实测可行**：workstation `ssh -N -L 8554:192.168.40.96:554 scaling@10.200.0.14` → 检测连 `rtsp://localhost:8554/...`（端侧零新增进程）。长期 7×24 需端侧常驻 ffmpeg 转发的话需用户另行授权。凭据到位后的顺序：短时拉流验证基线一致 → 半天~一天零误报观察 → 常驻进程 → VLM 确认接入。

**代码（monitor/cow_fall/，双端 38 项测试通过，已同步 workstation）**：zones（多边形+显式禁区+校验）、foreground（MOG2 禁区前景+质心+warmup+reset 复位）、rules（cow 辅助 R1/R2）、tracker（持续 30s 触发+空间绑定+去重+10s 关闭）、detector（YOLOv8n ONNX CPU）、capture（image2pipe+环形缓冲 300s+read1+FileReplayCapture+退出可见）、session（异常防护+事件 closed 落盘）、calibrate、`__main__`（stdin 凭据直播 / `--video` 回放+时长探测+SIGTERM 处理）。标定产物 `docs/monitoring/zones-camera-96-v1.json`（含标定依据），设计文档 `docs/monitoring/01-cow-fall-monitor.md`。

**workstation 资产**：模型 `/opt/ScalingRobotics/scopex/models-monitor/yolov8n-coco.onnx`；venv `/opt/ScalingRobotics/scopex/monitor-venv`；录像 `/opt/ScalingRobotics/scopex/video-replay/`（4 段 mkv + refs 参考帧 + cut-empty-100s 验证段）；代码 `app/monitor/cow_fall/`、`app/scripts/scan_foreground.py`。相机在线 `192.168.40.96/98`。

**下一步（按依赖序）**：① RTSP 凭据就绪后经 ssh 隧道实时接入（CLI 已支持，全链路已验证）；② workstation 常驻部署（先手动进程）；③ 真实掉落样本验证；④ VLM 确认任务接入（ScopeX API 建任务读事件帧目录）；⑤ 页面启停入口与事件列表。

**功能补完与容器化（2026-09-26 第二轮，①②④⑤ 已实现并在 workstation 部署验收通过）**：监控从"一次性 CLI"补完为可部署功能，全部走镜像与配置文件管理——`monitor/cow_fall/daemon.py`（会话守护：退避重启 5/10/20/60s + **帧龄看门狗**（ssh 隧道静默死亡时 ffmpeg 无限阻塞，唯一可靠信号是 latest 帧时间戳超 60s）+ 孤儿事件收尾 + 事件保留清理 + 127.0.0.1:8790 控制面 + 单 worker 带冷却的自动确认）；`docker/monitor.Dockerfile`（沙箱镜像 + ffmpeg + onnxruntime，build/export/import 脚本同步）；compose `monitor` profile（`SCOPEX_MONITOR_ENABLED=1` 启用，默认零影响，host 网络 + UID 对齐 + unless-stopped + healthcheck）；ScopeX API `/monitor/*` 代理（503 语义）；catalog 增 `cow_fall_events` 源（确认任务经 `/agent-data/cow-fall/` 读帧，`scopex_api_url` 必须用 VPN IP——ScopeX 不监听回环）；前端按任务三类重组导航（临时任务/定期任务/常驻监控/监控标定），常驻监控页含启停、标定版本不一致"重启生效"、事件列表与抽稀帧图、手动/自动确认。**部署验收（workstation 10.200.0.2，arm64）**：三容器 healthy、实时流帧龄亚秒、拔隧道演练看门狗 60s 触发退避且隧道恢复 20s 自愈、合成与真实事件确认全链路通过（真实事件 fall-1 判"明显误报"，防护罩遮挡致检测框偏移）；部署期修复：镜像补装 python3-pip（base 无 pip）、edge.env 中 RTSP URL 必须单引号（& 拆断）、auto_confirm 关闭时手动确认被静默丢弃。当前 auto_start=true、auto_confirm=false（手动确认）；zones 用 zones-camera-96-v1.json 兜底。详见 deployment.md 第 10 节及验收记录。真实掉落样本召回仍未验证（③）。

**挂机首夜误报复盘与修复（2026-09-27）**：过夜挂机产生 25 起事件（fall-1~25，02:43~07:53），用户人工复核全部为正常场景：夜视红外时段（走道护板/覆盖物）、凌晨人员着黄雨衣在转盘边作业、早晨灯切换，均被识别为异常。逐帧归因后确认四个叠加缺陷并全部修复：① 标定 v3/v4 的 `trigger_sustain_s` 被存成 **4s**（v1/v2 为验证值 30s；页面无校验，误改直接生效）——所有事件持续恰好 4~5s、瞬态全过阈值，是主因；② `session.py` 主判据（禁区前景）构造的观测借道 `fully_outside=True`，事件规则名显示为 `R1_box_center_outside`，与 YOLO cow 误检路径无法区分——改为独立规则名 `zone_foreground`（`FallObservation.override_rule_ids`）；③ YOLO cow 观测只要求"中心在转盘外"不要求在禁区内——`evaluate_frame` 在有显式禁区时增加"中心落在禁区内"必要条件（转盘外设备区/围栏误检归零，容器内实测）；④ MOG2 无全局光照突变防护（history=300@2fps≈150s 收敛，灯切换期间禁区持续"占用"）——`foreground.py` 增 `global_flash_ratio=0.25`：全画面前景占比超阈值时按安静帧处理、继续喂数让背景重学习。配套：标定保存校验对 `trigger_sustain_s<15` 给出警告（`validate_config`）。**参数修正**：从 v4 派生 v5（仅 sustain 4→30）并激活，重启容器后 running v5、帧龄亚秒；v1~v4 与 25 起误报事件档案保留作历史证据（30 天自动清理）。**验证**：本地 85 项测试通过（rules 禁区约束/foreground 闪光/replay 规则名/calibrations 警告各新增）；容器内实测禁区外误检观测数 0。**待观察**：今晚夜视时段与灯切换场景的实测表现。

**提交状态**：牛掉落检测内核已提交（c793a20）；编码器修复/直白化/标定页面与监控功能补完在分支 `feature/video-inference-validation` 后续提交（fc62b7b、52ada6c、7522619 起），未推送。端侧 10.200.0.14 全程只读（仅复制录像与 RTSP 验证），未动任务服务。

编码器 PNG 只是从同一 raw 快照生成的 `working-derived` 可视化，不是原始图片 Evidence；task-149b8b45c37d 报告的 R6 已由用户确认为问题数据（同窗口其余 15 候选 compatible_motion），但整体检测仍无准确率/召回率/误报率统计，`no_anomaly_evidence`、`compatible_motion` 和无 raw 结果均不能写成设备健康证明。

牛掉落固定视角模块曾完成部署联调，但已于 2026-09-28 停止作为当前推进路线，后续按第 7 节重新评估；机器人断线已移出本项目。不在本轮新增第二套 Agent Loop、专用调度引擎、复杂报告系统或生产部署。原视觉 POC 的停止结论见 [FINAL-CONCLUSION.md](video-monitoring/FINAL-CONCLUSION.md)，其后“连续检测触发 + VLM 确认解释”的历史实现也不代表可靠性已验收。

以现有已部署服务为基础，不重新设计结果链。真实编码器正常/异常对照、乳头逐牛分母对账、图片覆盖、资源来源、长输入压缩及1路/2路整批收益仍分别记录；“运行正常”不要求被否定，也不自动替代这些专项。

历史 TextReportComposer 回放只能验证回放器，不能代替现在的原生主链。验证当前输出使用新任务或只读检查其原生 outcome、result 与审计，不修改旧失败记录。

本轮文档修订不包含部署或重启授权，不调整模型并发、全局预算、业务阈值或容器权限。


### 7.8 停用计划编辑与照片快速查看（2026-09-28，本地待发布）

- 定时页复用表单编辑名称、内容、触发方式/时间及结果评估；仅停用计划可编辑。`PATCH /schedules/{id}` 在调度锁内检查停用状态，启用时返回 409；保存保持停用、下次时间为空，重新启用后计算新触发时间。计划 ID、创建时间及执行历史保留，无效修改不落盘。过期一次计划可改为未来时间。停用后已取得的旧触发点不会继续创建任务。
- 结果页直接显示已分析 JPG 照片、拍摄时间、缩略图；点击放大，上一张/下一张、左右键、Esc 关闭及查看原图。接口仅读取同一任务证据记录、已配置只读数据源中的图片，去重排序，并校验证据 SHA-256、JPEG 文件头及 20MiB 上限。原图清理、变化或无法读取时明确提示；不自动给照片生成新的异常标签。
- 验证：前端类型检查/生产构建通过；相关后端回归 34 项通过：沙箱内 31 项通过，3 项本地端口绑定测试受沙箱限制，提升权限重跑 3 项通过；记录见 `.local/acceptance-schedule-images/regression.txt` 与 `transport-regression.txt`。Chrome 实走停用计划回填、修改名称/内容/间隔、保存保持停用，启用计划编辑禁用；三张合成测试照片的缩略图、放大、方向键和 Esc 关闭通过，截图 `image-dialog.png`。合成图片只用于交互验收，不代表视觉识别准确率。
- 本节代码尚未提交或部署到 `.7`/`.2`。本地验收服务和专用浏览器标签完成后停止/关闭，专用生成数据清理，验收脚本、截图与日志保留。
