# 交付、验收与当前状态

状态：**2026-09-14 当前有效版本**。严格区分：

- **PASS**：已有真实实施证据；
- **已实现**：代码已落地，但仍需当前版本完整回归/真实环境验证；
- **待完成**：尚未完成。

## 1. 当前产品形态

```text
Vue 3 Result-first UI
        ↓
FastAPI loopback API
        ↓
TaskService / ScopeX Runtime
        ↓
OpenClaw + qwen3.8-27b-nvfp4
        ↓
local vLLM
        ↓
Business Skills + read / exec / process / view_image
        ↓
Evidence -> Fresh Finalizer -> Validated Claims
        ↓
Claim-bounded Product Answer + deterministic fallback
```

OpenClaw + 模型拥有自主调查/工具/动作/验证循环；ScopeX 不重新实现 Agent Loop，只负责产品控制、范围、权限、Evidence 和可信输出。

## 2. 已验证冻结基线

| 能力 | 状态 | 说明 |
|---|---|---|
| Runtime MVP | **PASS** | Task → OpenClaw → Tool → Evidence → Finalizer → Result |
| Stop / Resume / Steering | **PASS** | 同 Session、安全请求边界 |
| Evidence-Calibrated Output | **PASS** | 精确 E refs、Claim Validator、deterministic renderer |
| 6A Context / Compaction | **PASS** | OpenClaw 原生 compaction + state retention |
| 6B Large Data / Multi-Image | **PASS** | 120k CSV、48 图、有界 working set、task scratch |
| 6C Hard Budget | **PASS** | request/time budget 单一执行层 + partial finalization |
| 6D Native Loop Convergence | **PASS** | OpenClaw loopDetection + runtime-control Evidence filtering |
| 6E Complex Task Capability | **CAPABILITY PASS** | 日志 + telemetry + 图片 + constrained recovery + post-action verification |
| 6F Product-default Gate | **PASS** | 同任务约 `371.1 s / 14 requests`，进入 `600 s / 16 requests` |

Step 6A–6F 继续作为冻结回归基线。

## 3. Step 7 产品实现

| 子阶段 | 当前实现 | 状态 |
|---|---|---|
| Product Answer | `claims + evidence` 重新校验后生成 `answer.json`；保留 claim ids 与 deterministic fallback | **已实现** |
| Result-first UI | 结论 / 说明 / 执行情况 / 建议置顶；Progress/Evidence 辅助化 | **已实现** |
| Spark Product Integration | FastAPI/Vue 路径已进入真实任务测试 | **进行中** |
| Real Business Acceptance | 已进入第一批正式业务能力 | **进行中** |

真实业务阶段暴露并修复：

- Finalizer `finish_reason=length` 截断：加入有界输出和同 Evidence 一次无工具恢复；
- 单图/明确范围任务过度调查：加入 task-scope / minimal sufficient evidence / stop contract；
- Generic shell output 不自动等同业务动作验证；
- observed 文本/命令事实的用户可见内容继续以原始 Evidence 为准。

## 4. 第一批业务能力 V1

当前默认 Built-in Skills：

```text
system-health
image-quality-diagnosis
nipple-recognition-analysis
encoder-health
log-context
```

旧 `cow-disinfect-diagnosis` 保留作历史/专项回归，不再默认加载。

详细业务说明：

```text
docs/business/01-business-capabilities-v1.md
```

### 4.1 system-health — 已实现 / 待 Spark 真实验收

已实现：

- host `scripts/collect_system_metrics.py`；
- user-systemd 30 秒 timer；
- CPU/load、内存、磁盘、GPU、Docker、top process 事实；
- 有界 JSONL history；
- Runtime `--system-metrics-dir` 只读挂载；
- Skill 历史窗口摘要。

设计边界：Agent 不拿 Sandbox 的 `/proc/free/df/nvidia-smi` 当 Spark host 数据，也不因此获得 gateway shell。

待验收：Spark 连续采样、历史时间窗口、Docker/GPU 字段真实形态、文件上限行为。

### 4.2 image-quality-diagnosis — 已实现 / 继续真实验收

已有 direct original `view_image` + bounded metrics；单图任务要求不默认 `exec`、不扫其他数据、证据够停止。

### 4.3 nipple-recognition-analysis — 已按真实业务纠正 / 待完整时间窗对账

真实图片/JSON/日志确认后，第一版“以 JSON 为主”的设计已撤回。原因：检测或推理失败时 JPG/JSON 可能不保存，文件数不能作为总牛数分母。

当前正式口径：

- 一头牛固定最多 4 个乳头；
- 识别数量只统计日志里的 **2D `NippleNum` 检测框数**；
- 3D nipple coordinates / `IsValid` / 3D valid count 不参与该 KPI；
- 一头牛多帧时不能取 max，也不能求和；
- 由 `New cow detecte finished -> LastImgTimeStamp` 回挂到对应检测帧，取该帧 `NippleNum` 作为最终 2D 结果；
- `NippleNum > 4` 单列 over-detection，KPI 最多按 4；
- 已开始但没有最终结果的牛周期仍保留在保守分母中。

当前工具：

```text
skills/nipple-recognition-analysis/scripts/nipple_stats.py
```

输出 `total_cows / complete_four_nipple_rate / capped_2d_detections / expected_nipples / nipple_recognition_rate / distribution`，并保留逐牛最终时间戳和行证据。

JPG/JSON 现在只做辅助核对：如果 artifact 目录完整，可以验证最终 `LastImgTimeStamp` 文件是否存在，以及 JSON 的 2D marker `1..4` 是否与最终日志 `NippleNum` 一致。

仍有一个真实边界：系统完全漏掉、从未创建命名检测周期的物理奶牛，需要 RFID/视频/其他 ground truth 才能进入“实际经过牛数”。

### 4.4 encoder-health — 已实现 V1 / 待真实日志验收

已实现：

- invalid/read failure；
- sample gap；
- negative raw jump；
- large negative jump candidate；
- positive delta outlier candidate；
- flat raw candidate；
- raw/filtered diff summary；
- invalid sample 会打断连续区间，不跨失败样本制造假 delta。

V1 不做漏牛/牛位推断，不继承旧脚本 `200 mm/s`、`1.5× pitch` 等经验根因规则。

### 4.5 log-context — 已实现 V1

按显式日志 + 时间/关键词提取 bounded raw lines，保留 source/line/time/raw，只作为其他业务 Skill 的上下文，不独立诊断根因。

## 5. Analysis Sandbox

目标镜像：

```text
scopex-sandbox-analysis:step7
```

预装：numpy / scipy / pandas / OpenCV / Pillow / scikit-image / matplotlib / openpyxl / PyYAML / psutil / scikit-learn；Open3D 可选。

镜像内 `/opt/scopex/toolbox.json` 是实际能力清单。

## 6. 部署交付

当前部署拆成两层：

```text
Device Base Package（低频）
  DGX OS / Docker / NVIDIA Runtime / OpenClaw / vLLM image / model weights

ScopeX Update Bundle（高频）
  source / frontend / wheelhouse / analysis sandbox / Skills / checksum
```

`docs/09-zero-to-one-build-and-offline-deployment.md` 已补齐：

- DGX Spark Docker/NVIDIA runtime 验证；
- vLLM container 基础启动模板；
- 当前 served id 与下载 repo 的区别；
- `MODEL_REPO + MODEL_REVISION` 固定；
- Hugging Face `hf download`；
- model/vLLM 离线搬运；
- Device Base 与 ScopeX update 分层；
- TUNA APT/PyPI 使用边界；
- systemd / rollback。

当前 `qwen3.8-27b-nvfp4` 的真实 `MODEL_REPO + MODEL_REVISION` 仍需从现有部署补录到设备 manifest，这是从零重建前必须解决的事实缺口。

## 7. 当前验收 Gate

只有有实际证据后才改成 PASS：

1. `python3 -m unittest discover -s tests -v` 全量通过；
2. `tests.test_business_skill_tools / skill_provisioning / deployment_assets` 专项通过；
3. Vue `npm run build`；
4. Spark ARM64 analysis sandbox build + toolbox import；
5. system metrics timer 连续运行并能分析历史时间窗；
6. 用完整一小时轮转日志人工复算牛数、最终 2D `NippleNum`、四乳头率和乳头识别率；有完整 artifact 目录时做 JSON/JPG 辅助对账；
7. 真实编码器日志 candidate 与人工原始行一致；
8. log-context 不越界扩张；
9. 单图明确范围任务少量请求完成；
10. FastAPI + Vue 至少一个真实业务 Task；
11. Stop / Resume / Steering + refresh/reconnect；
12. Device Base + ScopeX update 双层离线 smoke + rollback。

## 8. 当前已知缺口

- 完全漏检且从未创建命名检测周期的物理奶牛缺独立 ground truth；
- 编码器 invalid/reset/物理阈值尚需真实协议/固件 profile；
- 网络 topology 未确认；
- current model 的真实 `MODEL_REPO + MODEL_REVISION` 未写入仓库/设备 manifest；
- 通用 business action provenance 仍需后续能力边界完善；
- 前端暂无 npm lockfile；
- Open3D 在 ARM64 不是强制项；
- task scratch retention/cleanup 和 mixed gateway/sandbox scratch 仍需真实复测。

## 9. 验收原则

- 不扩大 timeout/request budget 掩盖行为问题；
- 不通过降低任务要求提高通过率；
- 不把固定业务流程写成 Runtime Handler；
- 不把旧实验脚本的经验阈值无验证升级成产品规则；
- 不把模型话术当原始 Evidence；
- 不把 3D nipple 结果混入 2D 识别率；
- 不把统计异常直接等同物理根因；
- 用户范围优先，够证据即停；
- 代码合入不等于 PASS。

## 10. 当前阶段结论

> **ScopeX 已正式进入业务能力阶段。第一批能力把系统负载、图片质量、乳头 2D 识别 KPI、编码器数据健康拆成独立能力，并用 bounded log-context 提供必要上下文。乳头 KPI 已根据真实数据纠正为日志牛周期 + 最终采用帧 `NippleNum`，JPG/JSON 仅作辅助证据。当前代码仍需 Spark 单测和完整时间窗人工对账后才能标记 PASS。**
