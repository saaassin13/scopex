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

## Step 3 — 图片自主调查 — PASS

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

实机验证结果：

- Prompt 未给具体图片文件名；
- Agent 自主使用 `exec/read` 查找日志和图片目录；
- 日志异常/失败时间为 `20:05:00 / 20:06:01`；Agent 自主匹配到 `20:05:53 / 20:06:31` 的对应时间图片；
- trace 中真实出现 OpenClaw 原生 `view_image`；
- 一次多图调用有 1 张未进入 context 后，Agent 根据工具反馈再次单独查看该图；
- OpenClaw 最终可见 answer 对三张图给出了具体视觉观察：正常图清晰、异常时刻图片过曝/泛白、失败后图片模糊；
- ScopeX 没有实现图片搜索器、图片查看 Tool 或固定“先日志后图片”的流程。

因此确认：OpenClaw 可以在 ScopeX 当前 sandbox/bind/model harness 中自主完成“定位日志时间 → 搜索相关图片 → 视觉查看 → 综合回答”。

注意：原始 OpenClaw answer 曾使用“图像质量异常导致任务失败”这一偏强因果措辞。当前证据只支持直接观察、时间关联和未证实因果假设；这不回退 Step 3 的视觉能力结论，但说明产品最终输出仍必须经过 POC06 的 Evidence / Claim Validator / deterministic renderer。图片 Tool Result 如何进入 Evidence Catalog 留到 Step 5。

## Step 4 — Progress v2 — IN PROGRESS

目标：网页能够让用户看懂 Agent 正在做什么，但不暴露隐藏 chain-of-thought。

实现分两层：

1. **OpenClaw 原生 `progress_card`**：用于真正的多步骤任务，展示模型显式提交的 plan / markdown；简单问题允许不创建 card。
2. **真实 Tool 行为**：无论模型是否使用 `progress_card`，ScopeX 都从 OpenClaw trace 展示真实动作。

启动增加：

```bash
--enable-progress-card
```

Runtime 新增 `PROGRESS_UPDATE`，只记录 `progress_card` 明确提交的：

```text
plan: step + pending/in_progress/completed
markdown: 当前状态、阻塞或下一步
```

普通工具进度增强为：

```text
exec       → title + command
read       → path
view_image → path/paths + prompt
ToolResult → 最多 1200 字符结果预览
```

这些信息都来自真实 Tool Call / Tool Result，不从模型隐藏推理中提取。

网页时间线对应展示：

```text
调查计划
✓ 分析日志并定位失败时间
→ 查找失败附近图片
○ 综合日志和图片

执行命令
查找失败时间附近的图片
$ ls ...

读取文件
/agent-data/poc07/app.log

查看图片
/agent-data/poc07/images/...
观察要求：比较曝光、清晰度...

工具返回结果
<有限预览>
```

通过条件：

- 多步骤任务若模型调用 `progress_card`，网页能显示完整 plan 和当前步骤；
- 即使没有 `progress_card`，真实 exec/read/view_image 动作仍然可读；
- command、文件路径、图片路径和工具结果来自 trace，而不是二次猜测；
- Tool Result 预览有长度上限，不把完整大输出复制到事件流；
- 不展示 hidden chain-of-thought；
- 原有 Stop / Resume / Steer / Evidence / Finalizer 行为不被改变。

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
