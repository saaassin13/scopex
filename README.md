# ScopeX

ScopeX 是部署在 NVIDIA DGX Spark 上的端侧工业 Agent 产品。用户提出目标，OpenClaw + 本地模型负责自主调查、决策、执行、验证和停止；ScopeX 提供能力、权限、任务生命周期、证据、审计和产品界面。

> **OpenClaw owns execution. ScopeX owns product control and trust.**

## 当前基线：2026-09-14 阶段收口

`main` 是唯一当前集成基线。PR #14 将 Business / Product V1 和真实失败修复统一收口；**合入不是宣告所有 Spark 业务验收通过**。

| 层次 | 状态 |
|---|---|
| Step 6A–6D、6F | 冻结 PASS，保留原验证范围 |
| Step 6E | 冻结 CAPABILITY PASS |
| Business / Product V1 | 已实现，整合进入 main |
| Python / Vue | 收口前全量 519 项测试与 Vue 构建已在 GitHub Actions 通过；最终提交检查见 PR / Actions |
| Spark 多图、真实编码器、乳头 KPI、自然语言报告 | 待当前版本现场验收 |
| 并发、网络诊断、重型点云 | 尚未实现 / 暂缓 |

**新会话先读 [交接手册](docs/08-local-usage-and-handoff.md)。** 当前事实和待办在该文件维护，不从长会话中的旧启动命令反推实现。

## 统一执行链

```text
统一用户输入 / 定时触发
  -> TaskService
  -> 同一套 OpenClaw + 模型 + Skill / Tool / Session
  -> 调查、执行、验证
  -> 普通问答，或业务证据 -> Fresh Finalizer -> Validated Claims
  -> 无工具 Report Composer -> 引用/分类校验 -> 用户报告
```

用户不选择“对话 / 任务”。`POST /runs` 内部使用 auto；业务调查失败不得伪装成无证据聊天成功。Schedule 只在到点时创建普通任务，不编排业务步骤。不增加 Router Model、第二套 Agent Loop 或 Workflow Engine。

报告主路径由模型组织中文：**结论、事实依据、可能性分析、下一步、数据限制**。`answer.json` / deterministic renderer 仅保留为故障兼容，不继续扩充逐业务字段翻译规则。引用校验不是自然语言语义正确性的证明，仍需真实任务复核。

## 业务能力

| Skill | 业务边界 |
|---|---|
| system-health | 当前宿主机 CPU、内存、磁盘、GPU 等；无周期采集/历史分析，禁止用 Sandbox 状态冒充宿主机 |
| image-quality-diagnosis | 直接看原图判断脏污、模糊、起雾、水珠；指标仅辅助多图筛选/分区 |
| nipple-recognition-analysis | 从日志恢复牛周期，按最终采用帧的 2D NippleNum 统计，每牛最多 4；不使用 3D 有效点数 |
| encoder-health | 识别具体毛刺、回退、异常跳变和采样缺口；给时间、前后值、增量、恢复情况，不把全部负增量当故障 |
| data-locator / log-context | 时间窗定位与有界原始上下文，不独立下根因结论 |

工具按请求范围工作，够证据即停止。正常资源利用率或候选事件数量本身都不能证明业务根因。

## 数据目录与访问

唯一语义配置：`config/data-catalog.json`。

```text
/opt/ScalingRobotics/CowDisinfect/Log
  -> /agent-data/logs
  CowDisinfect-YYYYMMDD-HHMMSS.log[.N]

/opt/ScalingRobotics/CowDisinfect/GrabbedImages/LeftCamera
  -> /agent-data/left-camera
  YYYYMMDD/HH/YYYYMMDD-HHMMSSmmm.jpg|json|pcd
```

目录只读挂载。日志先定位相关文件组；LeftCamera 直接进入日期/小时目录，不递归扫描历史。图片/JSON 在检测或推理失败后可能不保存，不能拿文件数量充当总牛数。

Runtime 启动同步内置 Skill，并将 Catalog 复制到 **`<workspace>/skills/data-locator/references/data-catalog.json`**。Locator 通过该 Skill 相对路径读取配置；宿主机 workspace 根目录的副本不保证能在 Sandbox 根目录读到。单元测试只使用临时目录，不会生成真实 workspace 文件。

Catalog/Skill 是访问说明；稳定脚本执行有界定位；Sandbox 有 CPU/内存/PID/超时限制。**这些不等于已实现覆盖任意 Shell 命令的磁盘 I/O 限流。** 大目录性能仍需真实负载验证。

## 证据与界面

```text
Trace：Skill、命令、源码、工具错误、模型请求
Working Data：task-scratch 中间材料
Internal Evidence：working_derived，保留 Step 6 大数据兼容
Claim-grade Evidence：原始业务依据、可复验原图、结构化业务事实
User Facts：报告中给用户看的中文事实说明
```

同一份聚合统计允许支持多个不同事实；完全重复的 Claim 仍拒绝。原始 C/E 编号和技术字段用于审计，不应成为用户主报告。

界面包含统一输入、月历及当天执行记录、任务开始/结束/耗时、定时配置、评价、复盘包与终态任务删除。删除只处理 ScopeX 自有任务资产，绝不删除外部日志、图片、JSON 或点云。

## 定时与恢复

支持每 N 分钟、每天、一次执行、启停和立即执行。离线/断电期间错过的触发全部跳过，不补跑、不制造历史 Task；一次性过期任务停用。目前在线忙碌仍 `SKIPPED_BUSY`，尚无并发队列。未来并发方案不能改变离线不补跑的要求。

## 模型与部署

根据 2026-09-14 Spark 现机配置确认：

```text
MODEL_REPO=unsloth/Qwen3.8-27B-NVFP4
MODEL_REVISION=f0b7c9e722f5565102fff8481c99e4d86ae099c7
served id=qwen3.8-27b-nvfp4
image=nvcr.io/nvidia/vllm:26.08-py3
endpoint=http://127.0.0.1:18002/v1
context=32768
```

现机最后一次确认的图片容量为 4；12 张配置已提供，**未收到实际重启及探针成功回执**。`SCOPEX_MAX_IMAGES_PER_PROMPT` 必须与 vLLM 对齐，默认 4。每次看 2 张不会清空历史图片，累计附件仍占整份请求额度。

Device Base Package（Docker/NVIDIA/OpenClaw/vLLM/权重）与 ScopeX Update Bundle（固定源码、前端 dist、wheelhouse、Sandbox、Skill）分开交付。构建源使用清华 TUNA，运行时不安装包。完整命令、持久化目录和回滚见部署文档。

## 文档与检查

- [需求](docs/01-requirements.md) / [验收状态](docs/02-delivery-and-acceptance.md)
- [架构边界](docs/architecture/06-openclaw-scopex-boundary.md) / [下一阶段](docs/architecture/07-complex-task-validation-and-next-plan.md)
- [Report Composer](docs/architecture/08-trusted-report-composer.md)
- [业务口径](docs/business/01-business-capabilities-v1.md) / [数据访问](docs/business/02-data-catalog-and-bounded-access.md)
- [交接手册](docs/08-local-usage-and-handoff.md) / [从 0 到 1 与离线部署](docs/09-zero-to-one-build-and-offline-deployment.md)
- [任务与调度](docs/10-chat-tasks-scheduling-and-feedback.md)
- [两次真实失败复盘](docs/reviews/2026-09-14-runtime-failure-replay.md)

```bash
python3 -m unittest discover -s tests -v
cd frontend && npm run build
```

`.github/workflows/verify.yml` 持续执行仓库测试和前端构建，不部署现场、不调用真实模型。`docs/poc/` 保留为历史验证材料，不覆盖当前需求与交接基线。
