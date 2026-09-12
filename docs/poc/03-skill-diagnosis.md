# POC03：项目 Skill + 业务知识诊断

## 目的

POC03 不再验证“模型会不会 grep/read”。它验证：在不把调查流程写死的前提下，OpenClaw 是否能发现并读取工作区 Skill/知识，自己选择工具调查真实日志，并给出可核对的业务诊断。

当前案例继续复用 POC02 已验证的只读 `input.log`，目标事件是 `07:59:21` 左右的 `CalLeftCamStartFollowPt failed`。

## 设计约束

- Skill：`skills/cow-disinfect-diagnosis/SKILL.md`
- 项目知识：`skills/cow-disinfect-diagnosis/references/cow-disinfect-log.md`
- Skill/知识只包含通用业务语义和证据原则，不包含本案例时间戳、恢复记录或预制答案。
- 评测答案只存在于宿主机 runner/grader；ScopeX 仓库不会挂入 Agent sandbox。
- Sandbox 保持 POC02 的只读工作区、无网络、无 Docker socket、drop capabilities。
- Agent 工具仍只有 `read / exec / process`，不强制 tool choice，不提供固定 shell 命令。
- 这轮只验证一次功能可行性，不以 120 秒 SLA 判定成败。

OpenClaw 官方当前约定：工作区 Skill 位于 `<workspace>/skills`；`agents.*.skills` 是可见 Skill 的最终 allowlist；Sandbox session 会同步工作区 Skill。POC03 因此给测试 Agent 只暴露 `cow-disinfect-diagnosis` 一个 Skill。

## 通过条件

必须同时满足：

1. OpenClaw `skills list` 能看到测试 Skill。
2. Agent 实际读取 `SKILL.md`。
3. Agent 实际读取项目知识 reference。
4. 最终 evidence 中的原始日志行逐字存在于 `input.log`。
5. 直接触发证据包含 StartFollowPt 因左膝/左腿检测缺失而失败。
6. Agent 不在 ERROR 处停止，工具返回中实际看到了后续 `CalLeftCamStartFollowPt succeeded`。
7. 最终判断当前日志窗口内为 `transient`，并明确后续恢复。
8. facts / inferences / unknowns 分离，unknowns 非空。
9. 不无证据断言相机损坏、网络故障、硬件故障或模型失效。

通过只说明“这个单案例的 Skill + 知识 + 自主工具调查链可行”，不等于业务正确率已达标。

## 运行前提

- 已有通过的 POC02 native preflight。
- OpenClaw 仍为已验证的 `2026.9.2 (3928bad)`。
- 本地 OpenAI-compatible 模型服务已经启动。
- 推荐使用当前已验证的 NVFP4 vLLM 服务；runner 会主动检查 `--model` 是否存在于 `/v1/models`。

## 运行

示例（模型 ID/端口以实际 `/v1/models` 为准）：

```bash
cd /home/yanlan/workspaces/code/scopex
git pull

python3 scripts/poc03_run.py \
  --preflight .local/poc02/prepare-20260911T055921Z-042d152b/native-preflight-20260911T073155Z-21eea8ea \
  --model qwen3.8-27b-nvfp4 \
  --base-url http://127.0.0.1:8000/v1
```

如 NVFP4 服务不是 8000，只修改 `--base-url`，不要改 runner 的任务/Skill/评分条件。

runner 最多允许 8 次模型请求，默认总超时 300 秒；不会自动重试。

## 结果

审计目录位于原 POC02 prepare 目录下：

```text
.local/poc02/<prepare>/poc03-<timestamp>-<id>/
```

重点看：

```text
result.json
answer.txt
tool-trace.json
summary.md
wire-*-request.json
```

成功状态：

```text
PASS_POC03_SINGLE_CASE
```

如果答案看起来正确但没有证明确实读取 Skill/知识/恢复日志，会返回：

```text
SKILL_OR_EVIDENCE_NOT_PROVED
```

这类结果不能计为 POC03 通过。
