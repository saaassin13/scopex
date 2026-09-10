# POC01 交付前脚本校验记录

日期：2026-09-10。环境：Linux x86_64，Python 3.13.5。**不是用户的 Spark，没有运行用户的 vLLM 或真实模型。**

## 已实际执行

```bash
python3 -m unittest discover -s tests -v
bash -n scripts/collect_env.sh
python3 scripts/poc01.py --help
python3 scripts/poc01.py --suite .local/smoke init
bash scripts/collect_env.sh
```

测试结果：`Ran 28 tests ... OK`。样本初始化生成8个用例；环境采集在本地生成文件，不上传。测试时长仅用于工具校验，不是模型任务速度。

## 测试覆盖

[test_poc01.py](../../tests/test_poc01.py) 覆盖：严格JSON评分（类型、顺序、重复键）、direct/tool同内容对照、空结果、工具证据检查、两次读取与列目录、错误工具后恢复、推理字段回传、重复调用/轮数/调用数限制、输出截断、协议异常、路径越界与符号链接、文件大小上限、真值不暴露给工具、双图实际base64及交换顺序、任务硬超时、中断落盘、报告分母与未运行项目、回环地址和请求参数限制、拒绝重定向、原日志导入、CLI计划/manifest/失败停止，以及本机模拟HTTP端到端请求。

这些测试中的模型响应均为模拟响应。它们验证探针会怎样处理结果，不证明任何真实模型能够作出这些回答。

## 仓库内容一致性

通过 GitHub tree API 读取提交 `5a3175c538198434f30469b86748f3f1956a3cea` 的文件清单和blob SHA，对12个新增文件逐一计算本地Git blob SHA，全部一致；随后重新执行28项测试通过。该批代码关键SHA：

| 文件 | Git blob SHA |
|---|---|
| scripts/poc01.py | b451e3596b315ed049d12ba2da8398e70034c184 |
| tests/test_poc01.py | cd41aee005793015ec214275e6c1ecdeece64a7a |
| scripts/collect_env.sh | 6205987932d7bc721f2e016115ed394b66ee28d8 |

当前执行环境无法解析 `github.com`，因此直接 `git clone` 的再次检出没有完成；这里采用的是GitHub接口返回的内容哈希核对，不声称完成过网络clone后的实测。后续提交仅补充本说明与入口README时，上述代码SHA保持不变。

## 未验证 / 不能据此承诺

用户Spark的ARM环境和Python版本、GPU负载、实际模型ID、工具/推理解析器兼容性、真实日志内容与真值、实际图片理解、两分钟内完成率、最终运行时的对话式中断继续、服务端取消生成行为、断外网后完整链路、与生产业务共存，均须按[执行手册](01-spark-runbook.md)及后续POC在目标设备验证。

Python代码面向3.10及以上；本地实际执行版本如上，不把一次x86测试写成已测完所有Python版本和ARM硬件。
