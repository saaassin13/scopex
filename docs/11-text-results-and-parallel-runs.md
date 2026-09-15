# 文本结果、独立任务并发和活动入口

2026-09-15 用户确认。本文件覆盖早期文档中的强制 Claims 主路径和单执行槽位描述。

## 范围和状态

实现：单次无工具文本报告；独立任务并发、有限在线队列；全局活动入口；完整批次计时工具。
未实施：完成后追问、运行中追问及跨 Run 上下文恢复改造。现有 Stop/Steer/Resume 接口仅保持兼容。
尚未验收：Spark 多任务批处理带来的真实总耗时改善。代码可以并行不等于业务批次已经加速。

OpenClaw 继续拥有调查、决策、执行、验证和停止。ScopeX 的队列只管理任务准入，不编排业务步骤。

## 结果主路径

业务 Evidence（原图重新打开并校验 SHA）→ 一次无工具 TextReportComposer → 可读正文。
不要求模型输出 Claims、分类枚举、引用数量协议或嵌套 JSON。旧 StructuredFinalizer / ReportComposer 保留历史和 Step 6 专项兼容，不再是产品默认路径。

系统直接提供状态、时刻、来源和已经计算的业务数据。不从自然语言反向提取 KPI，不新建逐业务字段翻译字典。
`result.json` 包含 version=2、report_text、report_meta、execution_status、investigation_reasons；正文写 report.md / final.txt，复盘包包含这些文件。

标题、分节或 Markdown 不标准不阻断显示。前端按转义文本显示正文，不执行模型生成的 HTML/脚本。引用来源列表仅证明有这些来源，不证明每句话被语义蕴含；不再展示 Validated Report 徽标。未对应的 [E编号] 给出警告，不生成伪造链接。

空正文/传输失败为 unavailable；不完整流/length 为 partial，保留草稿但不算完整交付。报告失败不抹掉已取得的 Evidence，任务仍明确标为未完整交付。绝不拿“模型有返回文字”代替业务正确率验收。
图片必须在这一次模型调用中直接附上已校验原图；缺图、SHA 不符或输入预算不足明确失败，不静默删图或删掉分母/限制。

## 并发与队列

产品 CLI 默认 --max-active-tasks 2、--max-queued-tasks 16、--queue-timeout 600。活动任务范围 1..4 可配置；历史直接构造 TaskService 的默认仍是单槽位/无队列，保留原测试语义。

每个任务独立 agent_id/session、Runtime、Scratch、Evidence 和审计。模型请求和报告调用之间没有全局串行锁，多个请求可同时到达同一 vLLM；由 vLLM 处理模型批次。只启动一个 ScopeX API 进程，data-root 文件锁防止第二个进程争用或误修复活跃状态。

队列有上限和超时，满了返回明确错误。排队不构建 Runtime/Sandbox，不提前采集 host 快照；真正准入后才准备环境。历史相对时间窗使用创建/计划时刻，不随队列等待漂移；当前资源用执行时采样时间。执行、准入等待、总耗时分开记录。

排队可取消；活动任务暂停仍沿用安全边界。第一版暂停保留逻辑执行名额（与旧行为一致），不宣称已实现暂停释放/恢复排队。不同任务停止或清理不能影响其他 agent。当前并发只支持隔离 Sandbox 模式；网关/设备写动作的并发与锁不在本轮。

定时任务到点创建普通任务，在线忙碌可有限排队。同一 schedule 已有活动/排队项时，新触发跳过并记录原因，不合并时间窗、不无限累积。
设备离线期间错过的触发全部跳过；重启前排队项过期，运行/报告中任务标记中断，不静默重跑。旧 Evidence 保留；不凭宽泛容器前缀清理未知历史进程。

GET /activity 是不受日历日期限制的实时元数据摘要，不读取历史任务目录或业务 Evidence。前端顶部全局入口显示运行/等待/暂停，侧栏展示最近真实活动、等待/执行时长和队列位置。模型处理中包括模型服务内部等待，不虚构准确 GPU 调度状态或百分比进度。服务不可达时显示状态未知。

## vLLM 变更边界

最后用户回执：vLLM 0.27.1+93523f72.nv26.8.64249418；图片 count=12；mm-processor-cache-gb=0.5。
max-num-seqs 的最后文档值为 1，新的实机值仍需 inspect。**本次代码更新不会修改或重建 vLLM。**

若仍为1，需要在确认原 Compose/容器管理入口、保存现有参数并安排维护窗口后，将其调整为2来测试模型并发。保留镜像、模型 revision、12张额度和其他已验证参数，不顺手调大所有预算。仅 docker restart 不能改变 Cmd。原 Compose 入口未确认时，不给无差别重建脚本。

使用同一个模型服务，不为每项任务复制一份模型。显式 --kv-cache-memory-bytes 会覆盖自动估算缓存量，不能只调 gpu-memory-utilization 期望缓存变大。实机检查 KV 抢占/重算、内存、CPU/I/O 和生产业务影响。
参考官方参数说明：https://docs.vllm.ai/en/v0.27.0/cli/serve/ 。现场 NGC 构建以实际 --help 和 inspect 为准。

## 更新运行

先等待任务结束，停止 ScopeX API；保留 .local / package-lock / 原数据，不 reset --hard / clean。
拉取 main 并重建 frontend dist（本次有前端变更），仅重启 ScopeX，不重建模型或 Sandbox。

```bash
cd /home/yanlan/workspaces/code/scopex
# 已停止旧 Runtime，并确认没有未保存的代码改动之后：
git fetch origin
git switch main
git pull --ff-only origin main
(cd frontend && npm run build)
SCOPEX_MAX_IMAGES_PER_PROMPT=12 .venv/bin/python scripts/runtime_api.py \
  --model qwen3.8-27b-nvfp4 --base-url http://127.0.0.1:18002/v1 \
  --workspace .local/workspace --data-root .local/runtime-api \
  --sandbox-image scopex-sandbox-analysis:step7 --enable-view-image \
  --max-active-tasks 2 --max-queued-tasks 16 --queue-timeout 600
```

沿用现有额外 --data-dir/路径/鉴权参数。只有 npm 依赖尚未准备好才按部署文档在构建期安装；运行期不安装。
验证修复必须新任务或独立回放，旧失败页不会因升级被改写。小图容量通过与真实图像正确性、并发吞吐是不同的验收项。

## 不重新扫描业务数据的输出回放

```bash
.venv/bin/python scripts/replay_text_report.py \
  --saved .local/runtime-api/tasks/task-bc28ce880662 \
  --out .local/report-replay/encoder-01 \
  --model qwen3.8-27b-nvfp4 --execute
```

也支持 review ZIP。目标必须是新目录、不能放进原任务目录；图像回放需要显式 --data-bind HOST:AGENT:ro。一次模型请求、零工具重跑；不改写原 task/result。回放成功只是报告生成成功，不证明旧业务统计已经人工对账。

## 整批总耗时对照

先用同一版文本输出代码，固定数据/明确绝对时间窗，暂停定时任务，保证服务器空闲。固定 vLLM 配置（建议完成2路维护验证后保持不变），对比 ScopeX 1 个活动任务与2个活动任务。不能把结果链少一次调用的收益算成并发收益。

创建本机 cases.json，不提交现场数据：

```json
{"read_only":true,"dataset_id":"固定现场样本标识","cases":[
 {"id":"encoder","message":"分析2026-09-11 12:00至13:00的编码器具体异常，只读调查。"},
 {"id":"nipple","message":"统计2026-09-11 14:00至15:00最终采用帧2D乳头识别率，只读调查。"}
]}
```

把日期改成实际存在且已经人工确定覆盖的样本。先以 --max-active-tasks 1 启动同一版本 API，再运行：

```bash
.venv/bin/python scripts/benchmark_task_batch.py --cases cases.json \
  --expected-slots 1 --out .local/benchmark/serial-01.json --execute-read-only
```

等整批完成，停止 API，以 --max-active-tasks 2 重启（保持同样路径、模型和其他参数）再运行：

```bash
.venv/bin/python scripts/benchmark_task_batch.py --cases cases.json \
  --expected-slots 2 --out .local/benchmark/parallel-01.json --execute-read-only
.venv/bin/python scripts/benchmark_task_batch.py \
  --compare .local/benchmark/serial-01.json .local/benchmark/parallel-01.json
```

整批耗时从首次提交到最后一份完整可读报告，包含排队、推理、工具和报告。失败/缺失/不完整报告不算加速成功。默认只输出计时观察，人工核对两批范围、单位、缺失、原图及生产业务无影响后才加 --quality-confirmed。配对重复至少3次并控制预热条件。2路无收益就定位瓶颈，4路不默认开启；“能同时运行”不等于本项目吞吐 Gate 已 PASS。
