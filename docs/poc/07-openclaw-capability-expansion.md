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

## 需要验证的新增能力

1. **可配置只读数据目录**：启动时把宿主机日志、图片、结果等目录映射到 sandbox，Agent 自己使用 read/exec 查找和读取。
2. **真实 Spark 主机调查**：复用 OpenClaw `exec` 的 host 路由能力，而不是 ScopeX 实现系统监控命令。
3. **本地图片调查**：Agent 根据自然语言任务自己定位时间段、搜索候选图片，并调用 OpenClaw `view_image` 查看一张或多张图片。
4. **可读调查进度**：优先验证 OpenClaw 原生 `progress_card`，同时展示真实 Tool Call/Result；不暴露隐藏 chain-of-thought。
5. **通用 Tool Observation → Evidence**：ScopeX 观察 OpenClaw trace，把真实 Tool Result 形成可追溯 Evidence；不接管 Tool 执行。
6. **自适应预算**：Runtime budget 是保险，不是工作流。简单任务自然结束；复杂任务在持续形成新信息时继续；达到预算后用已有 Evidence 收敛。

## 实施顺序

### Step 1 — 可配置只读目录 — PASS

启动参数支持重复传入：

```bash
--data-dir /host/logs:/agent-data/logs
--data-dir /host/images:/agent-data/images
```

ScopeX 只把配置转换为 OpenClaw `sandbox.docker.binds` 的 `:ro` 挂载。真实 Spark 验证已证明 Agent 能读取显式挂载的 workspace 外数据目录；不传参数时原行为保持不变。

### Step 2 — Spark 主机 exec — IN PROGRESS

当前 OpenClaw Gateway 与 ScopeX 均运行在 Spark 本机，因此主机调查优先使用 OpenClaw 原生：

```text
tools.exec.host = gateway
```

`node` 暂不引入；它用于 Gateway 与目标执行主机分离的场景。

ScopeX 新增的只是启动配置：

```bash
--exec-host sandbox|gateway|node
--exec-mode deny|allowlist|ask|auto|full
```

默认仍为：

```text
host=sandbox
mode=full
```

因此旧行为不变。POC07 为了先验证能力，显式使用：

```text
host=gateway
mode=full
```

这只是把 OpenClaw `exec` 路由到 Spark Gateway 主机，不新增 CPU/Memory/Shell Tool。

测试任务：

> 当前服务器 CPU 和内存使用率是多少，占用 CPU 和内存最大的服务进程分别是什么？请实际执行必要命令查询当前服务器，不要根据常识猜测。

同时在 Spark shell 获取基线：

```bash
hostname
nproc
free -b
ps -eo pid,comm,%cpu,%mem --sort=-%cpu | head -n 8
ps -eo pid,comm,%cpu,%mem --sort=-%mem | head -n 8
```

**通过条件**：
- 生成的 OpenClaw config 中 `tools.exec.host=gateway`；
- Agent Tool trace 中出现 OpenClaw 原生 `exec`；
- `hostname` / CPU 数量 / 内存总量证明它看到的是 Spark 主机，而不是 1 CPU / 512 MB sandbox；
- CPU/内存/进程信息与同一时段 shell 基线合理一致；
- ScopeX 没有新增系统监控 Tool。

当前已知限制：现有 Evidence pipeline 仍只自动提取 `read`，因此本 Step 只验证 **OpenClaw host exec 能力与 trace**。如果最终因 `investigation_completed_without_evidence` 失败，但真实 exec 已正确拿到 Spark 主机数据，则 host exec 能力可以单独判 PASS；Evidence 接入留到 Step 5，不在这里重造 Tool。

### Step 3 — 图片自主调查

启用 OpenClaw 原生 `view_image`，使用 Step 1 的数据目录。例如：

```text
/agent-data/logs/app.log
/agent-data/images/20260912_101459.jpg
/agent-data/images/20260912_101500.jpg
...
```

只给自然语言任务：

> 分析 10:15 左右任务失败，结合日志和那个时间段的图片判断问题。

**通过条件**：
- Prompt 不提供具体图片文件名；
- Agent 自己搜索时间窗口内文件；
- trace 中出现真实 `view_image`；
- 模型能够结合文本和图片继续调查；
- 不创建固定“先日志后图片”的业务流程。

### Step 4 — Progress v2

先验证 OpenClaw `progress_card` 是否能提供可展示的阶段状态。ScopeX 继续从 trace 展示真实动作：

```text
调查状态/计划
读取文件 /path
执行命令 command
命令结果摘要
查看图片 /path
新增 Evidence
```

不展示隐藏 chain-of-thought，只展示模型显式提交的计划/状态和真实工具行为。

### Step 5 — 通用 Tool Observation Evidence

当前 `ReadLineExtractor` 只覆盖日志读取。POC07 要验证更通用的观察模型：

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

### Step 6 — Budget / 收敛

验证：
- 简单任务完成后立即结束；
- 有新 Tool/Evidence 时允许继续；
- 连续无新信息或 request/tool/context/elapsed 预算达到时进入 Fresh Finalizer；
- hard deadline 只作为最终保险。

### Step 7 — 迁入产品 Runtime

只有 Step 1–6 的真实运行证据通过后，才把验证过的配置和 Observation 能力固化到正式 Runtime/API/UI。

## 不做的事情

- 不新增 CPU/内存/进程专用 Tool；
- 不实现第二套 shell 执行器；
- 不实现第二套图片搜索器；
- 不把“日志 → 图片 → 系统”的调查顺序写死；
- 不用 UI 展示模型隐藏思维链；
- 不因为验证阶段方便而让 POC07 改写 POC01–POC06 的冻结结论。

## 当前状态

- Step 1：PASS
- Step 2：IN PROGRESS
- Step 3：PENDING
- Step 4：PENDING
- Step 5：PENDING
- Step 6：PENDING
- Step 7：PENDING
