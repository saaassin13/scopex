# 数据目录与有界访问

更新：2026-09-14 阶段收口。Catalog是语义说明，不是全盘文件清单，也不是一个新的数据平台。

## 1. 唯一配置与真实布局

`config/data-catalog.json`：

| source | 宿主机目录 | Sandbox逻辑目录 |
|---|---|---|
| cowdisinfect_logs | /opt/ScalingRobotics/CowDisinfect/Log | /agent-data/logs |
| left_camera_multimodal | /opt/ScalingRobotics/CowDisinfect/GrabbedImages/LeftCamera | /agent-data/left-camera |

日志：`CowDisinfect-YYYYMMDD-HHMMSS.log`及`.1/.2/...`；主程序按小时保存，文件组可能从非整点开始。

多模态：`YYYYMMDD/HH/YYYYMMDD-HHMMSSmmm.jpg|json|pcd`，相同stem用于辅助关联；失败时JPG/JSON可能不存在，不是牛总数来源。

## 2. Runtime接线

```text
config/data-catalog.json
 -> Runtime语义摘要 -> OpenClaw任务上下文
 -> host path存在则只读挂载
 -> workspace/skills/data-locator/references/data-catalog.json
 -> Locator脚本相对references路径读取
```

workspace根另保留宿主机可见的`scopex-data-catalog.json`副本，但不能假设它会成为Sandbox `/workspace/scopex-data-catalog.json`。真实副本在Runtime启动后产生，测试只用临时目录。修改仓库后需重启Runtime同步Skill，不能靠手改副本长期维护。

`--data-dir HOST:AGENT`可追加/覆盖同一个target；不默认根挂载整个宿主机。声明存在与实际可读性要分开，数据源缺失就说明缺失，不扫描其他未知目录补猜。

## 3. 日志Locator

请求使用明确`[start,end)`。非递归扫描Log目录名称，按文件组起点建立候选覆盖区间：`[group_start,next_group_start)`，选取重叠组和其轮转文件。

文件名区间是定位依据，不是精确采样覆盖证明。最终脚本按日志行时间过滤；日志丢失、末组范围、跨重启/轮转重复、小时边界需用原始数据核对。不能只根据“文件存在”说数据完整。

Catalog当前最多48小时桶、单次32文件。返回`files_truncated=true`时，日志任务应缩窗/分段明确合并，不能拿前32个文件冒充完整统计。

## 4. 图片/JSON/点云Locator

直接进入请求日期/小时目录，不递归扫描历史树；按文件名时间戳过滤，再按kind选择。当前普通多模态查询预算24小时桶、最多256文件；大于返回额度时使用分散时间抽样，保留总数/截断标志/抽样方式。

图片业务通常只请求小规模代表路径，再按时间/场景或可选指标分区选原图。抽样只减少重复工作，不保证检测所有短时异常，报告要声明覆盖范围。

PCD目录语义建议单次最多8文件；当前这个Catalog字段主要是访问指导，不能把它误称为已经实现的任意PCD命令硬限制。实际点云能力/独立内存配置尚待开发验收，严禁默认把全部PCD加载到内存。

## 5. 图片数不是文件数

Catalog管理文件定位预算，**模型图片附件额度只有 `SCOPEX_MAX_IMAGES_PER_PROMPT` 一个来源**。不在Catalog重复保存旧`max_claim_images=4`，避免与12张模型配置冲突。

该变量默认4，与vLLM已确认配置对齐；12须先更新服务并过探针。每view_image最多2张，完整prompt的历史附件也要计数，Finalizer重新附图同样受总数限制。不得静默删历史或丢图片来伪造任务完成。

小图数量探针通过不代表真实分辨率下Context/HTTP4MiB/内存足够。原图SHA校验用于身份一致性，不证明视觉模型理解正确。

## 6. 分层后的证据

Trace：Skill、源码、Locator、工具过程/错误、模型请求。
Working：task-scratch里的临时脚本和中间结果。
Internal：有界working_derived兼容原Step6大数据Finalizer，不等于原始业务观察。
Claim-grade：原始业务日志/图像/host快照/稳定结构化business_facts。
User Facts：基于已支持Claims写给用户的中文事实，不直接枚举EvidenceCatalog。

stdout紧凑，一份统计成为一份Evidence，重要事件保留时刻、数值和来源。完整详情放scratch；模型仅按需读取。控制结构/引用不能代替业务语义核对。

## 7. 当前保护的实际范围

已实现：语义摘要、确定性时间定位、有界返回、只读数据挂载、Sandbox CPU/内存/PID/exec timeout。运行期无网络，禁止临时pip/apt/npm。

使用规范：不默认 `find /agent-data`、`grep -R`、`du -a`、`rg --files` 扫根，不调查无关图像/日志/PCD。现阶段没有覆盖任意Shell写法的全局I/O guard，不能宣传“模型不可能扫描”。

脚本逐行读取大日志不代表整体常量内存：编码器会保存所选窗口序列等中间结构。下一阶段要测真实窗口的峰值RSS、CPU、I/O和超时；不以提高全部任务内存替代算法评估。并发在这些实测后再做。

## 8. 验收与维护

测试覆盖Catalog路径/provisioning、非整点组选择、目标小时访问、分散抽样和模型累计附件；真实验收另外核对缺失/重复/边界、文件数量、大目录耗时和报告覆盖声明。

新增数据源先补含义、host/agent映射、命名、时区、索引/时间选择、访问预算和不能推出的结论，不启动全盘索引服务。日志根文件数量实证成为瓶颈时才加轻量索引。
