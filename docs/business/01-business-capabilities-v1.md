# 首批业务能力与分析口径

更新：2026-09-14 阶段收口。实现不等于业务验收；当前进度以 `../02-delivery-and-acceptance.md` 为准。

## 1. 分工

OpenClaw + 模型根据用户目标自主选择工具、分析、执行和验证。Skill说明业务对象、数据源、边界、稳定命令和停止条件；脚本负责确定性读取、计算、候选定位，不负责通用自然语言报告或无依据根因。

ScopeX提供任务控制、数据目录/权限、Evidence、审计、Finalizer和Report Composer。不在业务Handler编排固定Workflow。

| 能力 | 主数据源 | 用户要得到的结果 |
|---|---|---|
| system-health | 当前host snapshot | 当前资源实际情况，不猜历史原因 |
| image-quality-diagnosis | 原始JPG | 脏污/模糊/起雾/水珠及可见依据、覆盖限制 |
| nipple-recognition-analysis | CowDisinfect日志牛周期及最终帧 | 总牛数、最终2D框分布、识别率、缺失口径 |
| encoder-health | 编码器日志序列 | 具体毛刺/回退/异常跳变、时间和值，不只是统计表 |
| data-locator / log-context | 语义Catalog、显式日志 | 定位文件/小窗原始事实，不独立诊断 |

网络topology尚不清楚，不进入首批。旧脚本只是历史经验与日志结构参考，不要求照搬，也不是验证真值。

## 2. 数据源和全局路径

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

读取策略见 `02-data-catalog-and-bounded-access.md`。先指定日期/时间窗，再定位，禁止遍历所有挂载目录摸索。工具输出紧凑，完整明细留在 `/task-scratch`，按需读取。

## 3. 当前资源

Run开始前由ScopeX宿主机生成当前快照，挂到 `/scopex-host/current.json`；`system_health.py`把它整理成一份business_facts。Agent回答用户所问资源即可。

没有周期采样/历史数据库；不允许以当前状态解释“昨天7点CPU”。快照失败或字段缺失必须报告不可用，不从Sandbox的free/df/proc/nvidia-smi补造。高利用率只说明利用率，不证明识别变慢/机器人故障。

## 4. 图片脏污/起雾等视觉判断

单图：直接查看用户指定原图，回答后停止；不默认读取邻近JSON/PCD/日志，不临时装包或探测工具。

多图时间窗：

```text
明确时间窗 -> Locator目标小时
 -> 有界时间代表样本
 -> 可选指标分区（仅用于覆盖差异）
 -> 各时间/场景/分区选原图
 -> 小批直接视觉查看
 -> 跨图比较 -> 结论与抽样限制
```

Laplacian、梯度、亮度、对比度、clip ratio只能筛选/参考，不能决定“无雾”“无脏污”。高锐度仍可能有乳白雾膜；低对比也可能仅是场景内容。不得拿指标推翻直接看到的雾化。

视觉维度：整体或局部模糊、乳白/半透明雾化、黑位泛灰、光晕、水珠形状/畸变、固定污迹/擦痕、方向性运动拖影和失焦。跨不同牛/场景仍固定的污迹或雾层支持持续问题，但“凝露/保护玻璃”等物理原因需要额外证据。

每次view_image最多2张，完整请求还受累计图片额度约束；2+2+2在上下文可能是6张。额度由Runtime的 `SCOPEX_MAX_IMAGES_PER_PROMPT` 配置，与vLLM和Finalizer一致，不在Catalog另维护一个上限。omitted/truncated必须重看，不当作已观察。

如果只抽样，只能说“所查看样本未见明显问题”，不能保证整小时没有雾。若任一原图明显异常，报告其时间和特征；其他图状态不同则说明混合/短时/覆盖不足。容量或Context不足先收缩覆盖并说明，不退回用指标代替视觉。

## 5. 乳头识别率

### 业务定义

只计算最终2D乳头检测框 `NippleNum`，每牛固定最多4个；3D有效点、坐标、IsValid、轨迹生成均不参与。

图片/JSON在失败路径可能不保存，因此不能按保存文件数算总牛数，也不能只算成功牛。统计对象是请求时窗内开始命名检测轮的牛周期；完全无周期的物理漏牛需要独立视频/RFID等真值，日志无法补猜。

### 分析过程

```text
请求时间窗 -> Locator显式相关日志文件
 -> 解析检测轮和牛周期
 -> New cow detecte finished / LastImgTimeStamp
 -> 关联最终采用帧的2D NippleNum
 -> 每牛封顶4、保留缺失/过检
 -> 汇总KPI和质量边界
```

一牛多帧2→4→3，最终采用3则算3，不取max，也不相加。牛号复用、重启epoch、轮转重复和小时边界需要验收，不能把日志中的计数器当永不重复的物理牛ID。

```text
理论乳头总数 = 命名牛周期总数 × 4
计入框数 = 所有已关联最终结果的 min(NippleNum,4) 之和
完整四乳头率 = 最终4框牛数 / 总牛数
总体乳头率 = 计入框数 / 理论乳头总数
```

同时输出4/3/2/1/0/缺最终结果分布；缺失保留分母但不伪造0框观察，这是含缺失的保守指标。零牛时比例不可用，不强行写0%或100%。超过4保留raw和过检标记，但封顶不能证明检测准确率；未经人工标注无法得到真正precision/recall。

JPG/JSON只按最终时间戳辅助核对，不默认查看整小时全部文件。解释某段异常时才用小窗日志/原图，不自动串查CPU与编码器。

## 6. 编码器数据异常

### 核心目标

用户要知道是否存在毛刺、回退、异常跳变，具体何时、前后值是什么、增量多大、是否快速恢复。不是展示数千个负增量和全部候选JSON。

优先应用累计值 `EncoderVal [N], TurnTableSpeed [...]`。raw/filtered是底层对照；无应用序列才明确改用raw。没有已验证标定不换算物理距离/速度，也不能用猜测阈值证明硬件故障。

### 分析路径

```text
时间窗 -> Locator -> 一次分析全部显式轮转文件
 -> signed delta + dt + 局部正常变化
 -> 回退分组/恢复/跳变/采样缺口
 -> 输出重要事件及依据
 -> 仅按需补log-context
```

事件分类：孤立明显回退并快速追平；连续回退区间；未确认恢复的单次回退；异常正向跳变；采样缺口。恒值可能是正常停止，小幅负增量只留统计，不自动判异常。

无效采样是断点，不能先删除再算相邻差值；快速恢复、负增量分组和恒值区间不能跨越断点。文件边界可连续处理，但重启/复位/不递增时间应与真实机械回退分开。正跳必须结合dt：长间隔自然积累更多脉冲，不应该仅因delta大就报毛刺；恢复腿不应重复计异常。

### 产品报告与未完成验证

结果先回答有无明显数据异常，再给重要事件的时间、前值/当前/随后值、pulse delta、dt、恢复和相对周围水平。只有“为什么/是否影响业务”需要时，针对具体事件核对raw/filtered、reset、Modbus、停止/真实反转上下文。

当前脚本已有候选算法和invalid断点测试，但其默认阈值不是现场协议规范。dt归一化对不规则采样的误判、重启epoch、重复记录、跨流逐事件传播关系、真实标注召回/误报及大窗口资源占用均仍要验证。不能把这些要求写成已完成事实。

## 7. 事实与用户报告

结构化脚本一份business_facts形成一份Evidence，不拆成几百行；不同Claim可以引用同一份聚合结果。Skill/工具过程是Trace，scratch是Working Data，可保留内部working_derived兼容，但不直接当用户事实。

```text
Evidence -> Fresh Finalizer -> Claims
 -> 无工具Report Composer -> 引用/分类检查
 -> 可读结论、事实依据、可能性、下一步、数据限制
```

模型负责中文表达，代码不是中文字段字典引擎。引用正确仍需复核语义、数字、单位、候选与根因的区分；报告失败保留明确fallback，不伪装全部成功。

## 8. 首批验收

完整一小时乳头逐牛对账；编码器具体候选与原始数值人工核对（含正常反例）；多张正常/轻雾/明显雾/水珠/污迹图视觉标注；资源host来源；报告数字/范围/可读性；任务轨迹无无关目录遍历、无重复preflight失败、有明确停止。单元测试绿不等于这些业务验收通过。
