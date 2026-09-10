# ScopeX

在 NVIDIA DGX Spark 上运行的离线、对话式本机工作助手。

**当前交付：已确认的需求与指标文档 + 可执行 POC01 验证包。尚未在用户 Spark 实测，不是已达标的 Agent 产品。** 模型、推理后端和运行时未定型；不预设 OpenCode / OpenClaw 胜出。

## 从这里开始

| 文档 | 内容 |
|---|---|
| [需求与边界](docs/01-requirements.md) | 已确认需求、首版范围、旧验证事实、完成定义 |
| [最终产物、指标与预估](docs/02-delivery-and-acceptance.md) | 交付物、时间/正确率口径、资源预算与权限目标 |
| [POC路线与阶段门](docs/03-poc-roadmap.md) | 每阶段要解决的问题、产物和进入下一阶段条件 |
| **[POC01：Spark执行手册](docs/poc/01-spark-runbook.md)** | **逐条命令、预期结果、失败检查、停止条件** |
| [原失败日志复现](docs/poc/01-real-log.md) | 真实材料留本地，同内容direct/tool对照与独立真值 |
| [实验结果模板](docs/templates/poc01-result.md) | 配置、数据、指标、失败证据与下一步 |
| [交付前校验记录](docs/poc/01-validation.md) | 28项脚本自测、仓库内容校验与未验证事项 |
| [外部依据](docs/references.md) | 官方协议与硬件说明，不把厂商信息当任务成绩 |

## POC01 先做什么

保持现有模型服务，先比较同一日志直接输入与通过只读工具获取后的表现；再验证空结果、列目录+两份读取、真实双图输入及原失败日志。不安装新的Agent，不修改生产服务。

这是有边界的测量探针，不是从零开发Agent：只提供 `list_files` / `read_file`，没有Shell、Skill、记忆、UI或生产调度。探针通过不等于最终自主性、对话干预或两分钟产品指标通过。

## 在 Spark 主机终端开始

新目录使用：

```bash
git clone https://github.com/saaassin13/scopex.git
cd scopex
```

已有本仓库则先检查工作区，在仓库根目录 `git pull --ff-only`；有改动或冲突先停下，不执行reset/clean。

```bash
python3 --version  # Python >= 3.10；脚本仅使用标准库
python3 -m unittest discover -s tests -v
bash scripts/collect_env.sh
cp -n configs/poc01.example.json configs/poc01.local.json
python3 scripts/poc01.py init
```

已初始化过样本时 `init` 会拒绝覆盖，复用现有样本即可。编辑本地配置的 `base_url` 为Spark现有推理服务回环地址，不要填Agent界面端口。然后：

```bash
python3 scripts/poc01.py models
```

将返回的实际 `data[].id` 填入 `configs/poc01.local.json` 的 `model`，有API key按手册放入环境变量。不要凭口头模型名猜ID、架构或解析器。

```bash
python3 scripts/poc01.py check
python3 scripts/poc01.py run --case chat --repeat 1 --warmup --label warmup
python3 scripts/poc01.py run --case direct-basic,tool-basic --repeat 1 --label smoke-text
```

**每一步先检查输出，再执行下一步。** 首次只做一轮对照，结果位于终端打印的 `runs/<运行编号>/summary.md`。后续3次/5次重复、chain、图片、原日志、thinking对照和错误定位按执行手册进行，不一开始启动长跑批。

## 记录与保护

每次保存真实请求响应、工具事件、配置、文件hash、最终答案和独立评分。默认120秒为目标、180秒总预算、6轮模型调用、8次工具请求；重复同样调用第三次止损。超时或被迫停止不算成功，失败默认停止后续计划。

本仓库公开。`.local/`、`runs/`、`*.local.json` 默认不入库，真实日志、图片、密钥、权重及生产配置留在本地。`.gitignore`不是脱敏，禁止强制上传。只读分析仍占用算力，首次在非关键生产时段运行；不提权、不重启服务、不改网络。

## 首版目标

单用户、同一时间一个主要任务。常见简单任务至少90%在约120秒内自主正确完成；确定性提取和状态检查自主正确率至少95%。复杂业务诊断以约2–5分钟预算单独验证，未准备的新任务另记。以上是验收目标，不是当前已有成绩。

最终复用现成助手及其对话界面，配本地模型服务、项目知识/Skill/专业脚本和任务证据记录。业务侧不逐项重造通用工具，也不把完整调查流程写死。
