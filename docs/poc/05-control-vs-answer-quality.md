# POC05：控制能力与答案质量分离

## 结论

POC05 的原始验证目标是交互控制链：

- runtime progress 可见；
- 用户 Stop 在安全 tool-round boundary 确定性生效；
- Stop 后不再转发新的模型请求；
- Resume 使用同一 session；
- Stop 前的 tool results 被保留；
- Resume 可重新指定调查优先级；
- 恢复后真实读取新的数据源；
- Investigation 结束后可进入 Fresh Finalizer，并自然 `finish_reason=stop`。

这些属于 **Control Plane**。

最终诊断文字是否严格遵守证据强度，例如：

- `status=137` 不等于 OOM；
- 不能给 OOM / 资源竞争 / 内部错误做无证据排序；
- robot.log 只能说明当前日志窗口未见异常，不能全局排除机器人原因；
- 时间相关不能直接写成已证明因果；

属于 **Answer Quality / Evidence Calibration**。

两类能力必须分别验收。不能因为答案质量失败而否定已经有 wire/tool 证据证明的 Stop/Resume 控制能力，也不能为了让控制 POC 通过而忽略答案里的过强推断。

## 当前 POC05 结果

- Control Plane：应独立重判。
- Answer Quality：当前 strict finalizer 暴露了证据强度问题，继续保留为 FAIL，不修改历史 audit。

## 后续

下一项验证应使用结构化 claim/evidence 输出，减少对自由中文正则的依赖。建议把 evidence calibration 作为独立 POC，而不是继续扩张 POC05 范围。
