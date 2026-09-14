# 当前阶段与下一步

更新：2026-09-14 PR #14 阶段收口，main 为唯一当前集成基线。详细接手见 `../08-local-usage-and-handoff.md`；本文件不重复维护多份部署参数。

## 1. 不变的边界

**OpenClaw owns execution. ScopeX owns product control and trust.** 不实现第二套 Agent Loop / Workflow Engine。业务脚本是确定性原语，Skill 提供业务语义/使用说明，模型决定实际调查、执行、验证和停止。

Step 6A–6D、6F 冻结 PASS，6E CAPABILITY PASS；旧综合任务 371.1s/14requests 只属于原 Gate，不是所有新任务的承诺。不无证据重做已过 Gate。

## 2. 本次集成范围

统一输入、内部 conversation/task、同 Runtime；简单 Schedule、离线不补跑；当前 host snapshot；六个默认 Skill；Data Catalog + Locator；Evidence/Trace/User Facts 分层；Fresh Finalizer + Report Composer；月历/当天记录、耗时、删除、评价、复盘导出。

```text
用户 / 定时
 -> TaskService -> OpenClaw + 模型 + Skill/Tool
 -> 业务依据 -> Fresh Finalizer -> Validated Claims
 -> 一次无工具 Report Composer -> 引用/分类校验 -> 可读报告
```

确定性 ProductAnswer 是 fallback，不作为增加业务字段的中文模板引擎。原始工具/源码/Evidence JSON 不直接等于用户事实。

当前仅一个主要执行槽位，在线 busy=SKIPPED_BUSY。没有新增资源历史采样器，也没有主动做网络诊断。

## 3. 已完成的失败修复

- Locator Catalog 从错误的 Sandbox 根映射假设改为 Skill references 相对路径，并在 Runtime 启动同步。
- invalid raw 保留断点，恢复/恒值区间不能跨断点计算。
- structured business facts 允许不同事实共用同一 E，不再错误 duplicate_claim。
- 图片每调用2张与完整 prompt 累计容量分别管理；额度在请求策略/Runtime/Finalizer统一。
- 图片窗口超预算使用时间分散抽样，不把前半窗当整小时。
- Report 主路径接入真实 factory/coordinator/persistence/UI。
- release systemd 的可变状态移到版本目录外，去掉旧的泛化业务根占位挂载。

修复存在不等于正确率全 PASS。最近真实包的具体证据见 `../reviews/2026-09-14-runtime-failure-replay.md`。

## 4. 下一阶段最小验收顺序

### A. 先确认现机配置

main 最新提交、工作区是否干净、OpenClaw实际版本、vLLM镜像/快照/完整Cmd。最后确认模型支持4图，12图切换和小图探针没有成功回执，不能假设完成。ScopeX环境变量与vLLM一致，先过容量探针。

### B. 新建真实任务，验证完整报告

当前CPU/内存、指定时间窗编码器、指定时间窗图片。必须检查 `result.report` 及 `report_meta`，不只看状态COMPLETED。

- 资源：host而非Sandbox，当前而非历史。
- 图片：直接看代表原图，指标只能筛选；抽样范围明确，负结论不外推；人工复核起雾/水珠/污迹。
- 编码器：具体事件的时间、前后值、signed delta、dt、恢复和连续性；局部正常水平与reset等候选解释分开，不把事件数量写成脉冲/硬件故障数量。
- 乳头：完整一小时人工对账牛周期、最终采用帧、2D框、每牛4、缺失与0、分母和小时边界。

### C. 产品与恢复验收

可读结论/事实/可能性/下一步；失败阶段与报告降级；日历/日期/耗时；评价和复盘；终态删除不触及原始数据；定时离线不补跑；启动后状态持久；弱网/离线包及回滚。

### D. 测资源，再扩并发

先测大日志时间/CPU/峰值内存、图片请求字节与token预算，识别真实瓶颈。之后再讨论最多约2个运行任务、在线排队/同Schedule合并、计划时间不漂移、设备写动作互斥。离线历史始终跳过，不补跑。当前不实现无限并行。

## 5. 明确仍未完成的部分

模型语义/数值校验不是通用引用validator已经解决的问题；没有逐事件raw/filtered传播关系的真实验收；大窗口算法不是已证明常量内存；任意Shell根扫描尚无全局I/O硬guard；HTTP请求4MiB和模型Context仍限制多图；npm lockfile未入库；旧Sandbox命名/清理及非终态重启状态恢复需检查；网络topology与独立漏牛真值未知。

## 6. 合入与后续工作方式

本次用户明确要求将代码/文档清理合入main以便新会话继续。通过仓库回归后作为**集成阶段基线**收口，未完成的真实业务验收留在交接文档，不能为了符合旧Draft说明冒充现场PASS。

后续从main另开小分支，每次先重现、修最小问题、跑对应回归，再做真实数据验收；不要在一个长会话/分支持续无边界扩展。handoff只在大阶段收口时更新。
