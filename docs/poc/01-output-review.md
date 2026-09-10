# POC01：输出内容、格式与执行证据分开复核

本说明补充 [Spark 执行手册](01-spark-runbook.md) 的结果检查，不改变原 POC 的任务、评分、提示词或阶段门。适用于最终 JSON 被代码块包装等情况。

## 1. 保留原分数，同时解释失败

原任务明确要求纯 JSON，因此返回 Markdown 代码块仍然违反输出约定。不能为了通过而修改 expected、原始 answer、correct、within_sla 或丢弃失败尝试。

但格式违约不等于提取内容错误。新增 `scripts/inspect_run.py` 单独读取已保存的 cases、results、plan 和工具事件，生成诊断表；不请求模型、不联网、不写入运行目录、不打印原始答案或推理文本。

| 诊断分类 | 含义 |
|---|---|
| strict_match | 纯 JSON、内容及该模式所需证据符合 |
| format_only | 原答案是完整单一 JSON 代码块，去掉包装后精确匹配，所需读取证据也成立；原严格验收仍失败 |
| content_mismatch | JSON 可以解析，但值、字段、数组顺序或类型不符合 |
| missing_evidence | 缺少该用例要求的成功读取或列目录证据 |
| unparseable | 按本规则无法可靠取得 JSON，不直接断言提取内容错 |
| not_completed | 超时、错误或中断等；不把残留答案当成功 |

诊断只允许整段单一三反引号代码块，语言标记可为 json 或空。不会从解释文字、多段回答中挑一个看起来正确的片段；不补字段、不修改值、不修复截断 JSON。拒绝重复键和非 JSON 常量。对象键顺序和空白不影响比较，数组顺序、布尔值与整数等仍严格区分。

`内容+证据符合（诊断）` 不代替原严格正确率，也不是最终 Agent 完成率。所有原始状态保持不变。

`required_listing=true` 在 direct/tool 模式可表示“此模式不要求列目录”，不能单凭它证明调用过 list_files。实际调用看 events。相同名称、相同参数的额外调用单列统计，但是否冗余还要看第一次是否成功、数据是否会变化。

## 2. 更新后检查已有运行

在 Spark 仓库根目录，工作区有改动时先审阅，不执行 reset/clean：

```bash
git status --short
git pull --ff-only
python3 -m unittest discover -s tests -p 'test_inspect_run.py' -v
```

把下面路径替换成已经存在的运行目录：

```bash
python3 scripts/inspect_run.py runs/<实际运行目录>
```

也支持一次读取多个目录，分别报告，不合并不同实验条件的完成率。例如：

```bash
python3 scripts/inspect_run.py runs/*-smoke-nothink-*
```

退出码 0 仅表示诊断读取成功，不表示模型验收通过；输入或文件错误返回 2。

## 3. 已发现仅格式失败时的下一步

在已经有 `configs/poc01.nothink.local.json`，且第一次请求级关闭 thinking 对照已执行的前提下，保持该配置、样本、提示词、工具及模型服务不变。先用相同两个基础任务各补 3 次，观察提取内容、格式、重复调用和耗时是否重复出现：

```bash
python3 scripts/poc01.py \
  --config configs/poc01.nothink.local.json \
  run --case direct-basic,tool-basic --repeat 3 \
  --keep-going --label repeat-nothink
```

共 6 次计划，不是 3 次。每次仍有原任务预算、轮数和工具上限。`--keep-going` 保留失败并继续后续计划，不是重试到成功；它也会继续非格式类失败，因此发现连接故障、超时、OOM 或业务影响，应 Ctrl-C 停止并核对服务端状态，不叠加请求。

读取终端打印的新目录，或：

```bash
python3 scripts/inspect_run.py runs/*-repeat-nothink-*
```

每个类别分别看严格正确、内容与证据诊断、仅格式失败、耗时、工具轮次。少量重复只帮助定位与筛选配置，不证明 90% 或 95% 的长期完成率。

若内容与证据稳定、仅有格式违约，将输出约定修复作为单独、有记录的后续实验；普通对话展示与交付给程序的 JSON 要区别设计。不能不经决策就在当前探针中偷偷去掉包装并重新计算成功。

若出现内容错误、缺少读取证据、反复调用或长耗时，则分别定位，不把所有失败都归因于 thinking。不要同时换模型、限制为固定 read→answer 流程、删工具和改评分。

后续仍需空结果、短链路、双图和原失败日志；本补充不替代这些阶段门，也不意味着可直接选定最终 Agent。

## 4. 本次新增工具的验证边界

新增 `tests/test_inspect_run.py` 的 25 项测试已在本地 Python 3.13.5 环境通过。覆盖代码块、错误内容、多段/带解释输出、类型与重复键、读取证据、超时残留答案、同参调用计数、命令行和运行目录不被修改。

测试使用合成结果和临时目录，不连接模型，不代表 Spark 模型性能验证。本次没有修改 `scripts/poc01.py`，未据此重新宣称原主探针全套测试成绩。公开仓库只提交通用代码、测试及说明，不提交用户实际运行目录、设备配置或生产数据。
