# 首批业务能力与分析口径

同步：2026-09-16，按main@cb90d02的原生回答与运动过程分析。用户已确认端侧部署并过夜运行总体正常；具体专项状态见[交付验收](../02-delivery-and-acceptance.md)。本轮仅同步文档，不变更Skill或算法。

## 1. 分工与数据

OpenClaw + 模型自主选择工具、调查、判断、执行、验证和回答。Skill说明对象、语义、源、边界与稳定命令；脚本确定性读取/计算/筛查，不无依据认定根因。ScopeX提供权限、任务控制、Evidence、审计和原生结果保存，不编排第二套Workflow或默认追加报告模型。

```text
cowdisinfect_logs
  host: /opt/ScalingRobotics/CowDisinfect/Log
  agent: /agent-data/logs
  CowDisinfect-YYYYMMDD-HHMMSS.log[.N]
left_camera_multimodal
  host: /opt/ScalingRobotics/CowDisinfect/GrabbedImages/LeftCamera
  agent: /agent-data/left-camera
  YYYYMMDD/HH/YYYYMMDD-HHMMSSmmm.jpg|json|pcd
```

语义唯一配置为config/data-catalog.json。先定位明确日期/时间窗，禁止遍历所有历史根；完整工具明细放task scratch，按需有界查询。edge业务目录挂载与Catalog host_path需同时配置，时区根据数据来源确认，不猜UTC。

## 2. 当前资源

目标是本次宿主机状态。准入后ScopeX调用快照采集，工具入口为/scopex-host/current.json；system_health.py整理输出。没有周期历史库，不以当前快照解释昨天的资源原因；高利用率本身不证明业务故障。

当前Runtime运行在容器中，host_snapshot.py在自身进程环境读取proc、进程、磁盘并调用GPU/Docker工具。进程/挂载/GPU等具体来源范围需与宿主机现场逐字段对照；不能仅凭source标签认证完整宿主机视图。缺字段如实报告，不从工具Sandbox补造。这是保留的专项核验，不是已确认现场故障；本次不改采集器、权限或部署。

## 3. 图片视觉判断

单图直接看用户指定原图，答完即停，不默认查邻近JSON/PCD/日志或安装工具。时间窗任务先Locator有界定位，直接看少量时间代表样本，为必要邻近复核保留额度。

指标仅在能改变筛选决策时使用，不默认先跑help/metrics；亮度、Laplacian、clip ratio不决定有无雾。结合可见退化、影响区域与跨场景特征区分污物、水滴、雾化、运动模糊、失焦等解释；看到雾化不证明冷凝或保护玻璃物理原因。

Locator按实际时间网格选邻近文件，密集段不挤掉中间时段，空档不重复取同图填额度。每次view_image最多2张，完整Prompt累计受SCOPEX_MAX_IMAGES_PER_PROMPT约束。未附加/省略的图片不是已观察；抽样不保证整小时正常，多个样本重复出现不证明全程连续。

最终表达由调查Agent直接完成。原图身份保留在审计，不再为第二次报告模型重开/重新附图。历史Finalizer回放的附图规则仅用于其独立路径。

## 4. 乳头2D识别率

从日志确定请求窗口内开始命名检测轮的牛周期，最终LastImgTimeStamp关联帧的NippleNum作为计数。每牛最多4，超过4留raw/过检标记并封顶；不采用3D IsValid，不取多帧max或相加。

```text
理论乳头总数 = 命名牛周期数 × 4
计入框数 = 已关联最终结果的 min(NippleNum,4) 之和
完整四乳头率 = 最终4框牛数 / 命名牛周期数
总体乳头率 = 计入框数 / 理论乳头总数
```

同时给4/3/2/1/0/缺最终结果分布。缺失保留分母但不伪造0框观察；说明是含缺失的保守统计。零牛时比例不可用，不强写0%或100%。图片/JSON可能在失败时不保存，不能用文件数作总牛数。

牛号复用、重启epoch、轮转重复、小时边界需逐牛核验；物理漏牛和真正precision/recall须独立真值。JPG/JSON只按最终时间戳辅助，解释异常才小窗核对，不自动串查无关系统。

## 5. 原始 raw 编码器数据（2026-09-20 本地改造，未部署）

目标默认是“是否存在 raw 数据异常”，确认一处即可结束后续深挖。只分析 raw 数值，其他日志用于解释复位/通信等上下文；filtered 和应用 EncoderVal 不作为测量或替代来源。正转、反转、启动、停止均允许。

产品工具采用 scan → inspect → report，schema 6，具体命令和边界以 [当前契约](../12-native-answers-and-skill-refinement.md#5-编码器raw-only-schema-62026-09-20-本地改造未部署)及 [Skill](../../skills/encoder-health/SKILL.md)为准。任意已覆盖区间可查看，不限于预先生成的 R 候选；候选总数不是异常数。

局部速率偏离和持续变化是筛查结果。是否确证由有来源的设备范围/速率条件检查给出，无相应配置时形状疑点保持未决；不以模型感觉或出现次数确认异常。报告明确已确认、未决、未查看状态和原始来源。无 raw 不回退应用数值；缺失时段不生成运动事件；负数与大整数不默认当无效码。

程序性回归与真实日志试跑验证计算/访问链，不代替现场误报漏报、实际端侧模型与真实故障标签验收。慢漂移、比例误差和无法区分的物理根因保留边界。历史 schema 5 为此前现场版本，本地重构未自动发布。

## 6. 日志和无数据

log-context使用明确文件位置参数、精确小窗和关键词；默认40行/6000字符，可显式1024–12000，保留完整raw/line/time，截断要标记。高频窗口起点前N行不保证覆盖目标；编码器优先查保存过程，而非反复缩窗倾倒原始日志。

固定窗无数据应结束，不扩窗/换源/猜时区；源不可用和读取错误分别说明。硬结束目前只覆盖已知Locator可核验no_data，其他工具零样本是指令约束，不宣称全部硬拦截。

## 7. 事实、结果与验收

业务结构化投影只压缩空白，不按字段白名单删语义；超预算明确capacity_exceeded/working_derived，完整工具记录留审计。工作材料不能仅因有E编号成为事实。

```text
OpenClaw原生调查和回答 -> cli_outcome
 -> ScopeX正文、状态、来源和审计 -> 用户
```

没有默认Claims或额外报告模型关卡。预算/原生错误/截断不能因文字非空变成功；FAILED+partial/unavailable分别记录。来源身份不是语义证明，正常执行不证明数字/单位/因果全部正确。

已部署过夜运行回执保留；编码器正常反例与真实异常、乳头逐牛分母、原图判断/覆盖、资源来源、长上下文和整批收益分别验收。本次只修正文档，不重做现行结果链或修改现场服务。
