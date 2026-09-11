# POC02-A：第一次真实模型任务

前提：已安装 OpenClaw **2026.9.2 (3928bad)**，完成原生预检且报告为 `PREFLIGHT_PASS_NOT_MODEL_EVAL`。不再跑定位器、不重新制作输入、不重装、不改现有 Gateway 或 vLLM。

**这里运行真实 OpenClaw + 真实本地模型，一次日志任务。不是模拟回答，不是让测量脚本扮演 Agent。** OpenClaw 自己选择原生 read/exec/process，测量脚本只负责独立启动、记录、预算、边界核对和外部评分。

## 1. 先纠正一个现场事实

用户 2026-09-11 的通过报告显示 `/agent` 和 `/workspace` 的挂载 `RW` 都为 **false**。此前预检手册把 `/workspace` 描述成可写临时目录过于绝对，以实际挂载为准。预检并没有验收 workspace 可写。

本步骤保持已通过配置不变：输入是 `/agent/input.log`；不为了测试将它或 `/workspace` 改成可写。继承原容器 `/tmp` 等 tmpfs 配置，临时脚本可使用 `/tmp` 或直接内联执行；该能力不等于已经验收所有写入方式。临时文件不会自动成为正式产物。

## 2. Spark 上的命令

仓库根目录、普通用户，不使用 sudo。先审阅已有改动，不 reset/clean：

```bash
cd /home/yanlan/workspaces/code/scopex
git status --short
git pull --ff-only
python3 -m unittest discover -s tests -p 'test_poc02_run.py' -v
```

预期 `Ran 32 tests ... OK`。这些是测量程序的离线测试，不会启动 OpenClaw、Docker 或 GPU 模型。

然后只运行一次（已经填入本次通过的预检目录）：

```bash
python3 scripts/poc02_run.py \
  --preflight .local/poc02/prepare-20260911T055921Z-042d152b/native-preflight-20260911T073155Z-21eea8ea \
  --openclaw-bin /home/yanlan/.openclaw/bin/openclaw
```

不是传最外层 prepared 目录，也不是传失败的 preflight 目录。其他设备需替换为自己的通过目录。不要直接复用旧 `openclaw.json`：旧配置指向已经关闭的模拟接口。

模型服务需要鉴权时，使用原 POC01 保存配置中的 `api_key_env`，默认 `SCOPEX_API_KEY`。密钥在当前终端环境中提供，不写入命令参数、Git 仓库或 Agent 配置。原 POC01 不需要鉴权时无需额外设置。脚本先 GET `/v1/models`；认证错误或 served ID 不存在即停止，不进入推理。

## 3. 自动执行的步骤

| 步骤 | 行为和证据 |
|---|---|
| 固定材料 | 读取通过的预检结果及原 reference，核对保存配置、提示、真值哈希和输入字节，不重新生成答案 |
| 独立实例 | 克隆已经验证的配置，只改本次状态/审计路径、随机会话/Agent 标识和模型连接入口；行为配置必须与原构建器一致 |
| 本地模型核实 | 用原回环地址读取 `/v1/models`，核对真实 ID；服务公开 max_model_len 时检查不小于声明长度，未公开则保留未知 |
| 原生沙箱 | 在第一次真实模型请求前检查本次原生容器的镜像、挂载、网络、用户、capabilities，并读取挂载输入的哈希；失败就不向模型转发 |
| 真实任务 | 给原业务 prompt 加上 `/agent/input.log` 路径，不给标准答案、筛选脚本或固定步骤；调用一次 `agent --local` |
| 请求记录 | 临时回环记录器将 HTTP JSON body **逐字节原样**转发到原模型服务，实时转发响应；不修参数、不修 ID、不造工具调用或成功回答 |
| 外部评分 | 从 CLI JSON 的最后一条可见非推理 payload 取最终答案，不从更早的输出挑正确答案；纯 JSON、内容、证据、完成状态分开判断 |
| 收尾 | 关闭记录器，只停止核实属于本轮的新沙箱；不删除原记录，不重启既有服务，不批量清理容器 |

模型 API key 由记录器用于上游鉴权；Agent 只拿到本轮记录器的临时随机凭据。请求鉴权 header 不保存，HTTP body 和模型原始回复仅保存在本机审计目录。记录器只允许原回环 `/v1` 上游，不遵循重定向或系统 HTTP 代理，没有云端 fallback 或自动网络搜索。

这不是整机防火墙或完整离线产品验收：原系统服务不在本脚本控制范围内。正式运行仍需选择不影响生产业务的时间。

## 4. 预算与统计口径

沿用原 reference 的 `sla_s` / `timeout_s`，当前为 **120 / 180 秒**。只执行一个业务任务，无自动批次或“失败再来一次”。记录器最多向上游接受本任务 6 轮模型请求；该上限是止损，不是完成保证。OpenClaw 内部发生的重试占同一时间预算和请求记录。

`wall_s` 从启动本次 native agent 命令到退出计时，包含其启动、模型、工具及首次沙箱检查。命令执行前的版本/配置校验、GET models、收尾停止容器不在 task wall_s 中；不是已经常驻服务下的最终交互延迟。代理 per-request 的 start/end 含记录和边界检查，不等于纯解码速度。usage 仅在真实响应提供时统计，缺失保持 null。

出现超时或 Ctrl-C 时中断本次客户端进程组并关闭转发连接，停止已确认的新沙箱；**不能仅凭连接关闭就证明 vLLM 已完成请求取消**，报告保留 `unconfirmed_do_not_autoretry`。未查清后端状态前不要立即重跑。

即使答案看起来正确，只要命令超时、未完整结束、fallback 或边界未确认，都不计成功。格式错误不改成成功；预检中的 ID 兼容修复不参与真实模型响应改写。

## 5. 输出与证据复核

终端会提示 `[run] model request 1/2/...`，最后直接打印摘要。不要用只处理 POC01 格式的 `inspect_run.py` 读取它。

```text
<prepared>/real-a-<唯一编号>/
  summary.md / result.json    # 贴回的摘要
  task.txt / answer.txt       # 实际任务和最后可见答案，留本机
  openclaw.json               # 独立配置，留本机
  wire-01-request.json        # 未改写的请求体
  wire-01-response.bin        # 原始 SSE/JSON 内容
  wire-01-meta.json           # 请求 hash、状态和时间
  tool-trace.json             # 从工具往返提取的名称、参数、结果关联，留本机
  agent.stdout.txt / agent.stderr.txt
  real-*.stdout.txt / real-*.stderr.txt

~/scopex-poc02-work/real-<唯一编号>/
  home/ state/ sandboxes/     # 原生会话与状态，不挂载给工具
```

工具证据采用保守规则：只有原生 `read` 请求指向 `/agent/input.log`、相同调用 ID 的工具返回覆盖完整原文时，自动标记 `verified_full_read`。这是运行时记录证据，不是系统调用级别审计。

**Agent 自选 exec/Python 筛选、分页读取或结构化工具结果不被禁止，但先标记 `review_required`**，保存轨迹供操作者核对。不能因为脚本没识别就断言模型失败，也不能仅看到命令里有文件路径就自动宣布读取成功。本阶段不自动放宽该证据口径。

| 状态 | 含义 |
|---|---|
| `PASS_SINGLE_CASE` | 本次严格内容+格式、完整读取证据、请求和沙箱检查成立，120 秒内交付 |
| `CORRECT_OVER_SLA` | 上述正确性成立但超过目标时间，时间指标失败 |
| `FORMAT_ONLY` | 本次内容与证据成立，但有代码块包装，原严格验收不通过 |
| `EVIDENCE_REVIEW_REQUIRED` | 任务结束且环境检查成立，工具证据不能自动确认；结合 grade 单独核对，不算已成功 |
| `NOT_COMPLETED` / `NOT_PASSED` | 未完整结束或存在其他验收失败，保留原始轨迹 |
| `SETUP_FAILED` / `RUN_SETUP_ERROR` | 准备或配置错误，查看 model_request_attempts 判定是否实际转发过推理请求 |

`model_request_attempts` 是转发端尝试提交的请求数，不保证后端实际启动了同等数量的推理。`synthetic_response=false` 表示正式模式；status 非 PASS 不需要重新安装或再次定位 CLI。

先贴 summary.md。若证据待复核，再查看本机 tool-trace 和对应工具回传，发送脱敏片段，不上传完整生产日志或密钥。此轮是最小原生工具配置，不是旧 Gateway 的全套工具/记忆回归，也未验收 Web/TUI 打断继续、完整日志搜索或 Skill。

## 6. 实际测试与依据

2026-09-11，本地 Linux 容器 Python 3.13.5 执行 `python3 -m unittest discover -s tests -p 'test_poc02_run.py' -v`：**32 项通过**。另通过 Python 3.10 语法解析检查，不声称在 Python 3.10 实机运行。

覆盖：回环 URL/鉴权限制、配置仅改变运行路径、原配置不变、JSON 严格和格式诊断、最后可见 payload、错误/未结束/fallback 拒绝、工具 ID 关联、exec/分页证据保留复核、历史消息去重、原样 HTTP body 与 SSE/JSON 转发、失败门不访问上游、使用真实响应 usage、不跟随重定向、模型 ID 检查、假 native helper 的正反整体控制流程。

HTTP 转发测试真实启动本地临时 HTTP 服务，**上游是假模型**；整体控制测试模拟 OpenClaw 和 Docker。没有在用户 Spark 执行正式任务，也没有在本步骤重新执行其他旧测试套件。正式部署复用 `poc02_preflight.py` 的配置和边界函数，其真实表现以前一步 Spark 通过记录为依据，新组合仍需现场验证。

配置/CLI依据使用已核对的 OpenClaw `3928bad`，不是猜最新示例：
- [同版本命令结果的 payloads/meta 定义](https://github.com/openclaw/openclaw/blob/3928bad/src/agents/command/delivery.ts)
- [同版本最终可见 payload 的选择](https://github.com/openclaw/openclaw/blob/3928bad/src/agents/command/post-run.ts)
- [官方 agent 命令说明](https://docs.openclaw.ai/cli/agent)（当前文档；已安装 CLI 帮助优先）。
