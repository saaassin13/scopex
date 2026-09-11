# POC02-A：模拟调用 ID 兼容性修复

接续 [原生接线预检](02-native-preflight.md)。本修复只涉及 ScopeX 的测试接收器，不修改 OpenClaw、vLLM、业务数据或 POC01 评分。

## 已定位的原因

用户提供的这次现场记录显示：原生 exec 返回了 `SCOPEX_SANDBOX_WIRE_CHECK`，工具失败数为 0；第二次请求中的 assistant tool-call ID 和 tool result ID 同为 `callscopexpreflight`。但旧模拟接收器发出并硬匹配的常量是 `call_scopex_preflight`。

旧代码先按原 ID 过滤，再检查 marker。过滤结果为空，所以模拟接收器自行返回 422 `native exec did not return the marker`。此处并不是标记真的缺失，也不是 vLLM 拒绝请求；本预检没有上游 vLLM 调用。OpenClaw 输出中的 provider/schema 错误是在报告模拟接口返回的 422。

本次可确认存在下划线被去除的 ID 变化，不能仅据此推断所有模型、提供方或 OpenClaw 版本均采用同一规则。原请求/响应保留，旧报告不得改成预检通过：该次后续的挂载和文件可达性检查尚未执行。

`extra_body overwriting request payload keys` 对应此预检配置显式覆盖输出预算和 chat_template_kwargs；最终捕获值与基线相符。它不是本次 422 的触发条件，但也不能据此独立证明仅使用 CLI `--thinking off`、不设 extra_body 时会得到相同请求。

## 修复内容

- 模拟接口改用纯字母 ID `callscopexpreflight`，保持它在已观察到的变换下不变；没有取消严格 ID 匹配，也没有对任意 ID 做模糊清洗。
- 工具结果仍须匹配指定 ID 并包含 marker；同 ID 出现多个结果时拒绝通过。
- 第二轮 wire 记录增加 `tool_return`，区分结果 ID 不符、重复结果和匹配结果缺少 marker；不打印工具正文。
- 新结果记录 `synthetic_tool_call_id`。配置构建、模型参数、工具范围、沙箱规则、超时、真实任务评分及清理规则均不变。

## Spark 上操作

先停止上一轮已确认属于预检的遗留容器，保留文件与容器本身用于取证。使用失败报告前缀对应、已经列出的**完整容器名**，不要把示例占位符直接复制，不使用通配符、批量 stop/rm/prune，也不操作原 `openclaw-sbx-*` 或生产容器：

```bash
docker stop -t 2 <已确认属于失败预检的完整容器名>
```

Docker `stop` 的官方说明：[docker container stop](https://docs.docker.com/reference/cli/docker/container/stop/)。停止不等于删除；若报告容器已不存在，先查看同一完整名称，不猜其他名称。

然后在 Spark 的 scopex 仓库根目录更新。工作区有代码改动先审阅，不 reset/clean：

```bash
git status --short
git pull --ff-only
python3 -m unittest discover -s tests -p 'test_poc02_preflight*.py' -v
```

上述通配符同时选择原 33 项与新增 17 项测试，仓库本版本合计 50 项；全部通过后再预检一次。继续用已经准备的目录，不重新准备或定位入口：

```bash
python3 scripts/poc02_preflight.py \
  --prepared .local/poc02/prepare-20260911T055921Z-042d152b \
  --openclaw-bin /home/yanlan/.openclaw/bin/openclaw
```

预期第二次请求的 `tool_return` 中 `matching_result_count=1`、`marker_matched_expected_id=true`、`problems=[]`，随后继续沙箱边界与挂载检查。只有全部预检条件通过才出现 `PREFLIGHT_PASS_NOT_MODEL_EVAL`。本轮仍是模拟响应，真实推理次数 0，不计入 Agent 完成率。

本修复未改失败分支的容器清理策略：确认并登记的新容器会停止；过早失败且尚未登记时仍可能留下本轮容器。按新报告的唯一前缀核对，不能假定退出即自动停止。不会自动清理上一轮的容器或修改旧报告。

## 本次实际校验与边界

2026-09-11，本地 Linux x86_64、Python 3.13.5 执行：

```bash
python3 -m unittest discover -s tests -p 'test_poc02_preflight_ids.py' -v
python3 scripts/poc02_preflight.py --help
```

**新增 17 项全部通过。**测试实际启动本地回环 HTTP 接收器，模拟现场观察到的 ID 变换，复现旧 fixture 的 422，再验证新 fixture 的流式/非流式往返。同时验证错误 ID、无标记、重复结果、仅在提示或工具参数中出现标记不能通过；不会为了让测试变绿而绕过关联检查。

主脚本与测试文件通过 Python 3.10 语法解析检查；未在 Python 3.10 实机执行。本次没有启动 OpenClaw、Docker 或模型，也没有在用户 Spark 运行。原 33 项在本交付步骤未重新执行，不把预期的 50 项总数当作本地已经运行的成绩。提交版本的文件 blob 哈希与实际测试文件进行核对。
