# Complex Task Validation and Next Plan

状态：**2026-09-14 当前有效**。

本文件记录已经在 Spark 证明的能力、当前产品实现、第一批业务能力以及剩余验收顺序。

## 1. Frozen architecture boundary

> **OpenClaw owns execution. ScopeX owns product control and trust.**

OpenClaw + 本地模型负责调查顺序、工具选择、执行、验证和停止。ScopeX 提供任务范围、能力、权限、生命周期、Evidence、预算、审计和可信产品结果。

不增加第二套 Workflow / Decision / Action Engine。

## 2. Step 6 — frozen

| Step | Result |
|---|---|
| 6A Context / Compaction | **PASS** |
| 6B Large Data / Multi-Image | **PASS** |
| 6C Hard Budget | **PASS** |
| 6D Native Loop Convergence | **PASS** |
| 6E Complex Task Capability | **CAPABILITY PASS** |
| 6F 600s / 16-request Product Gate | **PASS** |

6F 综合任务：120k telemetry + 15k+ logs + 48 images + constrained recovery + post-action verification，约 `371.1 s / 14 requests`。

该结论只证明当前基线和任务，不自动证明新模型、新业务 Skill 或任何未知任务满足同样 Gate。

## 3. Step 7 产品层

当前：

| 能力 | 状态 |
|---|---|
| Claim-bounded Product Answer | **IMPLEMENTED / ACCEPTANCE PENDING** |
| Result-first UI | **IMPLEMENTED / ACCEPTANCE PENDING** |
| Spark FastAPI + Vue | **IN PROGRESS** |
| Real business acceptance | **IN PROGRESS** |
| Edge/offline deployment | **IMPLEMENTED / SMOKE PENDING** |

### 已解决的真实产品问题

#### Finalizer length truncation

真实任务到达 request budget 后已有 Evidence，但 Fresh Finalizer JSON 可能被 `finish_reason=length` 截断。当前实现：

- 少量重要 Claims；
- 每 Claim bounded Evidence refs；
- 仅 length 时同 Evidence 一次无工具短 JSON 恢复；
- `result.json` 记录 retry count。

这不是第二次调查。

#### Explicit-scope task over-expansion

真实单图任务证明“native loop detection”不等于“业务范围控制”。当前 Runtime 通用契约：

```text
explicit target/source/scope = binding
        ↓
minimal sufficient evidence
        ↓
expand only if required by original question
        ↓
stop when supported
```

## 4. 当前主阶段 — Business V1

产品能力不再以“日志分析器”为中心，也不直接把历史脚本包装成 Skill。

当前设计：

```text
业务问题
   ↓
独立业务 Skill 的主数据源
   ↓
确定性事实/候选
   ↓ 仅在需要解释时
bounded log-context
   ↓
Agent 综合判断
```

默认 Built-in Skills：

```text
system-health
image-quality-diagnosis
nipple-recognition-analysis
encoder-health
log-context
```

旧 `cow-disinfect-diagnosis` 留作历史/专项回归，不再默认加载。

完整业务口径见 `docs/business/01-business-capabilities-v1.md`。

### 4.1 system-health

业务对象：DGX Spark host，而不是 Agent Sandbox。

实现：

```text
user systemd timer / 30s
   ↓
scripts/collect_system_metrics.py
   ↓
~/.local/share/scopex/system-metrics/system_metrics.jsonl
   ↓ read-only bind
system-health Skill
```

事实包括 CPU/load、memory、disk、GPU、Docker、top processes、collector errors。

设计目标：回答当前/历史负载，又不把 Agent `exec_host` 切到 gateway。

### 4.2 image-quality-diagnosis

主数据源是原图。单图优先 direct view；需要量化才调用稳定 metrics。脏污/起雾原因不足时允许 unknown。

### 4.3 nipple-recognition-analysis

产品 KPI 改为以 inference JSON 为主。

流程：

```text
JSON records
 -> explicit schema mapping
 -> per-cow selection (selected/latest/max, 必须显式)
 -> cow-level nipple count
 -> hourly KPI
```

V1 KPI：

- total cows；
- exact 4 cows；
- complete four-nipple rate；
- capped nipple recognition rate；
- >4 over-detection；
- selected nipple count distribution。

当前最大未决：真实 JSON schema / cow key / final-selection 语义，必须用真实数据冻结。

### 4.4 encoder-health

V1 只做编码器数据健康：

- invalid/read failure；
- sampling gap；
- negative jump；
- large negative candidate；
- positive delta statistical outlier；
- flat raw candidate；
- raw/filtered diff。

不在 V1 做漏牛/牛位推断，也不直接继承历史 `200mm/s`、`1.5×pitch` 等经验规则。

### 4.5 log-context

公共辅助能力。按明确日志 + 时间/关键词返回 bounded raw evidence；不独立诊断根因，无 anchor 时不无限扩大窗口。

## 5. Analysis Sandbox

当前目标：

```text
numpy scipy pandas cv2 Pillow scikit-image matplotlib
openpyxl PyYAML psutil scikit-learn
Open3D when ARM64 apt provides it
```

`/opt/scopex/toolbox.json` 记录实际可用能力。Runtime 网络保持 `none`。

复杂 interpreter 使用原则：优先已有 Skill script；必须临时写复杂 Python 时先写 `/task-scratch/*.py`，再直接 `python3 file.py`，避免被 OpenClaw complex-interpreter preflight 拦截。

## 6. Deployment baseline

部署拆两层：

```text
Device Base Package
  DGX OS / Docker / NVIDIA Runtime / OpenClaw / vLLM image / model weights

ScopeX Update Bundle
  source / frontend / wheelhouse / analysis sandbox / Skills / checksum
```

`docs/09-zero-to-one-build-and-offline-deployment.md` 已补 Docker、vLLM、模型选择/下载/离线搬运。

当前生产 served id：`qwen3.8-27b-nvfp4`。

必须补录真实：

```text
MODEL_REPO
MODEL_REVISION
vLLM image tag/digest
OpenClaw version
```

新模型必须重新跑 Agent/tool/image/business Gate。NVIDIA 当前 agent-ready 推荐可以作为候选，不自动替换现有模型。

## 7. Remaining acceptance order

### Gate 1 — Backend regression

```bash
python3 -m unittest discover -s tests -v
```

专项：

```bash
python3 -m unittest \
  tests.test_business_skill_tools \
  tests.test_skill_provisioning \
  tests.test_deployment_assets -v
```

### Gate 2 — Frontend

```bash
cd frontend
npm run build
```

### Gate 3 — ARM64 Sandbox

构建 `scopex-sandbox-analysis:step7`，验证 required imports + `/opt/scopex/toolbox.json`。

### Gate 4 — system-health real run

- metrics timer 连续运行；
- 当前/历史窗口正确；
- Docker/GPU 字段在 Spark 真实可解析；
- Agent 不读 Sandbox 系统信息冒充 host。

### Gate 5 — nipple JSON real-data acceptance

用户提供真实 JSON 后：

- 冻结 time/cow/nipple/final field mapping；
- 冻结 selected/latest/max 语义；
- 1 小时统计人工复算；
- malformed/missing data 显式暴露；
- >4 不提升 KPI。

### Gate 6 — encoder real-data acceptance

选已知正常 + 已知毛刺/回退/读取失败日志，验证 candidate events 与原始行一致，再用 log-context 对重要事件做小窗口解释。

### Gate 7 — image scope regression

一个明确单图任务：实际查看原图、不读排除数据、不默认 exec、少量请求结束、允许不确定。

### Gate 8 — product integration

FastAPI + Vue 跑至少一个真实业务任务，验证 Result-first / Evidence / refresh / Stop/Resume/Steer。

### Gate 9 — offline smoke

- Device Base Package：vLLM image + model + OpenClaw；
- ScopeX Update Bundle；
- ARM64 离线安装；
- systemd 自恢复；
- rollback。

## 8. Non-blocking gaps

- 真实 nipple JSON schema 未冻结；
- encoder firmware/site profile 未冻结；
- 网络 topology 未确认；
- current model 的真实 repo/revision 未补录；
- generic business action provenance 尚未完全泛化；
- frontend npm lockfile 缺失；
- task scratch cleanup / mixed gateway-sandbox scratch 仍需后续真实验证。

## 9. Merge policy

`main` 只承载当前集成基线。第一批业务 Skill 应先在分支完成单测和 Spark/真实数据最小验收，再合入；不要在同一批次混入网络 topology、机器人新动作或模型性能实验。
