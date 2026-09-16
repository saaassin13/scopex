# ScopeX 阶段交接与本地使用

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

编码器产品 Skill 使用 `--motion-report --events-out ...`，schema 5。允许前进、持续后退、停止、回弹、归零后累积；大反向位移、持续时间长、少见或缺少控制意图记录都不独立构成故障。按运动过程时间/形态、离轨返回、有效样本和缺口判断，必要时查询保存的 M 过程；不将编号或过程数当故障码/故障数。计数边界只是可能重置，不证明意图或全部重置已检测。

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
| 图片 | edge.env 默认 SCOPEX_IMAGE_LIMIT=12，同时供 vLLM 和 ScopeX；通用默认4，每次view_image最多2 |
| 并发/等待 | 2活动、16等待、600秒排队；活动名额范围1–4 |
| 调查预算 | turn 600秒、模型请求16、每次输出2048 tokens；不是端到端绝对耗时承诺 |
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

以现有已部署服务为基础，不重新设计结果链。真实编码器正常/异常对照、乳头逐牛分母对账、图片覆盖、资源来源、长输入压缩及1路/2路整批收益仍分别记录；“运行正常”不要求被否定，也不自动替代这些专项。

历史 TextReportComposer 回放只能验证回放器，不能代替现在的原生主链。验证当前输出使用新任务或只读检查其原生 outcome、result 与审计，不修改旧失败记录。

本轮文档修订不包含部署或重启授权，不调整模型并发、全局预算、业务阈值或容器权限。
