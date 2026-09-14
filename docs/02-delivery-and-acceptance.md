# 交付、验收与当前状态

状态：**2026-09-14 当前有效版本**。严格区分：

- **PASS**：已有真实实施证据；
- **已实现**：代码已落地，但仍需当前版本回归/真实环境验证；
- **待完成**：尚未完成。

## 1. 当前产品形态

```text
Unified Manual Run / Scheduled Trigger
        ↓
FastAPI + TaskService
        ↓
OpenClaw + qwen3.8-27b-nvfp4
        ↓
Data Catalog + Business Skills + Tools
        ↓
Trace / Working Data / Claim-grade Evidence
        ↓
conversation answer
或
Fresh Finalizer -> Claims -> Product Answer
        ↓
Vue Result-first UI + User Facts
```

用户不再选择 Chat / Task；手动统一 `POST /runs`，后台依据实际是否触碰业务数据内部落成 conversation 或 audited task。Scheduler 只做时间 Trigger。

## 2. 已验证冻结基线

| 能力 | 状态 |
|---|---|
| Runtime MVP | **PASS** |
| Stop / Resume / Steering | **PASS** |
| Evidence-Calibrated Output | **PASS** |
| 6A Context / Compaction | **PASS** |
| 6B Large Data / Multi-Image | **PASS** |
| 6C Hard Budget | **PASS** |
| 6D Native Loop Convergence | **PASS** |
| 6E Complex Task Capability | **CAPABILITY PASS** |
| 6F `600s / 16 requests` Product Gate | **PASS** |

Step 6A–6F 继续冻结。

## 3. Product V1 — 已实现 / 待本轮回归

当前代码已包含：

- 统一 `/runs` 手动入口；
- auto → conversation/task 内部结果策略，无额外 Router Model；
- 正式业务调查失败不得降级为聊天答案；
- Product Answer deterministic readability；
- started/finished/duration/trigger/schedule metadata；
- interval/daily/once schedule；
- 设备离线历史 schedule trigger **MISSED_OFFLINE，不补跑**；
- run-now 不移动 recurring cadence；
- 月历 → 当天 Run 列表；
- terminal Run 安全级联删除 ScopeX 数据，不删除业务源；
- 👍/👎 + tags + note；
- bounded review ZIP；
- Run 详情 Result / User Facts / 技术记录分层。

当前仍为单主要执行槽位；并发尚未打开。

## 4. Data Catalog / 大目录保护 — 已实现 / 待 Spark 验收

统一配置：

```text
config/data-catalog.json
```

当前真实路径：

```text
/opt/ScalingRobotics/CowDisinfect/Log
  -> /agent-data/logs

/opt/ScalingRobotics/CowDisinfect/GrabbedImages/LeftCamera
  -> /agent-data/left-camera
```

LeftCamera：`YYYYMMDD/HH/YYYYMMDD-HHMMSSmmm.jpg|json|pcd`。

CowDisinfect 日志：`CowDisinfect-YYYYMMDD-HHMMSS.log[.N]`；文件组起始时间可能不是自然整点，因此 locator 用“当前组开始 → 下一组开始”的区间和请求时间窗做重叠选择。

当前实现：

- Runtime 自动复制 Catalog 到 workspace；
- host path 存在时默认只读 bind；
- 内部 `data-locator` Skill；
- 普通任务不递归 `find / grep -R / du -a / rg --files` 数据根；
- LeftCamera 直接访问目标小时目录；
- PCD 有独立小文件数预算；
- analysis sandbox 仍有 memory/CPU/PID/exec-time hard limits。

详见 `docs/business/02-data-catalog-and-bounded-access.md`。

## 5. Evidence 分层 — 已实现 / 待真实任务验收

真实编码器复盘暴露原实现把 Skill、脚本源码和大 JSON stdout 都投影成 Evidence，曾产生约 990 条 Evidence。

当前修正：

```text
Trace
  Skill / script / locator / commands / model requests

Working Data
  /task-scratch

Claim-grade Evidence
  raw business lines / original images / host facts / structured business_facts

User Facts
  UI-visible factual basis
```

固定过滤：

- `/workspace/skills/**` read：Trace-only；
- workspace Data Catalog：Trace-only；
- `/task-scratch/**` read：Trace-only；
- `scopex_role=locator`：Trace-only；
- `scopex_role=business_facts`：整体成为 1 条结构化 Evidence。

Run 页面不再把 Skill.md/source code 作为“相关证据”展示给用户。

## 6. Business V1

默认 Skills：

```text
data-locator
system-health
image-quality-diagnosis
nipple-recognition-analysis
encoder-health
log-context
```

### system-health

每 Run 当前 host snapshot，只看当前 CPU/load/memory/disk/GPU/Docker/process；无资源历史，无 Sandbox fallback。

### image-quality

单图原图优先；时间窗先 locator 到目标 `YYYYMMDD/HH`；最终 claim-grade 原图集合有界。

### nipple-recognition-analysis

固定业务口径：一头牛 4 个；只统计最终采用帧的 2D `NippleNum`；3D 不参与；JPG/JSON 失败路径可能不存在，因此不做牛数分母。

产品路径：locator → 显式日志文件 → `nipple_stats.py` 一次统计 → compact `business_facts`；逐牛明细写 `/task-scratch`。

### encoder-health

真实失败复盘后已调整：

- locator 先选择请求时间窗对应所有轮转日志；
- `encoder_health.py` 一次处理显式文件列表并跨文件计算连续性；
- 所有小 raw decrease 只做次数/P95/max 分布，不逐条变成事件；
- 显著 negative / large negative / positive outlier / gap / flat 才进入 bounded candidates；
- compact `business_facts` stdout；完整 candidates 可写 `/task-scratch`。

### log-context

只在需要解释重要候选时读取显式日志小窗口；不独立判根因。

详细见 `docs/business/01-business-capabilities-v1.md`。

## 7. Scheduler V1

支持 interval / daily / once。

离线/断电重启：

```text
历史 trigger -> MISSED_OFFLINE
missed_count += N
last_missed_at = ...
next_run_at = 下一个未来时间
```

不创建历史补跑 Task。once 过期后停用。

在线 busy 当前仍 `SKIPPED_BUSY`，不排队；并发/queue/coalesce 是后续阶段。

## 8. 执行记录、删除、评价

- 首页单一 Agent 输入；
- 月历按设备本地日期聚合 ScopeX Run；
- 点击日期看当天 Run；
- terminal Run 可删除 audit/work/scratch/host/runtime/review export；
- 删除 API 永不触及 `/agent-data` 原始数据；
- terminal Run 可评价、导出 review ZIP。

## 9. Analysis Sandbox / 部署

目标镜像 `scopex-sandbox-analysis:step7`；运行期 network=none。默认 sandbox hard limits 仍为 512 MiB memory/swap、1 CPU、256 PIDs、exec 30s。

Device Base / ScopeX Update 双层离线方案不变。当前模型真实 `MODEL_REPO + MODEL_REVISION` 仍需补录。

## 10. 当前验收 Gate

只有有实施证据后才改 PASS：

1. Data Catalog / locator / Evidence / unified run / calendar/delete 专项 Python tests；
2. Python 全量 tests；
3. Vue `npm run build`；
4. ARM64 sandbox/toolbox；
5. 两个真实 Data Catalog host path 自动 mount；
6. 普通咨询通过统一入口落成 conversation；
7. 业务请求一旦触碰业务数据必须落成 audited task；
8. 3点 encoder 真实任务使用 locator + 单次 compact analysis 完成；
9. encoder facts 与人工数据一致，Evidence 数显著下降；
10. 一小时 nipple 2D KPI 人工复算；
11. image single-scope regression；
12. Facts 页面无 Skill.md/script/locator/scratch；
13. Scheduler 重启历史 trigger 不补跑；
14. calendar/day list/delete；
15. timing / feedback / review ZIP；
16. Stop/Resume/Steering + refresh/reconnect；
17. Device Base + ScopeX Update 离线 smoke/rollback；
18. 上述稳定后再进入 bounded concurrency。

## 11. 当前已知缺口

- 本轮新代码尚未跑完整 Python/Vue 回归；
- current host snapshot 需 Spark 字段实测；
- 日志根目录 locator 当前做**非递归文件名扫描**；若未来增长到几十万文件，再增加轻量日志索引，不提前上数据库；
- PCD 真实 workload 尚未验证 512 MiB sandbox 是否足够；
- 完全漏检且无命名牛周期的奶牛缺独立 ground truth；
- encoder firmware/site 阈值 profile 未冻结；
- 网络 topology 未确认；
- current model repo/revision 未补录；
- frontend npm lockfile 缺失；
- bounded concurrency 尚未实现。

## 12. 验收原则

- 不扩大 budget 掩盖行为问题；
- 不通过全盘扫描换“看起来更全面”；
- 不在 Runtime Handler 写固定业务 Workflow；
- 不把旧经验阈值当协议事实；
- 不把 Skill/source/scratch 当业务 Evidence；
- 不把 3D nipple 混入 2D KPI；
- 不把 Sandbox 状态冒充 host；
- 用户范围优先，够证据即停；
- 代码已实现 ≠ PASS。
