# POC02-A 准备工具：本次校验记录

日期：2026-09-11。执行环境：本地 Linux 容器、Python 3.13.5，**不是用户的 Spark**。

## 已实际执行

```bash
python3 -m unittest discover -s tests -p 'test_poc02_prepare.py' -v
python3 scripts/poc02_prepare.py --help
```

结果：**25 项测试通过，命令行帮助正常显示（先发现并修复 root 环境下帮助被权限检查挡住的问题，正式准备仍拒绝 root）。** 另用 `ast.parse(..., feature_version=(3,10))` 检查主脚本和测试文件语法；不将其称为已在 Python 3.10 运行。

覆盖：输入逐字节复制与原记录不变、工作目录只含输入而无真值、明确不宣称隔离/启动通过、防覆盖与目录相交检查、日志哈希不符、任务 manifest 变化、预热/未完成/格式失败记录拒绝、direct/tool 要求与真值不一致、文件及父目录符号链接、非本机或含凭据 URL 拒绝、thinking 必须为布尔 false、不导出未知请求字段/密钥/备注、重复 JSON 键和非 JSON 常量拒绝、帮助子进程清理敏感环境、参数不经 Shell 执行、子进程超时与可执行文件缺失、远程 Docker 上下文不发 daemon 查询、摘要不直接输出帮助原文。

所有评分记录使用合成材料；Docker 检查用模拟输出。超时与参数传递测试实际启动本地 Python 子进程，不启动 OpenClaw。仓库不包含生产日志、用户实际运行目录或标准答案。

## 尚未验证

用户 Spark 上实际 OpenClaw 的版本/安装方式/帮助命令；当前 Docker 权限、镜像架构和工具沙箱；配置 schema；模型请求字段是否真实传递；模型输出与耗时；对话式中断和继续；完整离线及生产共存。

本次没有修改 `poc01.py`、`inspect_run.py` 或 POC01 的评分。原有套件没有在这个交付步骤重新执行，因此不把历史的 28/25 项成绩合并成新的全套测试成绩。

退出码 0 是准备工具完成，不是 POC02 模型任务完成。`PREPARED_NOT_RUN`、`isolation_verified=false` 和 `wire_request_verified=false` 在下一轮真实取证前保持不变。
