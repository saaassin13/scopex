# POC03：两阶段诊断 Finalizer

## 结论

POC03 不再要求一个 OpenClaw 会话同时承担“持续调查”和“最终交付”。当前验证得到的稳定结构是：

```text
用户任务
  ↓
Investigation Agent（OpenClaw）
  ├─ Skill
  ├─ Knowledge
  ├─ read / exec / process
  └─ 自主选择调查路径
  ↓
Evidence Context Builder
  ├─ 只接受 Agent 实际看过的原始日志行
  ├─ 去重
  ├─ 按故障相关性筛选
  └─ 编号 E1 / E2 / ...
  ↓
Fresh Finalizer
  ├─ 新会话
  ├─ 无 tools
  ├─ 无 assistant tool-call history
  ├─ 无 tool role history
  ├─ thinking=false
  └─ 只引用 E 编号
  ↓
确定性展开与校验
  ├─ E 编号 → 精确原始日志
  ├─ schema 校验
  └─ 复用 POC03 business grader
```

## 为什么要拆成两阶段

真实实验中，Investigation Agent 已经可以自主读取 Skill/知识、定位失败、调用 Shell，并继续找到恢复证据；但单一会话会持续累积 tool history，模型容易继续调查而不停止，最终 prompt 膨胀到约 20k tokens。

只在原会话里隐藏 tools 也不足以完成切换：Qwen 会继续沿历史 `<tool_call>` 模式生成工具调用。

Fresh Finalizer 使用全新上下文后，首 token 可以正常输出，说明调查能力和最终整理能力都可用；之前 512-token 输出失败只是因为直接复制多条长日志导致结果被 `finish_reason=length` 截断。

## Evidence Context Builder 规则

1. 只从已记录的 tool results 中确认“Agent 实际看过”的日志。
2. 最终证据必须逐行与原始 `input.log` 完全匹配。
3. `grep -n` 等命令的行号前缀不被信任；只按原始日志字符串匹配。
4. 对候选证据做通用诊断相关性排序：
   - 用户关注的故障 anchor；
   - ERROR / WARN；
   - failed / invalid / exception；
   - succeeded / recovered；
   - 与 anchor 的位置距离。
5. 默认最多传递 12 条候选证据给 Finalizer。
6. Finalizer 最多选择 4 条，并优先包含 trigger / failure / recovery。

这些规则不包含 CowDisinfect 的固定调查流程，不规定必须执行哪个命令。

## Finalizer 输出

Finalizer 不复制原始日志，只返回证据引用：

```json
{
  "direct_trigger": "...",
  "persistence": "transient",
  "recovery": {
    "observed": true,
    "evidence_ref": "E7"
  },
  "evidence": [
    {"role": "upstream", "ref": "E4"},
    {"role": "trigger", "ref": "E5"},
    {"role": "failure", "ref": "E6"},
    {"role": "recovery", "ref": "E7"}
  ],
  "facts": ["..."],
  "inferences": ["..."],
  "unknowns": ["..."],
  "conclusion": "...",
  "confidence": "high"
}
```

程序再把 `E4/E5/...` 确定性展开成原始日志，从而避免模型抄写长日志造成 token 浪费或字符错误。

## 当前验证命令

先运行单元测试：

```bash
python3 -m unittest \
  tests/test_poc03_run.py \
  tests/test_poc03_finalize.py \
  -v
```

复用已经完成调查的 audit，不重新执行 Agent：

```bash
python3 scripts/poc03_finalize.py \
  --run .local/poc02/prepare-20260911T055921Z-042d152b/poc03-20260912T060813Z-a61cf7f6 \
  --model qwen3.8-27b-nvfp4 \
  --base-url http://127.0.0.1:18002/v1
```

默认：

- evidence catalog：最多 12 条；
- finalizer：384 tokens；
- thinking：关闭；
- tools：无；
- 自动重试：无；
- finalizer timeout：180 秒。

最终成功状态：

```text
PASS_POC03_TWO_PHASE
```

## 后续

在独立 Finalizer 验证通过后，再把两阶段流程串入 ScopeX runtime，并增加通用 Investigation Budget：模型轮数、tool call 数和上下文预算。预算只控制资源边界，不写死业务调查步骤。
