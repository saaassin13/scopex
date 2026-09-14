# 交付与验收状态

更新：2026-09-14 阶段收口。`main` 是当前集成基线；本次用户明确要求合入并交接，**不是宣告所有业务 Gate 已通过**。

## 1. 状态用语

- 冻结 PASS：原 Step 6 的既定验证结论，不扩大范围。
- 仓库回归 PASS：当前提交在指定测试环境通过单元/集成模拟和构建。
- 已实现 / 待验收：存在代码，但需 Spark + 模型 + 原始业务数据验证。
- 未实施：仍为设计方向，不能写成能力已完成。

## 2. 冻结能力

| 能力 | 状态 |
|---|---|
| Runtime MVP、Stop/Resume/Steering、Evidence 校准 | 原基线 PASS |
| 6A Context / Compaction | PASS |
| 6B Large Data / Multi-Image | PASS |
| 6C Hard Budget、6D Native Loop | PASS |
| 6E Complex Task | CAPABILITY PASS |
| 6F 600s / 16-request | PASS |

不无证据重做 6A–6F；新增业务、报告、容量配置并不自动继承业务正确率 PASS。

## 3. 本轮已有直接证据

收口前 GitHub Actions 使用 `94d34e000aa6464d06639df68206c677b198b1b0` 分支的 PR 合并树：

- Ubuntu 24.04 / x86_64 / Python 3.12：**519 tests，28.056s，OK**；
- Python compileall：通过；
- Node 22.18.0 / Vue 生产构建：通过；
- 该第一轮 workflow 的独立 whitespace 步骤因 shallow checkout 缺 `HEAD^` 失败，不是单测失败；随后配置修正为 fetch-depth=2，最终结果以 PR #14 Checks 为准。

此记录指向 Actions run `34858985419`，不把整次首次 workflow 误记为成功。最终收口提交及 main 的 verify workflow 是合入后的持续证据。

前述测试不调用真实 GPU 模型，也不验证工业现场图像/编码器语义，不可当成 Spark 业务验收。历史本地专项/复盘回放仍保留在专项 review 文档。

## 4. 已整合功能

统一 `/runs` 输入、同 Runtime 内部结果策略；当前宿主机快照；六个默认 Skill（含 Locator/上下文支持）；有界目录定位；Evidence/Trace 分层；Report Composer 主路径和 fallback；任务时间；简单 Schedule；离线 missed 不补跑；月历/当天记录；终态删除；评价与 review ZIP。

正式主链：

```text
TaskService -> OpenClaw -> 业务依据
 -> Fresh Finalizer -> Validated Claims
 -> 无工具 Report Composer -> 引用/分类校验 -> 人话报告
```

单槽位在线 busy 仍 SKIPPED_BUSY。无资源历史 collector、无 Router Model、无额外业务 Workflow。

## 5. 最近两个真实失败的状态

| 任务 | 已定位事实 | 修复状态 | 未完成 |
|---|---|---|---|
| 编码器 de435616a790 | 调查完成，两个不同聚合事实引用 E1 被 duplicate_claim 拒绝 | 聚合事实身份校验已修，原包回放及回归覆盖 | 新任务报告、业务措辞/事件人工核对 |
| 图片 52fcca3534ca | 三批共 6 张，完整 prompt 超过服务 4 张上限，HTTP 400 | 全链路额度统一及容量探针已实现 | vLLM 改 12 的现场回执、原图/token预算、视觉正确率 |

来源见 `reviews/2026-09-14-runtime-failure-replay.md`。不要归咎“模型不会识别”或“用户 Prompt 不好”来替代已确定的链路原因。

## 6. Spark 后续 Gate

| Gate | 验收方法 | 当前状态 |
|---|---|---|
| 模型容量 | 实际 vLLM 参数、ScopeX env 相同；小图探针 accepted=true | 最后确认 4，12 待回执 |
| 实际图片 | 有代表性原图视觉判断，人工复核雾/水珠/污迹；不拿指标否定起雾 | 待验收 |
| 编码器 | 时间/前后值/delta/dt/恢复、连续回退、invalid/reset 边界逐事件核对 | 待验收 |
| 乳头 KPI | 一小时牛周期/最终帧/2D框/缺失/封顶/分母人工对账 | 待验收 |
| 当前资源 | 使用 host 快照，不读 Sandbox 冒充；无历史分析 | 待现机复核 |
| 人话报告 | result.report、事实说明/可能性/下一步，数字单位与原证据一致 | 待人工验收 |
| 产品交互 | 日历、开始结束耗时、评价/导出、删除不影响原始业务数据 | 待当前页面验收 |
| 故障与恢复 | 无效 JSON/图像超预算/服务重启给出明确失败；历史定时不补跑 | 代码测试覆盖，现场待验收 |
| 大目录 | 不递归历史根；日志无截断冒充全量；测执行时间、CPU、内存和 I/O | 待实测 |
| 离线/升级 | ARM64 镜像+wheelhouse+权重+OpenClaw；版本切换保留运行数据，可回滚 | 待完整 smoke |

默认 Sandbox 512 MiB 不代表重型 PCD 足够；脚本逐行读取也不代表整个算法常量内存。

## 7. 当前部署事实

MODEL_REPO、revision、NGC 镜像已由用户的现机 inspect 补录，不再是未知：详见 `09-zero-to-one-build-and-offline-deployment.md`。12 张属于待验证配置；当前 OpenClaw 精确版本、原 Compose 管理入口仍待补录。

systemd 模板使用 release 外固定 state 目录，去掉旧的泛化 `/agent-data` 根挂载占位符。模板变更不会自动迁移 Spark 现有开发 `.local` 数据，切换前必须按部署说明备份/迁移。

## 8. 保留的风险与技术债

结构/引用校验不是自然语言语义保证；Report 数字、单位、因果和抽样范围需实测。业务指标→报告的传递不得再以逐字段映射扩大维护面。

图片额度不等于 token/HTTP body/内存额度。大目录任意 Shell 的 I/O 硬拦截、编码器逐事件跨流关联、非终态断电状态恢复、旧 Sandbox 精确清理、审计 retention、npm lockfile、完整 Device Base 离线交付尚未全部完成。

并发/队列/设备动作锁、网络 topology 和无周期漏牛真值继续后置；不能在本轮报告尚未验收时直接并行扩大负载。

## 9. 复测命令

```bash
python3 -m unittest discover -s tests -v
(cd frontend && npm run build)
```

`.github/workflows/verify.yml` 自动做完整仓库测试、前端构建和卫生检查，不负责现场部署。新会话按 `08-local-usage-and-handoff.md` 顺序进行最小真实验收，不先修改算法阈值或全局预算。
