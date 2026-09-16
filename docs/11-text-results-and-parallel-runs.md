# 独立任务并发、活动入口与整批测量

现行同步：2026-09-16，main@cb90d02。本文保留2026-09-15批准的独立并发与批次验收方法。**当时的一次TextReportComposer输出方案已被原生回答替代**，不是现在的默认主链；[原文固定版本](https://github.com/saaassin13/scopex/blob/cb90d028773b4fac4eabf4b429ecb3fed97f1c51/docs/11-text-results-and-parallel-runs.md)仅历史参考。

## 1. 当前执行与结果边界

OpenClaw继续拥有调查、决策、执行、验证、停止和回答。ScopeX队列只管准入，不编排业务步骤。结果直接保存原生CLI outcome，postprocess_model_calls=0；不追加Claims或报告模型调用。具体状态规则见[12](12-native-answers-and-skill-refinement.md)。

已部署且用户回执过夜运行总体正常，不代表整批并发加速已经专项验收。追问、运行中提问和跨Run上下文恢复改造仍暂缓；旧控制接口保留。

## 2. 并发、队列和恢复

产品CLI默认--max-active-tasks 2、--max-queued-tasks 16、--queue-timeout 600；活动名额1..4。直接构造TaskService时的单槽位/无队列默认保留为兼容，不是产品CLI未接线。

edge Compose将单次调查`--timeout`覆盖为1200秒，并将Runtime优雅停止时间设为1220秒；队列等待仍为600秒，vLLM并发参数不随任务时长调整。

各任务独立agent/session/Runtime/Scratch/Evidence/审计，模型请求没有全局串行锁；同一vLLM负责GPU调度，不复制模型、不混Prompt。当前并发仅支持隔离Sandbox执行，设备写动作并发锁不在本轮。

队列满明确拒绝，排队可取消/超时，排队不构建Runtime、不采快照。历史相对窗按created_at/scheduled_for；当前资源按执行采样时间。暂停保留逻辑名额，不宣称已实现释放/恢复排队。

同一schedule已有活动/等待项时跳过新的触发；在线其他任务可有限排队。离线错过不补跑，重启前排队项过期，其他未完成项记中断，不自动执行旧动作。一个data-root只有一个API进程，不运行多个uvicorn worker共享状态。

/activity提供不受日期限制的实时元数据，不扫描业务Evidence。页面显示活动/排队位置/耗时，模型处理中包含服务内部等待，不声称知道GPU百分比进度；断线显示未知。

## 3. 端侧配置与更新入口

现行操作只使用[deployment.md](deployment.md)和deploy/edge/compose.yaml，不照历史宿主机命令另起服务。ScopeX/vLLM均容器化，restart均no；文档同步不重启它们。

程序默认主动/完成后compaction关闭，但edge Compose已经显式--enable-compaction。图片用edge的SCOPEX_IMAGE_LIMIT默认12同时传给模型和ScopeX，数量额度不是全分辨率容量验收。

仓库vLLM配置max-num-seqs=1。ScopeX2路准入不证明模型批处理收益；若要调整模型服务，先按运行资产核对、保存参数、安排维护，不能为同步文档顺手改为2或4。

## 4. 原生主链与历史回放不要混测

`scripts/replay_text_report.py`可在独立新目录对保存Evidence执行一次无工具报告请求，保留旧任务，图片需显式只读bind；它是历史/独立回放器，**不用于证明现在原生答案主链正确**。

验证当前结果应检查实际任务原生CLI outcome、version=2正文/状态和postprocess_model_calls=0。不能以回放成功覆盖旧失败，也不能把辅助压缩调用的finish=stop当原生任务成功。

## 5. 整批耗时对照

同一版原生输出代码、相同模型配置、固定数据和绝对时间窗；暂停相关定时任务并控制其他负载与预热条件。在获准维护中按deployment.md调整ScopeX活动名额1与2，保持其余参数相同。不能把更换结果链少一次模型调用的收益算作并发收益。

在本机创建cases.json，不提交现场数据：

```json
{"read_only":true,"dataset_id":"固定现场样本标识","cases":[
 {"id":"encoder","message":"分析2026-09-11 12:00至13:00编码器运动与具体数据异常，只读调查。"},
 {"id":"nipple","message":"统计2026-09-11 14:00至15:00最终采用帧2D乳头识别率，只读调查。"}
]}
```

日期须对应实际已确认覆盖的数据。以下仅计时客户端命令，在能够访问本机API且具备Python依赖的环境运行，不是宿主机Runtime启动命令：

```bash
python3 scripts/benchmark_task_batch.py --cases cases.json \
  --expected-slots 1 --out .local/benchmark/serial-01.json --execute-read-only
# 完成本批并经获准操作改为2活动名额后：
python3 scripts/benchmark_task_batch.py --cases cases.json \
  --expected-slots 2 --out .local/benchmark/parallel-01.json --execute-read-only
python3 scripts/benchmark_task_batch.py \
  --compare .local/benchmark/serial-01.json .local/benchmark/parallel-01.json
```

edge API只绑定VPN IP；历史计时工具限制loopback。不要为跑计时工具放宽API或工具安全边界；客户端的本机可达路径须按实际部署准备，未经现场核对不把这些示例称为直接可运行的端侧验收。

整批从首次提交到最后完整结果，包含排队/推理/工具/交付。失败、缺失或partial不算加速成功。默认只输出计时观察；人工核对范围、单位、缺失、原图和生产影响后才用--quality-confirmed。至少3次配对，不同数据/代码不可比较。

2路无收益如实记录NO_SPEEDUP，4路不默认开启。“能同时运行”及“过夜运行正常”都不是已测得加速百分比。
