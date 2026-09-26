# ScopeX 阶段交接与本地使用

视觉监控 POC 已停止；关键结论见 [FINAL-CONCLUSION.md](video-monitoring/FINAL-CONCLUSION.md)。本文件其余内容为 ScopeX 主线交接，不受本次清理影响。

更新：2026-09-16，main 集成及用户端侧运行回执收口。代码核对基线：`cb90d028773b4fac4eabf4b429ecb3fed97f1c51`。本轮只同步文档，不修改架构、运行代码、Skill、Compose、模型或端侧服务。

## 1. 当前状态与证据

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

当前优先运维范围收敛为三项；状态如下：

| 场景 | 当前状态 | 下一步边界 |
| --- | --- | --- |
| 编码器明显异常 | raw-only 链路已实现、部署并通过完整窗口任务（task-149b8b45c37d） | 定时由页面上设置（用户自行配置）；R6 已由用户确认为问题数据，其形态（静止→1000+ counts/s、端点残差 51 counts）可作后续参考模式 |
| 牛掉落（转盘滑落） | 离线回放验证完成（POC 遗留录像 50 分钟扫描 + CLI 端到端零误报），待实时 RTSP 接入（见下方专节） | 凭据就绪后接入实时流；VLM 仅作事件解释与二次确认；不做告警；异常召回待真实样本 |
| 机器人断线 | 不在本项目实现，用户移至云平台处理 | 无 |

## 4.1 牛掉落检测：当前状态与交接（2026-09-26 更新）

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

**未提交状态**：monitor/、tests/test_cow_fall_*.py（6 个文件）、scripts/scan_foreground.py、docs/monitoring/（设计文档+zones JSON）及本 handoff 更新均未提交（截至 2026-09-26）；此前上下文链路修复已提交（605d7ca）。端侧 10.200.0.14 全程只读（本次仅复制录像），未动任务服务。

编码器 PNG 只是从同一 raw 快照生成的 `working-derived` 可视化，不是原始图片 Evidence；task-149b8b45c37d 报告的 R6 已由用户确认为问题数据（同窗口其余 15 候选 compatible_motion），但整体检测仍无准确率/召回率/误报率统计，`no_anomaly_evidence`、`compatible_motion` 和无 raw 结果均不能写成设备健康证明。

牛掉落检测模块已按上节状态开发完成但未联调；机器人断线已移出本项目。不在本轮新增第二套 Agent Loop、专用调度引擎、复杂报告系统或生产部署。视觉 POC 的停止结论见 [FINAL-CONCLUSION.md](video-monitoring/FINAL-CONCLUSION.md)；牛掉落重启采用的"连续检测触发 + VLM 确认解释"符合该结论。

以现有已部署服务为基础，不重新设计结果链。真实编码器正常/异常对照、乳头逐牛分母对账、图片覆盖、资源来源、长输入压缩及1路/2路整批收益仍分别记录；“运行正常”不要求被否定，也不自动替代这些专项。

历史 TextReportComposer 回放只能验证回放器，不能代替现在的原生主链。验证当前输出使用新任务或只读检查其原生 outcome、result 与审计，不修改旧失败记录。

本轮文档修订不包含部署或重启授权，不调整模型并发、全局预算、业务阈值或容器权限。
