# POC02-A：完成判定修正与筛选优先对照

日期：2026-09-11。继续复用已通过的原生预检，不重装、不重启现有 Gateway/vLLM、不重新生成日志或真值。本次分清两件事：修正测量程序的状态语义错误；另用显式参数开启一个新的提示策略对照。两者不可混称为模型优化成绩。

## 1. 已核实的根因

在用户报告的真实任务中，模型最后响应与 CLI 可见回答均为完整纯 JSON，独立对比标准答案均 strict_match=true；没有 error、abort、fallback，最后模型 finish_reason=stop。旧脚本唯一触发项是 meta.replayInvalid=true，总耗时 133.3608 秒。

按用户安装的 OpenClaw 2026.9.2 / 3928bad 源码：

- [replay-state.ts](https://github.com/openclaw/openclaw/blob/3928bad/src/agents/embedded-agent-runner/replay-state.ts)：该状态用于压缩或重试后的安全重放；无法证明安全时也可以置位。
- [tool-mutation.ts](https://github.com/openclaw/openclaw/blob/3928bad/src/agents/tool-mutation.ts)：isReplaySafeToolCall 对 exec/bash 返回 false。是否为只读命令与是否可自动安全重放是不同判断。
- [incomplete-turn-resolution.ts](https://github.com/openclaw/openclaw/blob/3928bad/src/agents/embedded-agent-runner/run/incomplete-turn-resolution.ts)：resolveReplayInvalidFlag 会因 replaySafe=false 置位；resolveRunLivenessState 的普通返回值是 working，不是专门的“进程仍在运行”检测。

所以旧 poc02_run.py 把 replayInvalid 与 error/aborted/fallback 并列为无条件失败是不正确的。修正仅取消这一错误等同，保留 replay_unsafe_do_not_auto_retry 警告；不会将任意 replayInvalid 记录直接判成功。真实 error、abort、fallback、空答案、错误 payload、未结束响应、证据缺失和超时仍独立处理。

原 NOT_COMPLETED 文件不覆写。该样例依据用户提供的正文、工具返回和时间，应记录为“已交付正确结果，但超过 SLA”；下面的只读复核在本机核对原请求、输入哈希及分页内容后给出分项结论。133.36 秒不会因判定修正变成满足 120 秒目标。

## 2. 工具路径的评价

本次轨迹为 read → read(offset=118) → read(offset=218) → grep → awk/cat/head → 最终 JSON。

前两次是正常分页；第三次请求第 218 行，而文件总共 213 行，未取得新数据。grep 返回目标记录；末次命令检查相关行及行尾空白，不能将全部工具调用都算成绕圈。toolSummary.failures=0 也不能抹去一次越界查询的事实。

已知第一次请求约 3357 个 prompt tokens，后续增长到约 20200；上下文膨胀与六轮请求是值得检验的性能方向，但不能仅据汇总量断言每一秒属于预填充、缓存或纯解码。前五个请求窗口合计约 67.09 秒，最终回答请求约 56.47 秒；这些不是工具本身耗时，也不能把删除某一步后的节省量机械相加。

## 3. 更新与测试

在 Spark 仓库根目录，以普通用户执行。有工作区改动先审阅，不 reset/clean：

```bash
cd /home/yanlan/workspaces/code/scopex
git status --short
git pull --ff-only
python3 -m unittest discover -s tests -p 'test_poc02_r*.py' -v
```

本批预期 **63 项通过**：更新后的 real-run 测试 33 项，以及新分项复核测试 30 项。测试用合成记录、模拟 native/Docker 和本机假 HTTP 上游，不会调用真实 OpenClaw 或 GPU 模型。

## 4. 先复核旧记录：零次推理

```bash
python3 scripts/poc02_review.py \
  .local/poc02/prepare-20260911T055921Z-042d152b/real-a-20260911T080004Z-d933d21c
```

只输出摘要，不修改原 result/summary/answer，也不写 Python 缓存、不调用模型、Docker 或 Shell。摘要记录旧状态、新的 review_status、分项评分、原记录哈希、重放警告和时间。退出码 0 仅表示复核执行成功，不代表 SLA 达标。

分页证据不再要求单次 read 必须返回全文。它核对同一调用 ID 的 read 结果、原文件路径、明确的 offset、连续原文行、续读提示和累计行覆盖。重复行、空格都保留，只统一换行编码；错误 ID、错 offset、冲突历史、缺页不能算完整读取。EOF 越界返回不贡献证据，但也不抹去之前已取得的正确证据。不会把任意 exec 输出或一条带路径的命令直接当作已读全文。

预期这份原始记录能得到 CORRECT_OVER_SLA，covered_lines=source_lines=213，并保留 replayInvalid=true。若得到 REVIEW_ERROR、REVIEW_BLOCKED 或 evidence review_required，先处理具体差异，不删检查、不重跑模型“修复”旧记录。

这是运行时转录证据，不是操作系统级文件访问审计。只读复核中的 final-response 检查还要求最后原始 SSE 完整结束、finish_reason=stop，且原始最终文字与 CLI 最后可见答案一致。对答案做独立评分，不从更早输出里挑一个正确答案。

## 5. 然后只跑一次显式策略对照

原样例已取得答案正确性的证据，不必为修正测量程序再完整推理一次。下一次需要一个明确的行为改变量：在原业务要求末尾追加通用的“筛选优先”策略。

```bash
python3 scripts/poc02_run.py \
  --preflight .local/poc02/prepare-20260911T055921Z-042d152b/native-preflight-20260911T073155Z-21eea8ea \
  --openclaw-bin /home/yanlan/.openclaw/bin/openclaw \
  --strategy filter-first
```

**这条会真实调用本机模型，只执行一次任务。** 输入、标准答案、模型、temperature、thinking、输出预算、工具范围、沙箱权限以及 120/180 秒预算均不变。原生 read/exec/process 仍由模型自主选择；不提供本样例的错误行号、四条答案或预写 grep/awk 命令，不固定工具顺序。

策略全文位于 poc02_run.py 的 FILTER_FIRST_GUIDANCE，原 task.txt 中也会保存实际附加文本。含义是：对明确条件的文件任务，优先在文件侧筛选，只返回匹配记录与必要上下文；必要时少量采样结构；遵循明确的分页位置；证据足够后收尾，不以减少调用为由跳过必要核查。

默认 --strategy baseline 与旧业务任务文本逐字相同；不是偷偷修改基线。新报告记录 strategy、原 prompt 哈希和实际 task 哈希，评分规则版本为 assessment_version=2。**filter-first 属于有指导的运行时配置对照，不是未经指导的原基线，更不是已经证实的性能改善。** 若有效，后续可提炼为通用项目指引/Skill，但需换窗口、无匹配结果等未调优用例验证泛化，不为这个样例硬编码流程。

运行结束后直接看新 summary。模型若自行用 exec 完成全部筛选，可能显示 EVIDENCE_REVIEW_REQUIRED：这不是自动判失败，也不是自动成功，应检查命令、返回数据与标准答案。不能为了获得自动评分而强迫它回到读全文。

若仍正确但超时，按新轨迹评估生成量、上下文和轮次；若提速但漏项，不接受这个策略。一次通过仅是筛选信号，不是稳定完成率。出现真实连接/资源故障或超时，保留记录，不启动长批次；客户端关闭不单独证明后端推理已取消。

## 6. 本次校验与限制

实际运行环境为本地 Linux、Python 3.13.5，不是用户 Spark。上述 63 项测试全部通过，并完成 Python 3.10 语法解析检查（不声称已在 Python 3.10 执行）。覆盖状态语义、真实错误保留、最后可见 payload、原代理字节透传、边界失败不转发、分页精确覆盖、重复行与尾部空格、只读复核不改文件、不联网、不启动进程，以及策略默认不变。

真实日志仅在本地用于来源字节核对，未上传到公开仓库。没有在此环境执行用户真实记录的完整复核，也没有执行 filter-first 的 Spark 模型任务；这些现场成绩仍待用户运行。
