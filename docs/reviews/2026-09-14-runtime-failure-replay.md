# 两个真实失败包复盘：聚合证据与多图容量

状态：2026-09-14，针对 `419784a` 基线；代码回归和真实业务验收分开记录。

## 1. 两个不同失败阶段

编码器任务 `task-de435616a790`：

- `last_reason = fresh_structured_finalizer_failed`。
- 调查已结束，4 次调查阶段模型请求、3 次工具调用、1 个结构化 Evidence。
- Finalizer 完成 JSON 输出，`finish_reasons = ["stop"]`，不是 JSON 解析错误。
- `result.json.errors = ["claims[1].duplicate_claim"]`。
- 两条同 scope 的 fact 分别描述“5 个候选事件”和“3 段恒定区间”，共同引用聚合统计 E1。旧去重签名忽略 topic，错误地将它们视为同一事实。
- Report Composer 尚未运行，不是中文报告排版导致任务失败。

图片任务 `task-52fcca3534ca`：

- `last_reason = investigation_turn_incomplete`。
- `cli_flags.error.message = 400 At most 4 image(s) may be provided in one prompt. (parameter=image)`。
- 已调用三次 view_image，每次 2 张；每次调用成功加载不代表下一次模型请求一定被接受。
- 总图片附件随上下文累积到 6，超过模型服务报告的 4 张上限。包内没有 wire 请求正文，完整请求仍需现场审计核对；明确的 400 错误足以定位容量阻塞。
- 尚未进入 Fresh Finalizer 或 Report Composer，不能从本次失败判断视觉模型识别起雾的准确性。

这些包没有原始 12 点日志和 13 点原图；本次是执行链复盘，不是重新完成业务诊断。

## 2. 聚合 Evidence 的去重修复

一份 structured_business_facts 可以包含多个独立事实。它不应重新拆成几百条 JSON 行，也不能要求“一个 E 只能有一个事实”。

对 structured_business_facts 的 observed claim，重复身份使用 scope + refs + 规范化 topic。完全相同的重复仍拒绝；缺证据、引用越界、事实/推理约束、每个 Claim 的引用上限不变。旧 line evidence 的去重行为保持兼容。

这只是结构/引用校验，不证明任意自然语言陈述都被 Evidence 语义支持。例如“5 段候选区间”不能自动变成“5 个反向脉冲”或“5 次硬件故障”。真实结果仍需业务抽验。

## 3. 图片容量必须跨层统一

新增统一配置：`SCOPEX_MAX_IMAGES_PER_PROMPT`，默认 4，可显式设为 1–12。

同一配置用于：

- Runtime Context：声明完整 prompt 的累计图片额度；
- 请求策略：统计所有 messages 的图片附件，超过额度即拒绝，不偷偷删除历史图片；
- Fresh Finalizer 的 EvidenceMediaLoader：按同一额度重新打开原图，保留 SHA 校验；
- 部署容量检查脚本。

每次 view_image 最多 2 张是另一条桥接约束，不等于整个 prompt 上限。

OpenClaw 仍管理调查与上下文；本次不增加滚动删图器、不增加第二个 Agent Loop、不改变业务计算阈值。

### 需要 8–12 张代表原图时

先在现有 vLLM 启动命令中设置：

```bash
--limit-mm-per-prompt '{"image":12}'
```

保留当前已验证镜像、模型权重、served-model-name、工具调用相关参数和上下文长度；不要为了这次容量调整顺带换模型。重新创建/启动使用新参数的模型服务。只设置 ScopeX 环境变量不会改变 vLLM 上限。

再执行小容量探针：

```bash
python3 scripts/check_model_image_capacity.py \
  --base-url http://127.0.0.1:18002/v1 \
  --model <真实served-model-id> --images 12
```

只有 `accepted: true` 后，使用同一个环境启动 ScopeX：

```bash
export SCOPEX_MAX_IMAGES_PER_PROMPT=12
# 然后执行原来的 scripts/runtime_api.py 启动命令
```

systemd 部署则把 `SCOPEX_MAX_IMAGES_PER_PROMPT=12` 放入服务实际读取的 EnvironmentFile，重启 ScopeX 服务。

探针仅发送 12 张生成的 64×64 PNG，不读取业务目录，不加载模型新权重。PASS 只说明服务接受该数量，不证明全分辨率图像的 token/显存预算，也不证明起雾识别正确。

保留 4 张部署时，ScopeX 使用默认 4，报告必须声明抽样覆盖限制。不得再指导模型通过多次 2 张调用规避累计上限。

官方参数说明：https://docs.vllm.ai/en/stable/cli/serve/ （`--limit-mm-per-prompt` 是 per-prompt 约束）。

## 4. 图片调查的额外低效点

本次先请求 256 个路径导致 16000 字符工具截断，再重复定位、临时采样、反复执行 metrics 并猜错 `images` 字段。Skill 已改为直接请求小规模时间样本，并明确 metrics 的返回结构。指标只供分组/参考，最终结论仍必须来自原图视觉。

这不表示所有工具输出投影问题已完成修复；截断 Locator/临时命令进入内部 Evidence 的情形仍需进一步收敛，不能冒充用户事实。

## 5. 验证范围

本次本地验证针对聚合 Claim 去重、完整 prompt 图片计数、跨消息计数、配置边界，以及模拟 4 张/12 张限制的 HTTP 服务。真实编码器复盘的 claims/Evidence 还做了不改输入的结构回放。

尚未宣称 PASS：完整仓库测试、Spark 上 vLLM 新配置、8–12 张全分辨率原图上下文、真实视觉判断、真实编码器事件业务准确率。

保持 PR Draft，不合 main，不修改已经冻结的 Step 6 结论，不更新阶段验收 handoff。
