# 交付、验收与当前状态

状态：**2026-09-14 当前有效版本**。本文件严格区分：

- **PASS**：已有真实实施证据；
- **已实现**：代码已落地，但当前版本仍需完整回归/真实环境验证；
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
read / exec / process / view_image / Skills
        ↓
Evidence -> Fresh Finalizer -> Validated Claims
        ↓
Claim-bounded Product Answer + deterministic fallback
```

OpenClaw + 模型拥有自主调查/工具/动作/验证循环；ScopeX 不重新实现 Agent Loop，只负责产品控制、范围、权限、Evidence 和可信输出。

## 2. 已验证的冻结基线

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

Step 6A–6F 继续作为冻结回归基线，不因 Step 7 调整而重做架构。

## 3. Step 7 当前实现

| 子阶段 | 当前实现 | 状态 |
|---|---|---|
| 7A Product Answer | 对 `claims.json + evidence.json` 重新校验后生成 `answer.json`；每项保留 `claim_ids`；`final.txt` 保留 | **已实现** |
| 7B Result-first UI | 结论 / 说明 / 执行情况 / 建议置顶，Progress/Evidence 降为辅助区 | **已实现** |
| 7C Spark Product Integration | FastAPI/Vue 路径已进入真实任务测试，仍需本版本完整联调 | **进行中** |
| 7D Real Business Acceptance | 已用真实图片数据暴露问题并修复，但尚未形成完整 PASS 证据 | **待完成** |

### 7A 可信边界

Product Answer 不开启第二个业务诊断循环。当前投影只从已经校验通过的 ClaimSet/Evidence 生成，并在审计目录保留：

```text
claims.json
answer.json
result.json
final.txt
```

`final.txt` 仍是 deterministic trust fallback。

### 7B 用户主视图

主界面目标已经落到代码：

```text
诊断结果

结论
说明
执行情况
建议

调查进度 >
相关证据 >
可信渲染 fallback >
```

Evidence 不再占主视觉位置。

## 4. 真实业务测试新发现

### 4.1 Finalizer 长度截断

真实图片任务在 `model_request_budget` 到达后已有 Evidence，但 Fresh Finalizer 自身输出 JSON 被 `finish_reason=length` 截断，导致 Task 被标成 FAILED。

当前修复：

- Finalizer Claim 数量保持小而有界；
- 单 Claim `evidence_refs` 数量有上限；
- 只有 `finish_reason=length` 时允许一次无工具、同 Evidence 的压缩重试；
- `result.json` 记录 `finalizer_retry_count`。

这是可信输出 transport recovery，不是新的 Agent 调查回合。

### 4.2 单图任务过度调查

用户明确要求“只看一张图/直接视觉判断”时，Agent 仍可能继续读取 JSON、日志、其他图片。这不是传统重复 tool loop，因此 OpenClaw loopDetection 不一定阻止。

当前修复：

- Runtime 加入通用 task-scope / minimal-sufficient-evidence / stop-when-supported 契约；
- 用户显式指定 target/source/scope 视为约束，不是建议；
- 默认同步并启用内置 Skill；
- 新增 `image-quality-diagnosis` Skill；
- 新增小型稳定 `image_quality_metrics.py`，仅在需要量化时使用，不默认扫描目录。

这仍然保持模型自主调查，不引入 ScopeX Workflow Engine。

## 5. 当前工具与 Skill 交付

### Analysis Sandbox

当前目标镜像：

```text
scopex-sandbox-analysis:step7
```

预装：

```text
numpy / scipy / pandas / cv2 / Pillow / scikit-image
matplotlib / openpyxl / PyYAML / psutil / scikit-learn
Open3D（发行版提供 ARM64 apt 包时）
```

镜像内写 `/opt/scopex/toolbox.json` 作为真实能力清单。

### Built-in Skills

```text
cow-disinfect-diagnosis
image-quality-diagnosis
```

Runtime 启动时把内置 Skill 同步到 `<workspace>/skills`，再交给 OpenClaw。

## 6. 部署交付

新增：

```text
docs/09-zero-to-one-build-and-offline-deployment.md
scripts/export_offline_bundle.sh
scripts/install_offline_bundle.sh
deploy/systemd/scopex-runtime.service
deploy/systemd/runtime.env.example
```

离线 bundle 设计包含：

- 固定 git commit 的 source archive；
- 预构建 `frontend/dist`；
- ARM64/Python 对应 wheelhouse；
- `scopex-sandbox-analysis` Docker image；
- manifest / SHA256。

Dockerfile build-time APT 主源切换到清华 TUNA；host Python 在线构建/下载 wheel 时默认使用清华 PyPI 镜像。运行期仍保持 Sandbox 无网络。

OpenClaw、vLLM、模型权重继续作为设备基础环境独立管理，不随 ScopeX 小版本 bundle 重复传输。

## 7. 当前验收 Gate

本轮合入 main 后，以下项目必须继续实测，只有有证据后才能改成 PASS：

1. `python3 -m unittest discover -s tests -v` 全量通过；
2. `frontend npm run build` 通过；
3. Spark ARM64 构建 `scopex-sandbox-analysis:step7` 成功；
4. `/opt/scopex/toolbox.json` 中必需包真实可 import；
5. FastAPI + Vue 创建/轮询/result/evidence/control 联调；
6. 单张明确图片任务正常在少量请求内结束，并且不访问用户明确排除的数据；
7. 一个真实复杂业务任务完成 Result-first 产品闭环；
8. Stop / Resume / Steering + 页面刷新/重连验收；
9. 导出一次真实 ARM64 offline bundle，并在新目录完成离线安装 smoke；
10. systemd `Restart=on-failure` 和回滚路径验证。

## 8. 当前已知缺口

- `execution` 产品区目前只有显式 `evidence_role=action_verification` 才会展示，通用业务动作 provenance 仍需后续能力边界完善；
- 前端尚无 npm lockfile；离线部署依赖预构建 `frontend/dist`，源码完全可复现构建仍需补 lockfile；
- Open3D 在 ARM64 基础发行版中不是强制可用项，以 toolbox manifest 为准；
- OpenClaw/vLLM/model 尚未纳入 ScopeX 离线 bundle；它们属于设备基础环境；
- task scratch retention/cleanup 仍简单；
- mixed gateway/sandbox 对共享 scratch 尚缺真实业务复测。

## 9. 验收原则

- 不用扩大 timeout/request budget 掩盖行为问题；
- 不通过降低任务要求提高通过率；
- 不把固定业务流程写成 Runtime Handler；
- 不把模型自然语言当原始 Evidence；
- 不把 exit code 0 当业务动作成功；
- 用户明确给出范围时，不允许为了“更全面”无边界读取其他业务数据；
- 代码合入 main 不等同于 PASS；PASS 必须来自日志、测试、真实运行结果。

## 10. 当前阶段结论

截至 2026-09-14：

> **ScopeX 已从“证明 Agent 能处理复杂任务”进入“产品结果、任务范围、Skill/工具箱和端侧部署工程化”阶段。Step 7A/7B 和相应 Runtime/部署增强已经实现；当前剩余工作是完整 Spark 回归、真实业务产品验收和离线安装 smoke，而不是再设计第二套 Agent Loop。**
