# POC01：在 Spark 上逐步执行

目的：使用**当前已有模型服务**，把“相同日志直接分析”和“经过工具后分析”的正确性、耗时、停止行为分开。再验证短工具链与基础读图。此轮不换框架，不安装模型，不修改生产服务。

所有命令在 **Spark 的 Linux 主机终端**执行。Mac 只用于 SSH/VS Code 连接。Python ≥3.10，仅标准库；需要 git、已有本地 Chat Completions 兼容服务。执行仓库脚本前可以审阅源码。不要用 sudo。

## 0. 安全和阶段约束

首次测试选择非关键生产时段，观察已有业务。脚本仅能访问配置中的回环推理地址与本用例复制的数据；工具没有 Shell。即使只读，模型推理仍占 CPU/GPU/统一内存，不代表零生产影响。发现 OOM、服务不可用或业务延迟立即 Ctrl-C 停止。

仓库是公开的。真实日志、图片、原始请求响应和配置写入 `.local/`、`runs/`、`*.local.json`，均已忽略；不要强制 git add，不要上传整个运行目录。脚本记录可能包含模型响应中的敏感内容，仅在本机检查。

**默认不自动重试 HTTP，不自动重启服务，不自动联网下载。** 客户端停止不保证服务端立即释放正在推理的任务，需检查服务状态后决定下一步，不连续叠加请求。

## 1. 获取仓库和先测脚本

新目录：

```bash
git clone https://github.com/saaassin13/scopex.git
cd scopex
```

已有本仓库则在它的根目录执行：

```bash
git status --short
git pull --ff-only
```

有本地改动或 pull 冲突时先停下处理，不执行 reset/clean。之后：

```bash
python3 --version
python3 -m unittest discover -s tests -v
bash scripts/collect_env.sh
```

**预期**：测试输出 `OK`，环境快照路径打印出来。测试只用模拟 HTTP/模型响应，不需要 GPU，也不代表模型通过。环境采集失败的子项会明确标记，不自动提权或安装。主机 Python 未装 vLLM 不等于 Docker 内未装，需手工补齐镜像版本。

先打开 `docs/templates/poc01-result.md`，复制一份到 `.local/poc01/notes.md`。写下本次业务负载、旧 POC 信息和环境快照路径：

```bash
cp -n docs/templates/poc01-result.md .local/poc01/notes.md
```

## 2. 接入现有模型服务，核对精确 ID

```bash
cp -n configs/poc01.example.json configs/poc01.local.json
```

编辑本地配置：把 `base_url` 改为模型实际映射到 **Spark 主机**的端口，例如 `http://127.0.0.1:8000/v1`。这不是 Agent 的 Web/Gateway 端口，也不是 Docker 内部尚未映射的端口。IPv6 回环可用 `http://[::1]:8000/v1`。

脚本拒绝公网或局域网地址、带凭据的 URL、HTTP 重定向，并绕过环境代理。当前模型必须就在这台 Spark 上，端口问题先排查现有服务，不自动建立隧道或改防火墙。

服务有 API key 时，从终端读入环境变量；不要写进 JSON、命令参数或 Git：

```bash
read -r -s -p '本地模型 API key: ' SCOPEX_API_KEY; echo
export SCOPEX_API_KEY
```

无 key 的服务跳过这两行。

```bash
python3 scripts/poc01.py models
```

**预期**：返回 `data` 数组。复制里面一个真实 `id` 填入配置 `model`。`/models` 输出另保存在 `.local/poc01/models.json`。不要凭“千问3.8 27b”猜 ID、架构或视觉能力。

```bash
python3 scripts/poc01.py check
```

**预期**：本机地址及模型 ID 检查通过。这一步只验证发现接口，不代表工具、thinking 或图片功能已通过。

在结果模板中手工记录模型权重来源/revision、量化、服务/镜像版本、实际聊天模板、工具解析器、推理解析器、上下文长度、KV cache/显存预算、额外参数。信息拿不到就写 UNKNOWN，不要猜。

## 3. 创建固定合成样本

```bash
python3 scripts/poc01.py init
```

**预期**：创建8个用例，位于 `.local/poc01/cases.json`。数据位于 `.local/poc01/data/`。真值保存在数据目录之外，不提供给模型工具；不覆盖已存在的样本。

| 用例 | 做什么 | 独立评分依据 |
|---|---|---|
| chat | 返回一个固定 JSON 标记 | 仅用于通路，不测聊天质量 |
| direct-basic | 直接给完整日志，按时间、设备、级别提取 | 必须返回3条，含起点、不含终点，字段精确 |
| tool-basic | 相同日志、相同问题，但只给路径 | 同一真值，且必须真的 read_file |
| direct-empty / tool-empty | 筛选不存在的设备 | 必须为空数组；tool 仍须读取 |
| chain | 列出文件，读取两份日志并合并 | 必须 list_files + 两份成功读取，且结果精确 |
| vision / vision-swap | 发送两张 PNG 的真实 base64；交换顺序再测 | 分别数出圆数量；顺序变更后答案也变化 |

每张合成图 320×160，仅验证视觉通道，不代表生产相机图像质量、奶牛细节识别或深度测量。PNG 由标准库生成，不需第三方绘图库。合成日志很小，不能外推大文件性能。

查看 `cases.json` 可以核对真值。模型每次只收到 prompt、指定输入和工具定义，不收到整份 case manifest 或 expected。

## 4. 预热与首次文本冒烟

逐条运行，先看结果再继续：

```bash
python3 scripts/poc01.py run --case chat --repeat 1 --warmup --label warmup
python3 scripts/poc01.py run --case direct-basic,tool-basic --repeat 1 --label smoke-text
```

终端会显示 request started、response received、tool started/finished、task finished 和运行目录。**请求是非流式**：生成期间可能只有开始事件；这不是最终对话 UI，也不用于验收流式推理展示。

**预期**：两项 `correct=true`，工具项 `successful_reads` 包含 `input.log`。理想情况 direct 1轮、tool 2轮（读文件+回答）；这是观察预期，不是固定强迫步骤。工具数多时看每一步是否有效。

每次查看终端打印路径中的 `summary.md`，不要只看退出码。`run`：0表示本次完整计划正确且达到该配置时限（预热仅看正确）；1表示错误/超时/未完整执行；2表示入口配置或文件错误；130表示执行中用户中断。

出现失败默认停止剩余计划，不自动反复跑。先用第9节定位。第一次若明显受到加载/缓存影响，不删除结果；标注 cold/first-use，单独再次预热并用新标签复测。

## 5. 文本重复与短链路

冒烟正常后，先小批量：

```bash
python3 scripts/poc01.py run --case direct,tool --repeat 3 --label baseline-text
python3 scripts/poc01.py run --case chain --repeat 1 --label smoke-chain
```

`direct,tool` 是组名，选中 basic 和 empty 共4例；repeat3共12次，不是3次。每轮交错用例，避免先跑完所有 direct 再跑 tool；每次用新 messages，不沿用答案。服务端缓存不自动清理，必须记在环境配置中。

确认没有服务压力、循环或协议故障，才收集完整5次矩阵：

```bash
python3 scripts/poc01.py run --case direct,tool,chain --repeat 5 --keep-going --label repeat-text
```

共25次。`--keep-going` 只在评估允许后使用：即使某次失败也继续收集，**不是重试直到成功**。每次最多180秒、6个模型轮次、8次工具调用；同一调用第三次触发止损。120秒以上即不计入时限内成功，180秒停止也不算正确完成。

## 6. 独立验证真实图片输入

```bash
python3 scripts/poc01.py run --case vision --repeat 1 --warmup --label warmup-vision
python3 scripts/poc01.py run --case vision --repeat 5 --keep-going --label repeat-vision
```

组名前缀同时选中 `vision` 和 `vision-swap`，正式共10次。请求文件应含两个 `image_url`，内容为 `data:image/png;base64,...`，不是文件路径或外网 URL。

图像不支持/多图超限/解析报错：记录为视觉链路未通过。不要只测文本后宣称满足整体需求，也不要为通过基础测试立即安装另一个视觉服务。先核对精确模型能力、模板和服务的图片数量配置；任何服务改动另开受控对照。

## 7. 复现原来失败的日志（必需）

按照 [真实日志接入手册](01-real-log.md) 复制原始文件、原始任务和独立核对的 JSON 真值。先各一次，再各五次。**真实材料尚未提供时，只能说合成探针通过，不能说旧问题解决。**

原日志不可得、任务定义有歧义、真值未核对、原输出格式不同，都写明。探针加入 JSON 输出约定与只读工具，不能把它的成绩直接代替 OpenClaw/OpenCode 原完整运行时成绩。

## 8. 输出在哪里，如何判断

```text
runs/<UTC时间-标签-随机尾缀>/
  manifest.json         # 脚本/样本hash、仓库commit、Python；thinking有效状态默认UNKNOWN
  config.json           # 实际发出的采样等参数；不写API key的值
  cases.json / plan.json # 本地真值与计划；不提供给模型
  summary.md / results.json
  001-<case>/
    01-request.json / 01-response.json  # 每轮真实请求响应，后续轮次依次保存
    messages.json / events.jsonl
    result.json         # 精确评分、耗时、轮数、工具数、证据、错误
```

每类分开看 correct 和 within_sla；不要把只会返回固定标记的 chat 计入业务正确率。请求耗时含排队/输入处理/生成/传输，不是纯推理时间；usage只保留服务端返回的数据，缺失就是null，不编造token数量。

当前探针不测 TTFT、纯 decode tokens/s、完整中断恢复、业务共存。P90使用 nearest-rank；5次时接近最慢一次，不能据此保证总体分位数。

阶段门见[路线图](../03-poc-roadmap.md)：文本各5/5正确、至少4/5在120秒内，仅说明值得进入下一阶段；不是已经达到正式产品95%/90%指标。图片单列，原日志必须加入。最终运行时还没选定。

## 9. 失败对照表

| 表现/记录 | 下一步检查 | 不要做什么 |
|---|---|---|
| connection_error | 主机端口映射、现有服务是否启动、是否误填 Agent 端口 | 不反复发长请求、不先重启生产 |
| http_401 / http_403 | 本机服务 key、环境变量与权限 | 不把 key 放进仓库或URL |
| http_404 / model 不存在 | /v1 路由、真实 served ID | 不用显示名猜模型 |
| http_400 提到工具 | 对照实际版本的 tool parser、auto tool choice、聊天模板 | 不假定聊天通就代表工具通 |
| http_400 提到图片 | 模型是否视觉、多图数量、模板与服务限制 | 不把图片路径当视觉输入 |
| token_limit | 看 usage、是否生成推理、输出预算；另复制配置单独改预算对照 | 不把截断当成功、不悄悄扩大原基线 |
| answer JSON 不合规 | 看原响应 content，区分格式错误与事实错误 | 不从一堆解释中偷偷提取“看起来正确”的片段 |
| correct但missing_evidence | 没真正读取工具所需文件/没列目录 | 不接受猜中了答案 |
| repeated_identical_call / round_limit / tool_call_limit | 看工具名、参数和回传，定位不理解结果或不会结束 | 不仅提高上限来隐藏循环 |
| timeout 或明显慢 | 查 wall/API耗时、业务负载、上下文、生成量、实际thinking；核对服务端是否仍在生成 | 不直接归因某一个框架 |
| direct对、tool错 | 优先查协议、tool message、推理字段回传与多轮行为 | 不宣称“模型完全没问题” |
| direct和tool都错 | 先核对规则/真值、模型/配置、响应完整性 | 不优先开发更多业务工具 |

本探针严格要求整个最终content为JSON，不接受Markdown围栏，字段类型、数组顺序和重复记录都核对。它有意不使用 response_format 强制结构化解码，以避免掩盖基础行为；后续可另做明确标记的对照。

## 10. 受控调整 thinking / 输出预算

基线 `request` 不含 thinking 参数，表示继承当前服务默认值，**不是关闭 thinking**。先查服务启动与对应模型/版本文档，记录实际配置。

只有确认模型模板支持时，才在本地配置副本尝试例如 `"chat_template_kwargs": {"enable_thinking": false}`。这是条件示例，不是所有模型通用设置，也不应猜 `reasoning_effort` 枚举。REST请求中这些字段位于顶层；这里不是SDK调用，不加一个名为 extra_body 的外层对象。

```bash
cp configs/poc01.local.json configs/poc01.variant.local.json
# 手工只改一个已确认支持的设置，并写进 notes
python3 scripts/poc01.py --config configs/poc01.variant.local.json run --case direct-basic,tool-basic --repeat 3 --label variant-one-change
```

HTTP接受字段或响应出现reasoning，不足以单独证明开关确实生效。保存真实请求、服务端证据与输出差异。当前脚本保留返回的 reasoning / reasoning_content 字段用于下轮上下文；某后端不接受时记录协议问题，不静默删掉后声称等价。

## 11. 停止与提交反馈

Ctrl-C中断当前尝试并保存结果，后续计划停止；不可等同生产命令撤销或GPU立即空闲。强制kill/断电可能来不及写完，需与正常中断区分。

完成后填 `.local/poc01/notes.md`，提供各类完成数、wall耗时、轮数、工具数、首个失败阶段和配置差异。对外分享先人工脱敏，只摘必要片段；不要打包上传整个 runs。

此轮不执行断网测试命令，避免切断你的 SSH/业务。真正离线产品验收在维护窗口完成；客户端只访问回环地址不能证明服务端内部没有外网依赖。
