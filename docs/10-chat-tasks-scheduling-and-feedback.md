# 统一输入、定时任务与评价闭环

更新：2026-09-14，Product V1已整合，真实页面和重启验收待完成。当前实现文件：`scopex/api/service.py`、`schedules.py`、`fastapi_app.py`、`frontend/src/views/`。

## 1. 一个用户入口、一套Runtime

用户自然输入，不选择“聊天还是任务”。统一`POST /runs`内部auto，始终使用相同TaskService、OpenClaw、模型、工具、Skill和审计。

没有业务访问且正常回答可完成为conversation；一旦调查业务数据/能力，必须满足业务依据和结果链。业务失败不能降级聊天绕过证据。定时一开始即task。兼容旧API不等于另起执行引擎。

多轮API复用OpenClaw session，每轮单独Run记录；UI的多轮体验及从聊天转业务的全过程仍应实测，不因为后端方法存在就宣布完整聊天产品验收通过。

## 2. 任务结果

```text
业务Evidence -> Fresh Finalizer -> Validated Claims
 -> 一次无工具Report Composer -> 报告引用/分类校验
 -> 结论 / 人话事实依据 / 可能性分析 / 下一步 / 数据限制
```

不继续维护按业务字段翻译的主输出路线。内部Evidence编号/原始JSON/脚本和工具过程在原始依据与技术记录内，不能成为用户主要内容。Report失败保留降级与deterministic fallback；历史记录不自动重写。

任务同时记录开始、结束、持续时间、触发方式、计划时间。调查未结束、Finalizer失败、报告表达失败是不同阶段，不统称“解析失败”。状态成功不证明业务含义正确。

## 3. 简单定时配置

只维护名称、普通任务内容、周期/时间、启停。支持每N分钟、每日HH:MM、一次执行、立即执行、上次/下次执行和状态。

例如：检查当前磁盘；检查过去30分钟编码器；检查过去30分钟图片。Scheduler到点只调用普通Task，不选择Skill、不编排A/B/C流程。相对时间窗依据scheduled_for，不由模型猜测“现在”。不新增CPU周期采样或资源历史库。

## 4. 断电与忙碌

离线历史触发全部跳过：missed_count、last_missed_at，推进到未来时间，不生成历史Run。once过期停用，interval相位保持，daily跳到未来日期。立即执行不改变原周期。

目前单槽位在线busy=SKIPPED_BUSY，无在线队列。之后可能引入约2任务并发和有界在线排队/合并，但设备离线历史仍然不补跑。前一任务长时间占用不得无限积压。

Schedule配置持久化不等于所有非终态Task都能跨重启恢复；重启后的历史Run只读和异常终止状态需要专项验证。

## 5. 月历与删除

首页月份日历显示每日执行数量及状态，点击日期展示当天Run，再进入详情。日历只读ScopeX任务元数据，不扫描业务目录。时间以设备时区为准，时区/时钟需现场检查。

终态Task删除其ScopeX audit/work/报告/评价/导出资产，不删除`/agent-data`原始源，不删除Schedule。运行/暂停/Finalizing不允许直接删除；先正常终止。精确任务/容器归属检查仍需对旧命名残留实测，不得批量prune未知容器。

## 6. 评价与导出

正确/有问题，错误类型与备注保存为evaluation.json；评价不自动修改Skill或Prompt。

review ZIP包含存在的task/session/claims/evidence/result/answer/report/meta/events/evaluation/错误记录。默认不打包外部大日志、原图、模型或secret。缺原始材料时只能复盘执行链，不能假装重新验证图片内容。

更强模型复盘时区分Model、Skill、Tool、Runtime、Evidence、Finalizer、Report、UI，给可复现的最小改动与回归，而不是静默重做业务诊断。

## 7. 接下来的验收

统一输入普通咨询与业务任务；自然中文报告及原始依据；计划时间/日历/耗时；立即执行周期不漂移；断电错过不补跑；删除只影响任务资产；评价保存和复盘包可定位失败；报告降级不谎报业务成功。并发与网络仍后置。
