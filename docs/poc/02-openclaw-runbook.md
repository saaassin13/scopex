# POC02-A：现有 OpenClaw 的同任务受控回归

状态：**本提交交付第 0 步的可执行准备工具，以及后续启动门与验收规则。未交付通用一键启动器，也未在 Spark 运行 OpenClaw 正式测试。**

不重装 OpenClaw，不升级模型，不更换 vLLM，不修改默认 Gateway 的配置或已有会话。先核实实际安装版本，再为测试实例生成相匹配的独立配置。不是再次推荐一个框架，也不是从零编写 Agent Loop。

## 1. 继承的基线与待解决项

根据用户在 2026-09-11 提供的 Spark 运行报告，真实日志窗口的 direct/tool 各累计五次严格正确，五次都在 120 秒内；五次中位耗时分别约 68.79 / 62.32 秒。这是用户报告的设备实测，不是本提交作者重新在 Spark 测得。数据来自同一个窗口，不代表五种不同业务任务。

仍保留 POC01 的严格 JSON 格式失败、其他用例重复次数不足及部分重复调用。原始任务与完整输入没有全部对齐，不能宣称最初 OpenClaw 失败已修复。此处是受控探索的准备，不豁免 [原阶段门](../03-poc-roadmap.md)。

POC02 分开执行：

| 子项 | 固定什么 | 只新增什么 |
|---|---|---|
| **A** | 已验证的同一片段、提取要求、标准答案、本地模型服务 | 换回现成 OpenClaw 执行，观察完整运行时行为 |
| B | A 的业务问题与预期数据 | 提供完整日志，让 Agent 自主寻找对应时间窗口 |
| C | 已有双图判断要求 | 改为 Agent 自行找图并经工具传入本地视觉模型 |

先做 A，不同时加入 B、Skill、多 Agent、定时任务或修复动作。业务权限与用户目标不变；本测试中只读日志，不要求自动维修生产设备。

## 2. 第 0 步：收集入口并准备输入（本提交已实现）

在 Spark 主机终端、scopex 仓库根目录执行，不在 Mac 本地执行，不使用 sudo：

```bash
git status --short
# 工作区有代码改动先审阅，不执行 reset/clean。
git pull --ff-only
python3 -m unittest discover -s tests -p 'test_poc02_prepare.py' -v
```

选择已完成、非预热、全部严格正确且在时限内的那批**真实日志**运行目录；不要选合成或带格式失败的记录：

```bash
python3 scripts/poc02_prepare.py \
  --baseline-run runs/<已经通过的真实日志运行目录> \
  --suite .local/poc01/real-cowlog-window
```

`<...>` 必须替换为已有的目录名。脚本不会自动猜“最后一批”，也不会覆盖任何已有记录。

该命令会：

1. 从指定运行目录的 `config.json` 读取真正测试过的模型 ID、回环服务地址和请求设置；要求 `enable_thinking` 是布尔值 `false`。不用现在可能已修改的配置覆盖旧基线。
2. 对比保存的 cases 与当前 suite，验证 `input.log` 与每次成功记录里的 SHA256。发现任务、数据、真值变化就停止，不静默更新。
3. 在 `~/scopex-poc02-work/input-<唯一编号>/` **只复制 input.log**，原文字节不变。不会复制 expected、历史回答、结果记录、证据文件、代码或 Skill。
4. 检测主机 `openclaw` 可执行入口，用独立 HOME/STATE_DIR 和精简环境读取 `--version`、`--help`、`agent/config/sandbox --help`。不运行 agent，不执行 config set、doctor --fix、onboard 或 gateway start。
5. Docker 存在且当前上下文指向本机 Unix socket 时，只列容器/镜像名称并筛选含 openclaw/vllm 的候选。不读取容器环境变量与完整启动命令，不 docker exec，不拉镜像、不创建容器，不访问远程 Docker daemon。

所有帮助命令都有等待上限，无 Shell 拼接。第三方启动包装器可能不遵守 OpenClaw 的 HOME/STATE_DIR 环境变量约定，因此未知包装器的帮助结果仍须人工确认。脚本自身不调用模型/HTTP API，不读取默认 OpenClaw 生产配置；不以“没有主动下载”替代网络隔离证明。

主机 CLI 不在 PATH 时不是失败结论：它可能部署在 Docker、源码目录或别的账号下。脚本仍准备输入并报告 UNKNOWN。名称筛选也可能漏掉自定义容器名。不要因此重新安装；根据实际报告选择后续入口。已有明确的主机 CLI 路径可通过 `--openclaw-bin /实际路径/openclaw` 指定，不接受一整段 Shell 命令。

### 输出位置

```text
scopex/.local/poc02/prepare-<唯一编号>/
  summary.md        # 可先发回的准备摘要；不是模型成绩
  reference.json    # 基线设置和材料哈希；没有 API key、备注或 expected 原文
  inventory.json    # 本地帮助全文等，留本机审阅，不整份公开
  task.txt          # 原提取规则 + /workspace/input.log；尚不可在默认会话执行
  help-home/        # 帮助命令临时环境；不与生产 OpenClaw state 混用

~/scopex-poc02-work/input-<唯一编号>/
  input.log         # 唯一待挂载文件；日志只读标志不是完整权限隔离
```

成功显示 **PREPARED_NOT_RUN**。退出码 0 仅表示准备成功；2 表示材料或入口检查错误；130 表示人为中断。缺少主机 CLI、Docker 不可用等会明确记录，不能解释成启动门通过。

**本次只需发送 summary.md。不要把整个 `.local/`、inventory、生产配置或日志上传到公开仓库。** 日志已有副本无需重新上传；不需要手工制作新的标准答案。基线读取失败时发送错误类型，保留原材料。

## 3. 第 1 步启动门：独立配置与真实隔离（尚待本机环境确认）

下一步依据第 0 步的版本与部署方式，使用同一个已安装版本创建独立测试实例。下面是配置约束，不是可以直接贴进任何版本的通用配置：

| 检查 | 必须成立 |
|---|---|
| 配置与状态 | 单独 profile 或显式独立 config/state；不继承原会话、记忆、认证和云端 fallback |
| 模型 | 使用 reference.json 中实际 served ID；只连同机服务；不改变权重或推理后端 |
| 工具 | 优先原生文件读取、执行和临时脚本能力；记录暴露的工具列表。不固定为 read→answer，也不给筛选答案脚本 |
| 文件隔离 | 用实际沙箱/独立受限进程边界，只把准备目录只读挂入；评分与历史结果必须不可访问 |
| 副作用 | 不挂载生产目录、家目录、scopex 仓库或 Docker socket 给模型工具；不允许提权/节点执行绕过沙箱 |
| 扩展 | 不自动加载用户既有 Skill、MCP、消息通道、定时任务、历史记忆；版本自带系统提示等差异如实记录 |
| 网络 | 工具沙箱不需要网络；模型请求由受控服务端通道到本机推理服务。不是禁网了就连模型也无法访问 |

**workspace 是默认工作目录，不是硬沙箱；换目录、chmod 0444、写“不许看答案”都不能证明隔离。** 同一 Unix 用户在沙箱外仍能读取其他目录。本次暂存完成后仍标记 `isolation_verified=false`。

主机 Gateway 和 Docker 内 Gateway 的挂载路径、回环地址含义不同。不能机械照搬 `127.0.0.1` 或 `host.docker.internal`；先确认是在哪个网络/文件系统命名空间里发请求。没有合适的本机沙箱镜像时停止，不自动在线安装，更不能退回无限主机权限。

正式任务前用操作者的无模型检查验证：只看到允许的 input.log；能读它；不能访问主机 scopex 的 runs、expected、原生产目录；工具执行无法提权离开沙箱。记录实际挂载与权限结果。额外的失败边界测试使用合成文件，不能把秘密文件内容作为检查输出。

## 4. 第 2 步启动门：核对实际发给模型的请求

当前官方文档说明 Qwen 可用 `compat.thinkingFormat: "qwen-chat-template"` 映射 `/think off`，但**已安装版本是否支持需核对**。`reasoning:false` 的模型能力声明、UI 隐藏推理、解析器设置都不能代替实际 `enable_thinking:false`。

先用短通路任务检查实际出站请求，暂不使用生产片段。可以利用该安装版本确实提供的请求追踪，或单独的本机透明记录点；记录点不能偷偷注入 false、改工具或修答案，否则测到的是被改写的请求，不是运行时是否正确传参。

必须记录：实际模型 ID、接口、stream、temperature、输出预算、chat_template_kwargs、工具清单数量、系统提示长度及是否有隐藏的 fallback/辅助请求。与 POC01 reference 对照；不支持某参数就记差异并停下决定，不静默接受。

当前文档的映射示例还包含 `preserve_thinking:true`。这与 POC01 只设置 enable_thinking 的基线可能不同，必须记录，不声称所有请求字段完全相同。适配层传参、服务端模板消费和返回推理字段需结合判断，字段为空不单独证明模式生效。

记录采样请求须先遮蔽鉴权；真实日志进入请求后，原始 trace 仅在本地保存。尚未交付/执行请求记录程序时，状态保持 `wire_request_verified=false`，不得把预备配置当证据。

## 5. 第 3 步：A 的单次执行与外部验收

只有第 1、2 步通过后才执行。使用独立新会话，给 task.txt 的同一任务和片段路径，不提供 expected。无需将完整日志直接放进 prompt，Agent 自主选取文件工具或临时脚本。

保留原纯 JSON 约定。最终回答在操作者侧评分，按内容、格式、实际成功读取分开检查；CLI 的 JSON 外层封装不是模型答案，不可把整个封装与 expected 比较。适配器不能从中挑选较早的“正确答案”或补全漏项。

记录提交到最终交付的墙钟时间、模型轮数、工具名/参数/结果摘要、首次可见进度、全部自动重试与中断。接口或 CLI 返回 0 不等于任务成功；缺实际读取证据不能因猜中答案而通过。

目标 120 秒，实验止损预算 180 秒。OpenClaw 的 CLI/Gateway 默认时限可能不同，需在所用版本明确设置。客户端退出或 Ctrl-C 不等于模型服务或外部命令已停止；不确定取消结果时核对进程后再跑，不自动重复提交。

第一轮只做 A 一次；失败就取证，不堆调用上限。通过后才做少量重复。暂不同时测完整文件 B、视觉 C 或交互验收。完整 Web/TUI 的“插话、打断、继续”仍归 POC04，A 的单次命令不替代它。

## 6. 结果决策

| A 的结果 | 下一步 |
|---|---|
| 严格正确、证据完整、时限内 | 保留 OpenClaw 为候选；重复后进入完整文件 B，不宣告整产品通过 |
| 内容正确但格式违约 | 保留原失败；单独输出约定修复与回归，不用高强度 thinking 掩盖 |
| 同片段显著慢、反复动作或不收尾 | 与 POC01 对比分轮输入/输出和工具链；此时才考虑有明确假设的第二运行时对照 |
| 请求参数与基线不同 | 先修接入；此轮不能称为同条件运行时比较 |
| 标准答案可访问或生产权限未隔离 | 本轮无效/停止，不继续跑模型任务 |

## 7. 官方依据与验证边界

以下为 2026-09-11 查询的官方文档，描述当前文档而不是保证已安装版本具有同名字段。实际部署以本机帮助、配置校验和出站请求为准。

- [CLI：profile、全局参数](https://docs.openclaw.ai/cli)
- [Agent：本地/Gateway 入口与会话](https://docs.openclaw.ai/cli/agent)
- [vLLM：Qwen thinking 参数映射](https://docs.openclaw.ai/providers/vllm)
- [Workspace：不是硬沙箱](https://docs.openclaw.ai/concepts/agent-workspace)
- [Docker 沙箱默认边界](https://docs.openclaw.ai/gateway/sandboxing/docker-backend)
- [沙箱配置与挂载](https://docs.openclaw.ai/gateway/config-agents/sandbox)

本提交的脚本测试使用本地合成 manifest、假安装信息与子进程，不需要模型或 Docker daemon；不证明 Spark 的 CLI、沙箱或真实请求检查通过。精确测试数量与命令见 [准备工具校验](02-prepare-validation.md)。
