# POC07 — OpenClaw 通用调查能力扩展

## 目的

POC01–POC06 的结论继续成立。本 POC 不重做 Agent、Tool 或 Finalizer；只验证 ScopeX 在继续复用 OpenClaw 原生能力的前提下，能否从“日志诊断”扩展到“通用本机调查”。

核心边界：

```text
用户自然语言任务
        ↓
OpenClaw Agent 自主决定调查动作
        ↓
OpenClaw 原生工具
read / exec / process / view_image / progress_card / Skill
        ↓
ScopeX
Control / Progress / Evidence / Budget / Finalizer / Audit
```

ScopeX 不新增 CPU Tool、Memory Tool、Image Search Tool、Shell Tool，也不把调查顺序写死在 Handler 中。

## Step 1 — 可配置只读目录 — PASS

启动参数支持重复传入：

```bash
--data-dir /host/logs:/agent-data/logs
--data-dir /host/images:/agent-data/images
```

实机验证：OpenClaw 能读取外部只读 bind 中的 marker 文件，且 Evidence / Finalizer 主链可工作。

## Step 2 — Spark 主机 exec — PASS

使用 OpenClaw 原生 `tools.exec.host=gateway`，没有实现 ScopeX CPU/内存工具。

实机证据：

```text
nproc = 20
Mem total ≈ 121 GiB
```

Agent 自主执行了资源概况与进程排序命令，并观察到 Spark 上真实的 `openclaw-agent`、`claude`、`VLLM::EngineCor`、`vllm`、`gnome-shell` 等进程。因此确认命令运行在 Spark Gateway 主机，而不是 1 CPU / 512 MB sandbox。

当前最终任务出现 `investigation_completed_without_evidence` 是预期的独立缺口：现有 Evidence Pipeline 仅覆盖 read，尚未把 exec Tool Result 纳入 Evidence。该问题留到 Step 5，不回退 Step 2 结论。

## Step 3 — 图片自主调查 — IN PROGRESS

启用 OpenClaw 原生 `view_image`，不实现 ScopeX 图片查看工具。当前本地模型已经声明 `input=["text","image"]`，因此 `view_image` 直接使用当前模型；无需额外 image model fallback。

启动增加：

```bash
--enable-view-image
```

图片目录仍通过 Step 1 的只读 bind 暴露，例如：

```bash
--data-dir /host/poc07:/agent-data/poc07
```

**Step 3 使用 `--exec-host sandbox`。** `/agent-data/...` 是 sandbox 内的 bind 目标路径；如果继续使用 Step 2 的 `--exec-host gateway`，`find /agent-data/...` 会在 Spark 主机执行，而主机上不存在该容器路径。混合“主机调查 + sandbox 数据调查”的 per-call host 路由留到后续单独验证，不在本步骤提前处理。

测试目录应同时包含日志和多张带时间信息的图片。只给自然语言任务，例如：

> 分析 10:15 左右发生的问题。请自己在 `/agent-data/poc07` 中查找与该时间段相关的日志和图片，根据问题选择需要查看的图片并分析；不要假定具体图片文件名。

通过条件：

- Prompt 不提供具体图片文件名；
- Agent 自己用 OpenClaw read/exec 搜索时间窗口内文件；
- trace 中出现真实 `view_image`；
- `view_image` 参数指向 Agent 自己找到的图片；
- 模型看到图片后继续自主调查或形成图片观察；
- 不创建固定“先日志后图片”的业务流程。

注意：Step 3 只验证 OpenClaw 原生视觉调查能力。图片 Tool Result 是否进入 Evidence Catalog 留到 Step 5。

## Step 4 — Progress v2 — PENDING

优先验证 OpenClaw `progress_card` 是否能提供可展示的阶段状态。ScopeX 继续从 trace 展示真实动作：

```text
调查状态/计划
读取文件 /path
执行命令 command
命令结果摘要
查看图片 /path
新增 Evidence
```

不展示隐藏 chain-of-thought，只展示模型显式提交的计划/状态和真实工具行为。

## Step 5 — 通用 Tool Observation Evidence — PENDING

```text
OpenClaw trace
    ↓
Tool Call + Tool Result
    ↓
ScopeX Observation Adapter
    ↓
Evidence Catalog
```

至少覆盖：
- read：精确文件/行来源；
- exec：执行位置、command、exit/result、时间；
- view_image：图片路径、内容哈希/元信息、模型观察来源。

这里只做 provenance/证据身份，不重新实现工具。

## Step 6 — Budget / 收敛 — PENDING

验证：
- 简单任务完成后立即结束；
- 有新 Tool/Evidence 时允许继续；
- 连续无新信息或 request/tool/context/elapsed 预算达到时进入 Fresh Finalizer；
- hard deadline 只作为最终保险。

## Step 7 — 迁入产品 Runtime — PENDING

只有 Step 1–6 的真实运行证据通过后，才把验证过的配置和 Observation 能力固化到正式 Runtime/API/UI。

## 不做的事情

- 不新增 CPU/内存/进程专用 Tool；
- 不实现第二套 shell 执行器；
- 不实现第二套图片搜索器；
- 不把“日志 → 图片 → 系统”的调查顺序写死；
- 不用 UI 展示模型隐藏思维链；
- 不因为验证阶段方便而让 POC07 改写 POC01–POC06 的冻结结论。
