# 交付与验收状态

同步：2026-09-16；代码核对基线 main@cb90d028773b4fac4eabf4b429ecb3fed97f1c51。当前产品使用原生回答和 edge Compose，不再沿用旧报告主链的完成定义。

## 1. 当前已确认

| 层级 | 证据与结论 |
|---|---|
| 集成 | 原生回答、Skill修正、端侧Compose等已在main，非未合入开发分支 |
| 仓库回归 | [Actions 35043132700](https://github.com/saaassin13/scopex/actions/runs/35043132700)：650 tests in 31.437s，OK；compileall、Vue构建、卫生检查通过 |
| 端侧运行 | 2026-09-16用户确认：已部署到端侧服务器，过夜运行总体正常 |
| 证据边界 | 本次未独立读取现场运行SHA/镜像/任务明细，不把上述回执扩大为每一专项均PASS |

“未部署”不再是当前总体状态。后续应记录哪项能力缺少专项验证，而不是重复要求从零部署。

## 2. 保留历史结论

Runtime MVP、Stop/Resume/Steering、Evidence校准以及6A/6B/6C/6D/6F保留原PASS；6E保留CAPABILITY PASS，不扩大业务或新配置范围。

PR #14历史首轮519项及后续修正、PR #15输出恢复、PR #16首轮573项、PR #17的586项属于各自提交的证据。旧记录保留在Git历史、reviews及[2026-09-15验收记录](acceptance/2026-09-15-output-parallel-status.md)，不替代650项的现行基线。

原生回答开发阶段的605项及macOS既有失败见[固定版本开发记录](https://github.com/saaassin13/scopex/blob/cb90d028773b4fac4eabf4b429ecb3fed97f1c51/docs/12-native-answers-and-skill-refinement.md)。不同平台与不同提交的历史失败不抹除，也不自动算成当前Linux回归失败。

## 3. 已整合的产品能力

统一/runs、同Runtime内部模式、原生CLI答案直接保存；默认2活动/16等待、活动入口；普通Schedule、离线不补跑、重启记中断；月历及定时任务历史；终态删除、评价、review ZIP和显式原始数据收集；6个默认Skill、Catalog有界定位、只读业务挂载及独立scratch。

```text
TaskService -> OpenClaw + 模型 + Skill -> 原生CLI outcome
 -> ScopeX正文/来源/执行状态/审计 -> Vue
```

结果version=2，通常producer=openclaw，postprocess_model_calls=0；已核验no_data为scopex_no_data。业务固定schema不是交付关卡。历史Claims/Finalizer/Composer/独立回放仍在，但退出默认产品链。

预算中断、原生错误、截断或进程失败不会因文字/Evidence而变成功；有原生正文为FAILED+partial，无正文为FAILED+unavailable。`tests/test_native_answers.py`覆盖超时草稿、错误通知不当答案和零额外报告调用。旧链“预算中断后报告完整使任务完成”不再列作当前主链待修复项，不据此变更架构。

## 4. 部署配置与运行回执

入口：[deployment.md](deployment.md)、deploy/edge/compose.yaml。ScopeX和vLLM容器restart均no；API绑定显式VPN IP；配置、运行数据、模型与app分开。旧systemd/宿主机方案仅历史参考，不混用。

直接CLI/LocalRuntimeConfig默认compaction关闭；edge Compose显式开启原生compaction，并将单次调查预算设为1200秒、Runtime优雅停止时间设为1220秒；CLI通用默认仍为600秒。edge图片额度默认12同时传给模型与ScopeX，通用默认4，每次view_image最多2。Runtime镜像固定OpenClaw2026.9.2；vLLM Compose max-num-seqs=1。这是仓库配置，不代替运行中参数inspect。

用户过夜运行回执说明已部署且总体运行正常；没有对应日志的情况下不编造运行时长、任务数、故障率或加速比例。同步文档不触发任何现场部署/重启。

## 5. 仍需分别记录的专项验收

| 专项 | 所需证据/边界 |
|---|---|
| 原生答案业务正确性 | 实际任务正文与原始材料对照；正常执行不认证数字/单位/因果全部正确 |
| 编码器 | 正常前进/持续后退/停止/回弹/归零与已知异常对照；过程、离轨、缺口/invalid逐项核验，不能以大后退判故障 |
| 图片 | 代表性原图视觉与人工标注，抽样范围和8/12张全分辨率容量；数量配置不等于视觉/容量PASS |
| 乳头KPI | 命名牛周期、最终2D框、缺失、封顶、分母逐牛对账 |
| 当前资源 | Runtime容器下进程/磁盘/GPU来源逐字段与宿主机对照；静态疑点不等于已确认现场故障 |
| 长上下文 | 原生compaction真实触发、证据保留及overflow恢复；过夜多个短任务不能替代长任务验证 |
| 整批收益 | 同代码/模型/样本/质量比较1路与2路，多次配对；排队并发不等于GPU加速 |
| 生命周期/存储 | 断电不补跑、历史中断、删除/数据包不影响外部源、升级回滚与长期retention |
| 大目录 | 真实耗时、CPU/内存/I/O与覆盖；不宣称任意Shell全局I/O硬限流 |

这些是未取得本轮独立专项结果的范围，不表示用户已观察到运行失败。不无证据重做历史Step6，也不重新引入结果后处理或修改全局预算来“修复”已退出主链的旧问题。

## 6. 工具与后续范围

```bash
python3 -m unittest discover -s tests -v
(cd frontend && npm run build)
```

CI不调用现场GPU或执行端侧部署。TextReportComposer回放只验证历史/独立回放器；当前主链验证应检查新的原生outcome及result。

追问/跨Run上下文恢复改造、设备写动作并发锁、网络诊断、重型点云、完整retention继续后置。新任务推进依据现行[交接](08-local-usage-and-handoff.md)和[12契约](12-native-answers-and-skill-refinement.md)。
