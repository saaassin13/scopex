# 统一输入、定时任务与评价闭环

同步：2026-09-16，按main@cb90d02。端侧已部署并获用户过夜运行总体正常回执；专项状态见[02](02-delivery-and-acceptance.md)。实现入口为scopex/api/service.py、schedules.py、fastapi_app.py及frontend/src/views/。

## 1. 一个入口、一套Runtime

用户自然输入，不选择聊天或任务。POST /runs创建auto，所有模式共用TaskService、OpenClaw、模型、Skill、工具和审计，无额外Router Model。

是否访问业务用于内部模式分类；原生回答不要求ScopeX固定业务JSON/Evidence schema。正常回答与业务任务走同一原生交付函数，原生失败不能降级成聊天成功。定时创建task。

Stop/Steer/Resume及旧conversation接口保持兼容；运行中追问、完成后追问和跨Run上下文恢复改造暂缓，不因接口存在宣称完整多轮产品已验收。

## 2. 原生结果

```text
OpenClaw调查、判断、回答 -> cli_outcome
 -> ScopeX正文、来源、执行状态、审计 -> Vue
```

不再要求Claims或额外Report/TextReportComposer调用；postprocess_model_calls=0。原生错误、预算中断和截断有可见原生正文时FAILED+partial，没有正文时FAILED+unavailable。框架错误提示不作为草稿；不从任意辅助模型响应抢救成功。完整交付也不认证全部业务含义正确。

version=2结果使用report_text/report_meta/execution_status/investigation_reasons。通常producer=openclaw，已核验Locator无数据结果为scopex_no_data。历史报告兼容展示，不自动改写或重跑。

正文转义展示，不执行模型HTML；源码、内部JSON和工具流水不作为用户主结论。未知引用提示核验，不伪造链接。

## 3. 简单Schedule与并发准入

支持名称、普通任务内容、每N分钟/每日HH:MM/一次执行、启停和立即执行。Scheduler只创建普通task，不选Skill或编排流程；相对窗口用scheduled_for，数据时区来自Catalog，不猜UTC。

默认2活动、16等待、600秒排队；排队不准备Runtime/快照，获得名额后执行。暂停保留名额。在线可排队，同一schedule已有活动/等待项则跳过新触发；容量与队列都满时明确busy，不无限积压或改变统计窗口。

离线错过全部跳过、推进到未来，不逐个补建历史Run。once过期停用，interval保持相位，daily跳到未来；立即执行不改变原周期。重启旧排队项过期，其他未完成项记中断，不自动重做业务动作。

固定窗无数据应结束，不扩窗；已知Locatorno_data在现有请求边界硬结束，其余工具零样本目前仍为指令约束。读取错误和源不可用不冒充无数据。

## 4. 活动、日历与定时历史

全局/activity不受所选日期限制，显示活动/等待/暂停及排队位置；不可达显示未知，不虚构GPU进度。月历与当天Run列表只读任务元数据。

定时名称/执行历史进入/schedules/:id/history，按schedule_id跨日期查询，最新优先，每页50条，包括立即执行记录。GET /tasks支持schedule_id、limit(1..200)、offset，先过滤后分页；当前仍为文件枚举，没有磁盘查询索引。

详情记录计划、开始、结束、执行/准入等待/总耗时，可返回对应定时历史。页面路由是前端路径，不是独立调度引擎。

## 5. 删除、评价和导出

仅终态Task可删除ScopeX拥有的audit/work/正文/评价/导出/数据收集资产，不删除外部日志、原图、JSON、PCD或Schedule。暂停不是终止；不能批量prune未知旧容器。

评价保存正确/有问题、标签、备注，不自动改变Skill/Prompt。review ZIP保存存在的task/session/evidence/result/report/meta/events/evaluation/技术错误及历史兼容资产，默认不包含整份业务源或密钥。

原始数据另有显式收集与下载入口，默认源内容2GiB/5000文件，缺失、变化、截断要记录；有限数据包不能宣称完整源数据。收集与同任务删除互斥，收集包随任务资产删除而不影响原始源。

## 6. 验证边界

已部署过夜运行回执保留；业务语义、长上下文、并发收益、重启/删除边界的专项结论分别记录。旧TextReportComposer回放不验证当前原生主链。历史开发记录见固定版本的12文档，现行说明见[12](12-native-answers-and-skill-refinement.md)。
