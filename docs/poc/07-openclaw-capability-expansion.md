# POC07 — OpenClaw 通用调查能力扩展

## 目的

POC01–POC06 的结论继续成立。本 POC 不重做 Agent、Tool 或 Finalizer；只验证 ScopeX 在继续复用 OpenClaw 原生能力的前提下，能否从“日志诊断”扩展到“通用本机调查”。

最终边界以 `docs/architecture/06-openclaw-scopex-boundary.md` 为准：

> OpenClaw owns execution. ScopeX owns product control and trust.

```text
用户自然语言任务
        ↓
OpenClaw Agent 自主决定调查动作
        ↓
OpenClaw 原生工具 / Session / Sandbox / Approval
        ↓ trace
ScopeX
Task Control / Progress Projection / Evidence Projection /
Convergence / Fresh Finalizer / Claim Validation / Product UI
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

Agent 自主执行资源概况与进程排序命令，并观察到 Spark 上真实进程。因此确认命令运行在 Spark Gateway 主机，而不是 1 CPU / 512 MB sandbox。

当时的 `investigation_completed_without_evidence` 属于独立缺口：产品只投影 read Evidence；该问题进入 Step 5 解决，不回退 Step 2 结论。

## Step 3 — 图片自主调查 — PASS

启用 OpenClaw 原生 `view_image`，不实现 ScopeX 图片查看工具。

启动增加：

```bash
--enable-view-image
```

图片目录仍通过 Step 1 的只读 bind 暴露，例如：

```bash
--data-dir /host/poc07:/agent-data/poc07
```

实机验证结果：

- Prompt 未给具体图片文件名；
- Agent 自主使用 `exec/read` 查找日志和图片目录；
- Agent 从日志异常/失败时间自主匹配对应时间图片；
- trace 中真实出现 OpenClaw 原生 `view_image`；
- 多图上下文遗漏一张后，Agent 根据工具反馈再次单独查看；
- OpenClaw 最终回答包含只能来自直接视觉查看的具体图像观察；
- ScopeX 没有实现图片搜索器、图片查看 Tool 或固定流程。

原始 OpenClaw answer 曾出现偏强因果措辞，因此产品最终输出仍必须经过 Evidence / Claim Validator，而不能直接发布调查 Agent prose。

## Step 4 — Progress v2 — PASS

网页已实机验证能展示真实调查动作，而不是只有 `MODEL_REQUEST / TOOL_CALL / TOOL_RESULT`。

实现：

- `progress_card` 可选，用于 OpenClaw 显式 plan/status；
- `exec` 展示 title + command；
- `read` 展示路径；
- `view_image` 展示 path/paths + prompt；
- Tool Result 只展示有限预览；
- 不展示 hidden chain-of-thought。

注意：`progress_card` 是 OpenClaw durable state；ScopeX 的 `PROGRESS_UPDATE` 只是历史 UI projection，不发展成第二套 plan state machine。

## Step 5 — OpenClaw Trace → Evidence Projection — IMPLEMENTED / NEEDS REAL VALIDATION

Step 5 不再叫“通用 Tool Adapter”。Evidence 不是第二套 transcript。

```text
OpenClaw Trace  <- 执行事实源
      │
      │ project claim-grade source material only
      ▼
Evidence Snapshot <- 最终结论引用源
```

### Step 5A — Evidence Projection

生产 Runtime 已从 `ReadLineExtractor` 插件路径切换为单一 `OpenClawEvidenceProjector`：

- `read` → 精确非空行，保留 source / line / tool_call_id；
- `exec` → **claim-grade command line Evidence**：每个非空输出行独立 E ref，同时保留 command / actual host / full-result SHA256 / 原始位置；不再把整段 stdout 作为一个大 E ref；
- `view_image` → 只冻结图片身份：path / SHA256 / size / MIME；
- `progress_card` → 不进入 Evidence；
- 不执行工具，不解释业务，不保存第二套完整 transcript。

之所以把 exec 改为行级 Evidence：一条系统查询命令可能同时输出 CPU 数量、内存、多个进程等独立事实。如果整段 stdout 只有一个 E ref，Fresh Finalizer 会被迫让多个不同 fact 共用同一个证据身份，触发 `duplicate_claim`，也不符合 claim-grade Evidence 的目标。

旧 extractor 类暂时保留用于历史回归测试，但不再是产品默认路径。

### Step 5B — Multimodal Fresh Finalizer

图片 Evidence 不保存调查 Agent 的“模糊/过曝”等描述作为 raw Evidence。

Fresh Finalizer 现在会：

1. 仅通过显式 `HOST:AGENT:ro` bind 重新解析图片；
2. 重新计算 SHA256；
3. 若图片变化/丢失则 finalization 失败；
4. 使用 vLLM OpenAI-compatible multimodal Chat Completions 将原图重新直接附加；
5. 视觉 fact 必须引用对应 image E ref；
6. deterministic renderer 将这种 claim 显示为“视觉观察”。

这避免“调查 Agent 先说图片模糊 → ScopeX 把这句话当证据 → Finalizer 再证明图片模糊”的自证循环。

实机已证明图片主链可工作：最终结果能够出现引用 image E ref 的“视觉观察”。目前观察到 Fresh Finalizer 有时会把多张图片压缩为过于宽泛的场景级事实，因此 prompt 已收紧：如果多张图片存在与原任务相关的明显差异，应按图片或证据子集分别生成视觉 fact，不要把有意义的差异合并成一个宽泛描述。

### Step 5 实机通过条件

**CPU/内存任务**：

- `exec host=gateway` 输出自动形成 command line Evidence；
- 不再出现 `investigation_completed_without_evidence`；
- 不再因为多个不同系统事实共用一个大 E ref 而触发 `duplicate_claim`；
- Finalizer 只能从对应命令输出行陈述直接观察事实。

**图片任务**：

- evidence.json 中除了 app.log 行，还包含 image Evidence；
- image Evidence 有 SHA256；
- Fresh Finalizer 请求真实包含 image input；
- 最终结果能出现引用 image E ref 的“视觉观察”；
- 时间关联仍不能升级为已证明因果。

## Step 6 — Budget / 收敛减法 — PENDING

方向：避免 OpenClaw / ModelProxy / ScopeX 三套预算互相打架。

- hard timeout / request count / exec timeout 使用一个配置来源；
- ScopeX Convergence 重点保留 goal-satisfied / stale / no-new-evidence；
- 不继续发展 `max_context_chars` 这种第二套粗略上下文估算，优先使用 OpenClaw/vLLM 已有 context budget 信息。

## Step 7 — End-user Answer Composition + Product Freeze — PENDING

当前 deterministic renderer 保留为可信/audit 视图，但不作为最终唯一产品文风。

目标：

```text
Evidence
  -> Fresh Finalizer
  -> Validated Claims
  -> deterministic trust rendering
  -> constrained Answer Composer
```

原则：Claims 决定“什么可以说”，Answer Composer 决定“怎么说”；Composer 不得增加未引用的新事实或升级因果强度。

完成后再进入 Web Stop / Resume / Steer 产品交互回归。

## 不做的事情

- 不新增 CPU/内存/进程专用 Tool；
- 不实现第二套 shell 执行器；
- 不实现第二套图片搜索器；
- 不复制 OpenClaw 完整 transcript 到 Evidence；
- 不把调查顺序写死；
- 不把 progress event 做成第二套 plan state machine；
- 不对 OpenClaw exec 再叠一套 ScopeX shell approval；
- 不用 UI 展示模型隐藏思维链；
- 不因为验证方便而改写 POC01–POC06 的冻结结论。

## 当前尚未完全确认的实现细节

1. 当前 Qwen/vLLM 一次 Fresh Finalizer 最适合附加多少张图片，需要 Spark 实测；实现会在超出配置上限时明确失败，不静默丢图。
2. `progress_card` 的 durable current-state 读取接口需要在后续 refresh/restart 场景再验证；当前历史 Progress UI 不依赖该能力。
3. 单任务混用 `exec host=gateway` 与 `exec host=sandbox` 的 per-call 路由还需要实机验证；Evidence provenance 已记录实际 call host 优先，不影响当前架构。

这些细节不影响 Step 5 的架构结论，也不需要暂停当前实施。
