# POC02-A：已确认版本后的原生接线预检

前提：用户实际帮助输出已确认 `/home/yanlan/.openclaw/bin/openclaw` 是 **OpenClaw 2026.9.2 (3928bad)**，支持 `agent --local`、`--agent`、`--session-id`、`--thinking`、`--timeout`、`config file/validate`。入口定位到此结束，不需要重复准备输入、不重装、不改变现有 systemd Gateway。

**本步骤运行真实 OpenClaw 的独立 embedded 实例，但模型端是仅在本次进程内存活的本机模拟接收器。它没有到 vLLM 的转发代码。不是正式日志分析，不计入完成率或性能成绩。**

## 1. 执行命令

在 Spark 仓库根目录，普通用户执行。已有代码改动先审阅，不 reset/clean。

```bash
git status --short
git pull --ff-only
python3 -m unittest discover -s tests -p 'test_poc02_preflight.py' -v

python3 scripts/poc02_preflight.py \
  --prepared .local/poc02/prepare-20260911T055921Z-042d152b \
  --openclaw-bin /home/yanlan/.openclaw/bin/openclaw
```

这里的 prepared 是已经校验成功的准备目录。其他机器改成自己的已存在目录。脚本从它的 reference.json 复用 served model ID、采样参数和日志哈希。**不读取默认 OpenClaw 配置，不要求提供原配置或密钥。**

这次可能创建一个新的轻量工具沙箱：复用本机已有 `openclaw-sandbox:bookworm-slim` 镜像，并先取得不可变 image ID；不拉镜像、不升级、不启动第二个模型。上限为 1 个 CPU、512 MiB 内存、256 个进程，网络 none，无 GPU，无额外设备。原 Gateway、原沙箱、原 vLLM 都不重启。

## 2. 自动做什么

| 顺序 | 真实动作 | 通过条件 |
|---|---|---|
| 输入 | 校验待挂载目录只含 input.log，哈希仍匹配 | 不夹带 expected、历史回答或代码 |
| 配置 | 创建独立 HOME/state/config；确认版本及 `config file`；运行该安装版本的 `config validate` | 不用旧实例配置，校验失败即停，不运行 doctor 或自动迁移 |
| 请求 | 真实 `agent --local` 向临时回环地址发请求 | model、温度、输出预算、thinking kwargs 与基线匹配；保留额外字段供复核 |
| 原生工具 | 模拟接收器固定返回一次 `exec` 调用，仅 `printf` 一行测试标记 | OpenClaw 自己执行其原生 exec 并把标记回传；本脚本不代替 Agent 调用业务工具 |
| 结束 | 模拟接收器返回 `SCOPEX_WIRE_CAPTURE_ONLY_NOT_A_MODEL_RESULT` | 正常结束模拟接线；预计恰好两次模型接口请求 |
| 沙箱 | 检查本轮新建容器的身份、挂载、网络、用户、capabilities；操作者在该新沙箱做只读文件可达性及 SHA256 检查 | `/agent` 是 input.log 所在目录的只读挂载，`/workspace` 是独立临时工作区；无仓库、答案、家目录和 Docker socket 挂载 |
| 收尾 | 关闭临时接收器；停止已经核实属于本轮的新容器 | 不 prune、不删除、不停止任何原容器，保留证据 |

`printf` 的命令与模拟回答明确由测试程序预设。因此它只能证明原生工具接线，不是模型选择了正确工具，更不能算自主完成任务。

若在发现新容器之前失败，本脚本不会盲目搜索并清理，可能留下新沙箱。摘要中的 `sandbox_prefix` 用于识别本轮；不要对 `openclaw-sbx-*` 或所有 Docker 容器做批量停止。已经核实的新容器会尝试停止，停止失败明确记为预检失败。

## 3. 独立配置的取舍

按同一源码提交 3928bad 核对了 `agents.entries`、defaults、sandbox、tools.exec 与模型 compat 的结构，不使用旧版 `agents.list` 示例。最终仍以 Spark 上这个实际安装的 `config validate` 和出站请求为准。

仅暴露 OpenClaw 原生 **read / exec / process**，禁止提权、Gateway/node 工具、浏览器、消息发送、子 Agent、云端 fallback；不加载用户业务 Skill/记忆。exec 只能走 sandbox；`mode=full` 仅用于本测试沙箱内执行，而不是给生产主机免确认执行权限。没有固定任何业务读取/筛选步骤。

请求使用 `compat.thinkingFormat=qwen-chat-template` 和 CLI `--thinking off`；同时通过该版本模型 params 的 `extra_body` 显式指定基线请求参数。模拟接收器**只检查收到的原始字段，不会替运行时注入/修正 false**。如果映射不生效或 extra_body 没有被消费，匹配检查会失败。任何额外 `preserve_thinking` 字段都保留在摘要，不能声称请求完全没有其他差异。

本轮是有记录的“最小原生工具配置”，不是复刻旧 Gateway 的全套默认提示词、工具与记忆，也不是跨框架同条件排名。系统提示长度和暴露工具与 POC01 不同，完整模型比较另计。

**路径变化：`workspaceAccess=ro` 时，原始输入位于容器 `/agent/input.log`，不是 `/workspace/input.log`。** `/workspace` 是可写的独立暂存区；原准备目录的 task.txt 不覆盖，此步并不执行它。后续正式 A 必须使用正确路径，不直接粘贴旧任务文件。

contextWindow=32768 是本轮沿用此前 served 模型长度设置的本地元数据；预检不查询 vLLM，也没有重新核实服务当前上限。正式任务前应再次确认，不能从预检推导长上下文能力。

## 4. 输出与判断

```text
<prepared>/native-preflight-<唯一编号>/
  summary.md / result.json
  openclaw.json            # 本次独立配置，只有临时接收器的随机鉴权，不含生产 API key
  wire-01-request.json    # OpenClaw 真正发出的 HTTP JSON body，未改写；不保存鉴权 header
  wire-02-request.json    # 原生工具结果回传后的下一次请求
  config-*.stdout.txt / config-*.stderr.txt
  wire-turn.stdout.txt / wire-turn.stderr.txt
  sandbox-*.stdout.txt / sandbox-*.stderr.txt
  openclaw.log

~/scopex-poc02-work/native-<唯一编号>/
  home/ state/ sandboxes/
```

所有原始请求、配置、容器详细元数据及 CLI 日志只留本机，不提交公开仓库。不打印模型推理文本、生产日志正文或原鉴权。本机模拟接收器没有上游连接；摘要中的 model_inference_calls=0 指本次接线测试不做真实推理，不代表整台 Spark 的既有服务没有其他并发模型请求。

成功：**PREFLIGHT_PASS_NOT_MODEL_EVAL**。意味着独立配置、实际出站字段、一次原生 exec 标记回传、容器结构与文件可达性检查通过。仅配置校验通过不够；没有沙箱、额外工具、缺少 thinking=false、参数不匹配，都会失败。

失败：**PREFLIGHT_FAILED**，查看 stage、errors、wire.problems、sandbox.problems。保留失败，不删字段重试、不关闭沙箱绕过问题。若 config 失败，先看本机的 config-validate.stderr.txt；发送脱敏错误行即可，不必发送配置全文。

退出码 0 只表示预检全部通过；1 表示已落盘的失败；2 表示入口/准备材料错误。成功也不代表完整离线产品、所有工具安全边界、视觉、自由调查或打断继续已通过。

**注意：结束后模拟接收器已关闭。生成的 openclaw.json 不能直接拿来运行正式任务，其 baseUrl 不是实际 vLLM。** 下一步按预检结果生成受记录的真实模型任务配置，先执行同片段 A 一次；不在这一步自动开始长时间模型任务。

## 5. 依据与实际自测

2026-09-11 已读取并核对同一提交：

- https://github.com/openclaw/openclaw/blob/3928bad/package.json
- https://github.com/openclaw/openclaw/blob/3928bad/src/config/zod-schema.agents.ts
- https://github.com/openclaw/openclaw/blob/3928bad/src/config/zod-schema.agent-defaults.ts
- https://github.com/openclaw/openclaw/blob/3928bad/src/config/zod-schema.agent-runtime.ts
- https://github.com/openclaw/openclaw/blob/3928bad/src/config/types.models.ts

当前官方使用说明（与安装版本区别对待）：

- https://docs.openclaw.ai/providers/vllm
- https://docs.openclaw.ai/gateway/config-agents/sandbox
- https://docs.openclaw.ai/tools/exec
- https://docs.openclaw.ai/cli/agent

本次本地 Linux / Python 3.13.5 实际执行 **33 项测试全部通过**，另完成 Python 3.10 语法解析检查。覆盖输入保护、独立配置、kwargs 传递检查、工具清单、沙箱挂载与网络拒绝、标准/流式 HTTP 模拟交换、参数不符拒绝、敏感环境不继承、子进程超时，以及模拟 CLI/Docker 的主流程。

HTTP 测试确实使用本地回环接收器；OpenClaw 与 Docker 输出使用模拟，工具结果在测试里也是模拟注入，没有在此环境实际启动 OpenClaw 或 Docker，更没有运行用户 Spark 的模型。没有把 Python 语法检查称为实际 Python 3.10 测试。本次不修改任何 POC01 评分或历史结果。
