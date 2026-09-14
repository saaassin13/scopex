# ScopeX 阶段交接与本地使用

更新：**2026-09-14 阶段收口 / PR #14**。`main` 是唯一当前集成基线。

本次按用户要求结束长会话并合入 main。合入表示代码/文档整合，不表示 Business V1 已通过全部现场验收。之后仅在完成大阶段时更新本交接文件；小修在测试、PR 和专项复盘里记录。

## 1. 接手必读与不可回退的原则

先读本文件、README、`docs/01-requirements.md`、`docs/02-delivery-and-acceptance.md`，再读：

- `docs/architecture/06-openclaw-scopex-boundary.md`；
- `docs/architecture/08-trusted-report-composer.md`；
- `docs/business/01-business-capabilities-v1.md`；
- `docs/business/02-data-catalog-and-bounded-access.md`；
- 部署问题读 `docs/09-zero-to-one-build-and-offline-deployment.md`。

固定边界：**OpenClaw + 模型拥有自主调查、决策、动作、验证和停止；ScopeX 提供能力、权限、产品生命周期、Evidence、审计和结果。** 不重写 Agent Loop / Workflow Engine，不把业务步骤写死在 Handler。

用户要的是能执行业务的通用 Agent，不是数据看板。结果首先是可读中文，不是内部 JSON。新的业务能力通过业务说明、Skill、稳定脚本和数据目录语义接入，不在前后端分别维护字段翻译表。

用户明确范围优先，够证据即停止；不要因为挂了更多目录就继续探索。不要迎合用户下根因结论；区分事实、候选/假设和未知，用原始数据/测试证明。

## 2. 状态不能混在一起

| 项目 | 交接状态 |
|---|---|
| 6A Context / Compaction、6B Large Data / Multi-Image、6C Budget、6D Native Loop | 历史冻结 PASS |
| 6E Complex Task | 历史 CAPABILITY PASS |
| 6F 600 秒 / 16 请求产品 Gate | 历史冻结 PASS |
| Business / Product V1 | 代码已实现并整合到 main；不是业务全面 PASS |
| 收口前仓库回归 | GitHub Actions 全量 519 项 Python 测试、Vue 构建通过；最终提交检查在 PR #14 / verify workflow |
| Spark 当前版本端到端验收 | 未完成；尤其多图、编码器真实事件与报告可读性 |
| 12 张图片的 vLLM 重启及容量探针 | 命令已给出，未收到成功回执 |
| 并发、网络、重型点云 | 未实施 / 暂缓 |

单元测试主要使用模拟 Runtime/HTTP/数据，不能替代 ARM64、GPU、真实模型、业务正确率和页面人工验收。不要无证据重做 Step 6，也不要把已有 PASS 延伸成新业务 PASS。

## 3. 当前代码主链和入口

```text
统一输入 POST /runs (auto) / Schedule 创建普通 task
 -> TaskService
 -> OpenClaw Runtime + 同一模型/工具/Skill/Session
 -> 普通回答，或 Evidence -> Fresh Structured Finalizer
 -> Validated Claims -> 无工具 Report Composer
 -> 引用/分类校验 -> report.json -> Vue
```

`conversation/task` 是内部结果策略，不要求用户选择。已经尝试业务调查但没拿到依据时不能降级成普通聊天成功。兼容 `/tasks`、`/conversations` API 保留。

主要代码位置：

| 职责 | 文件 |
|---|---|
| 服务启动、挂载、Skill provisioning | `scripts/runtime_api.py`、`scopex/api/factory.py` |
| 任务生命周期/分类/查询/删除/复盘 | `scopex/api/service.py` |
| 到点触发、离线错过跳过 | `scopex/api/schedules.py` |
| OpenClaw 适配/代理请求边界 | `scopex/agent/runtime.py`、`request_policy.py` |
| Evidence 投影/原图校验 | `scopex/evidence/projector.py`、`media.py` |
| Claims / 报告 | `scopex/finalizer/structured.py`、`validator.py`、`report.py` |
| 持久化报告及 fallback | `scopex/storage/runtime_audit.py` |
| 图片额度唯一配置 | `scopex/model_capabilities.py` |
| 结果页面 | `frontend/src/views/TaskView.vue` |

Report Composer 主路径由 factory 注入 coordinator；不是第二个 Agent。`answer.py` 的确定性结果仅供兼容/fallback，不继续增加业务分支。引用/分类校验并不证明自由文本被证据语义蕴含，数字、单位、因果与范围仍要实测复核。

## 4. 业务语义（不要再重新猜）

默认 Skill：`data-locator`、`system-health`、`image-quality-diagnosis`、`nipple-recognition-analysis`、`encoder-health`、`log-context`。`cow-disinfect-diagnosis` 仅保留历史/专项参考，不默认加载。

### 当前设备资源

只分析当前宿主机 CPU/内存/磁盘/GPU/Docker/进程。每个 Run 创建时生成 `/scopex-host/current.json`，稳定 helper 输出结构化事实。没有周期采集，不做历史资源分析。源不可用即说明不可用，不能使用 Sandbox 的 `/proc`、`df`、`free` 冒充宿主机。

### 图片质量

最终脏污、模糊、起雾、水珠等判断必须看原始 JPG。Laplacian、亮度、对比度、clip ratio 只允许辅助多图筛选/分区，不能决定“无雾/正常”。时间窗用跨时间、场景和参考分区的代表图；负结论限定于实际抽样覆盖，不能外推整小时无异常。

每个 `view_image` 最多 2 张，还受完整 prompt 累计图片额度限制。omitted/truncated 的调用不能作为已看图依据。Fresh Finalizer 会 SHA 校验重新附原图；不要只提高调查阶段的额度。

### 编码器

目标是具体毛刺、回退、异常跳变、缺口：异常时间、前后累计值、signed delta、dt、局部正常水平、是否快速恢复。不要把每个 `delta < 0` 当异常，不用“负增量总数”替代用户结论。

优先应用层 `EncoderVal [N], TurnTableSpeed [...]`，raw/filtered 用于进一步对照；无应用流时才明确使用 raw。无效采样必须保留为断点，不能先删除再连接计算，恢复判断/恒值区间也不能跨断点。连续回退与孤立回退-恢复分开；候选不是硬件故障定论。

工具已有事件检测，但现场阈值、dt 归一化误判、raw/filtered/application 的逐事件传播关系仍需真实样本复核；不要宣称这些全部已验收。用户提供的历史脚本是业务分析参考，不要求兼容旧输出，也不是正确率真值。

### 乳头识别率

只统计 **2D `NippleNum` 检测框**；一头牛固定最多 4 个，3D 坐标/`IsValid` 不参与。总牛数来自命名检测周期，不是文件数；失败可能不保存图片和 JSON。

`New cow detecte finished -> LastImgTimeStamp` 指向最终采用帧，再找到该帧 `NippleNum`。不取周期 max，不把多帧相加。KPI 分母为总牛数 × 4；`>4` 保留原值/过检标记，计数封顶 4。缺最终结果不等于观察到 0，必须单列；含缺失的全周期比例是保守统计口径。完全无周期的物理漏牛不能凭日志推断。

## 5. 数据源与 Catalog

```text
Host: /opt/ScalingRobotics/CowDisinfect/Log
Agent: /agent-data/logs
Files: CowDisinfect-YYYYMMDD-HHMMSS.log[.N]

Host: /opt/ScalingRobotics/CowDisinfect/GrabbedImages/LeftCamera
Agent: /agent-data/left-camera
Layout: YYYYMMDD/HH/YYYYMMDD-HHMMSSmmm.jpg|json|pcd
```

`config/data-catalog.json` 是唯一配置源。日志按相关文件组定位，非自然整点也要覆盖；多模态直接进日期/小时目录。超预算图片采取时间分散抽样；日志不能抽样后宣称全量统计，截断必须缩窗处理。

Runtime 启动后实际生成：

```text
<workspace>/scopex-data-catalog.json                         # 宿主机副本
<workspace>/skills/data-locator/references/data-catalog.json # Locator 使用
```

Locator 从脚本相对的 Skill references 找配置，不依赖 Sandbox `/workspace` 根目录映射。单元测试不生成 `.local/workspace` 真实文件；拉代码后必须重启 Runtime 同步 Skill。不要手改 workspace 副本代替改仓库源。

目录说明是软约束，Locator 预算与 Sandbox 限额是实际工具/进程边界；尚无覆盖任意 Shell 的全局 I/O 限流。大日志仍可能占用较大内存，不能仅凭流式逐行读取就声称常量内存。

## 6. 已定位的失败和修复

| 现象 | 根因 / 修复 | 剩余验收 |
|---|---|---|
| 无效采样多算回退 | 主 raw 流先过滤无效值造成跨断点连接；`419784a` 前后的修复保留断点并补测试 | 真实异常事件人工复核 |
| 编码器 `task-de435616a790` 失败 | 调查完成；两个不同聚合事实共用 E1 被 `duplicate_claim` 错拦；`937d879` 已修 | 新 Run 能进入报告且措辞准确 |
| 图片 `task-52fcca3534ca` 失败 | 3 次 × 2 张在完整上下文累计 6 张，vLLM 限 4，返回 HTTP 400 | vLLM/ScopeX 额度对齐、探针、真实原图 |
| Catalog 文件不存在 | 误以为 workspace 根映射稳定；已移到 Skill references | 重启后检查实际生成路径 |
| 结果/证据像代码 | 逐字段翻译及 Evidence 直接展示；已切无工具 Report Composer 主路径 | 自然语言、数字、单位、范围、降级提示 |

完整两包复盘见 `docs/reviews/2026-09-14-runtime-failure-replay.md`。这些历史失败记录不能被改写为成功。复盘 ZIP 默认不包含外部原始日志/图片，缺原图时不能重新做视觉正确率判断。

## 7. Spark 实机配置（用户 inspect 已确认）

```text
Repository: /home/yanlan/workspaces/code/scopex
OpenClaw CLI default: /home/yanlan/.openclaw/bin/openclaw
vLLM container: scaling-scope-vllm-nvfp4
image tag: nvcr.io/nvidia/vllm:26.08-py3
local image ID: sha256:20b5b6d2f4709f6a72aa954b87bf46f806462314c81df095f9d20252683158b4
MODEL_REPO: unsloth/Qwen3.8-27B-NVFP4
MODEL_REVISION: f0b7c9e722f5565102fff8481c99e4d86ae099c7
host cache: /home/yanlan/.cache/huggingface/hub/models--unsloth--Qwen3.8-27B-NVFP4
container model: /hfmodel/snapshots/f0b7c9e722f5565102fff8481c99e4d86ae099c7
served model: qwen3.8-27b-nvfp4
endpoint: http://127.0.0.1:18002/v1
context: 32768; max-num-seqs: 1
gpu-memory-utilization: 0.60; kv-cache-memory-bytes: 8G
kv-cache-dtype: bfloat16; prefix caching: enabled
reasoning-parser: qwen3; tool-call-parser: qwen3_coder; auto-tool-choice: enabled
network: deploy_default; IPC: private; shm: 8 GiB
last observed restart policy: no
last observed image limit: count=4,width=1024,height=1024; video=0
mm-processor-cache-gb: 0.5
```

本地 image ID 不是 registry manifest digest。换设备优先搬运现机镜像/权重并核对，不用未知 latest 替换。OpenClaw 的当前精确版本尚需 `--version` 现场补录。

已提供保留旧容器后修改图片数为 12 的命令，**未收到执行结果**。下一会话先确认现机是否已改，不盲目重建容器。

## 8. 同步 main 与启动

先看本地改动，禁止 `reset --hard` / `git clean -fdx` 清理未知工作：

```bash
cd /home/yanlan/workspaces/code/scopex
git status --short
# 有改动时先保存/提交自己的工作，再继续。
git fetch origin
git switch main
git pull --ff-only origin main
git log -3 --oneline
```

准备/完整检查：

```bash
.venv/bin/python -m unittest discover -s tests -v
(cd frontend && npm run build)
docker image inspect scopex-sandbox-analysis:step7 >/dev/null
```

开发机保持现有 `.local` 状态目录，不因切 main 而迁移/删除历史：

```bash
cd /home/yanlan/workspaces/code/scopex
# IMAGE_LIMIT 必须用现机探针确认的容量；最后确认值是 4，12 尚待验证。
IMAGE_LIMIT=4
.venv/bin/python scripts/check_model_image_capacity.py \
  --base-url http://127.0.0.1:18002/v1 \
  --model qwen3.8-27b-nvfp4 --images "$IMAGE_LIMIT" --timeout 180

SCOPEX_MAX_IMAGES_PER_PROMPT="$IMAGE_LIMIT" \
.venv/bin/python scripts/runtime_api.py \
  --model qwen3.8-27b-nvfp4 \
  --base-url http://127.0.0.1:18002/v1 \
  --workspace .local/workspace \
  --data-root .local/runtime-api \
  --sandbox-image scopex-sandbox-analysis:step7 \
  --enable-view-image
```

只有探针 `accepted=true` 才继续；小图探针不证明全分辨率预算或视觉准确性。API 默认 loopback `127.0.0.1:8787`，远程通过既有 SSH 隧道访问，不为方便监听公网。Runtime 必须普通用户运行，不 sudo。

生产 release 的 systemd 模板改用版本目录之外的 `~/.local/share/scopex/workspace` 和 `~/.local/share/scopex/runtime-api`；现有开发 `.local` 不自动迁移。换用模板前先停服务、备份并迁移历史，详见部署文档。

## 9. 下一会话优先顺序

1. 确认 main commit / 本地工作区 / vLLM 实际图片额度，不重做未知容器切换。
2. 跑容量探针，再新建图片、编码器和当前资源任务；检查 `result.report`、`report_meta`、真实中文含义，不只看 COMPLETED。
3. 图片原图人工复核雾化/污迹/水珠，区分可见特征与物理原因；核实累计附件和上下文/4 MiB 代理体积预算。
4. 编码器核对具体事件、dt 与正跳、连续回退、raw/filtered 上下文、重复/重启边界和筛选阈值，避免把候选数量当脉冲数或硬件故障数。
5. 乳头一小时人工对账：牛周期去重、小时边界、最后采用帧、0 与缺失、封顶和分母。
6. 页面人工验收：可读报告、事实、失败/降级、日历/耗时/评价/导出/删除；随后设备重启/离线不补跑和离线包 smoke/rollback。
7. 上述稳定后才讨论最多约 2 个并发、在线排队/合并和设备写动作锁。网络 topology 未知，仍不做网络诊断。

## 10. 已知技术债（不是已完成项）

- Report / Claim 校验主要验证结构、引用和分类，不是完备的语义蕴含或数值一致性验证；自然语言根因/数量关系仍需评测。
- 前端 fallback 仍可能显示旧字段/技术结果；新报告页面需实际截图验收。
- npm lockfile 尚未入库；CI 能构建不等于传递依赖完全可复现；离线使用已构建 dist。
- 模型图片额度对齐不消除 token/内存/HTTP body 上限；不能无限加图。
- 旧 Sandbox 容器列表中出现多种历史命名，是否残留/被其他服务使用需现场检查；不能按 broad prefix 删除。
- 非终态任务断电后的状态修复、孤儿容器精确清理、审计保留策略还需专项验证。
- 日志/图像定位预算并非整个任务的 I/O 配额；编码器分析大窗口内存和 CPU 需实测。
- 完整 Device Base 离线包、服务自启动链及不可变版本回滚未获现场 PASS。

## 11. 新会话可直接粘贴

```text
请接手 ScopeX / 端侧 Agent 项目。
仓库：/home/yanlan/workspaces/code/scopex
GitHub：saaassin13/scopex
main 是唯一当前集成基线，先检查最新提交和 git status。
先读 docs/08-local-usage-and-handoff.md、README、docs/01-requirements.md、
docs/02-delivery-and-acceptance.md、docs/architecture/06-openclaw-scopex-boundary.md、
docs/architecture/08-trusted-report-composer.md，以及 docs/business/ 下两份业务说明。
OpenClaw + 模型负责自主调查/执行/验证/停止；ScopeX 不重写 Agent Loop / Workflow Engine。
Step 6A–6F 冻结，不无证据重做。Business/Product V1 已整合但真实业务尚待验收。
用户入口统一；定时只触发普通任务；断电错过全部跳过不补跑；无资源历史采集。
报告主路径是 Validated Claims -> 无工具 Report Composer；所有主结论和事实要是人话，
不要恢复逐字段翻译主路线。图片必须视觉看原图，指标只筛选；编码器看真实事件；
乳头 KPI 只算最终2D框、每牛最多4，缺图片/JSON不能从分母删牛。
先确认 vLLM 的实际图片容量：最后确认4张，12张重启/探针命令已给出但没有成功回执。
再检查真实图片/编码器/资源任务的报告与复盘，后续才做并发。
先给当前状态理解和最小验证计划，再改代码；阶段完成后才更新 handoff。
```
