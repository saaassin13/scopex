# POC01：接入原来失败的真实日志

目标是保留原问题，而不是找一个更简单的演示代替它。所有输入留在 Spark，本仓库不接收生产原文件。

## 1. 准备三份本地材料

```bash
mkdir -p .local/poc01/real-input
```

用你自己的实际路径复制**原来失败的那份日志**到 `.local/poc01/real-input/original.log`。不要直接对持续写入的生产日志跑基线；使用快照并核对 SHA256，保留原文件。不要改写原有时间、编码和异常记录。

创建 `.local/poc01/real-input/prompt.txt`：保留原任务的业务问题、筛选条件与字段定义；说明应输出的JSON结构。没有输出结构或“问题数据”未定义时，先用人能逐条判定的规则澄清，再标记与原指令的差异。不要把答案写入prompt。

创建 `.local/poc01/real-input/expected.json`：由人工独立核对或可信解析器加人工复核得到真值。不能让被测模型生成真值后自己评分。允许任何标准JSON根值，但推荐对象，例如键 records、数组元素指定字段；数组顺序、类型、空结果、边界必须明确。

只支持UTF-8文本；非UTF-8先留原快照，再制作转换副本，记录编码与两份hash。默认上限256KiB，不静默截断。大日志可以保留原完整文件，再明确截取哪个区间作“小样本诊断”，但截取后不再称作原完整任务通过。必要时仅改本地大小上限并记录，更大的直接输入可能超过模型上下文；不要把超长任务当成同等简单任务。

## 2. 导入为成对用例

在仓库根目录执行：

```bash
python3 scripts/poc01.py add-log \
  --name original \
  --file .local/poc01/real-input/original.log \
  --prompt-file .local/poc01/real-input/prompt.txt \
  --expected-file .local/poc01/real-input/expected.json
```

输出 `.local/poc01/real-original/`，包含：

- `data/input.log`：原文件复制品，模型只读这个目录。
- `cases.json`：direct/tool共享的业务要求和真值，位于数据目录外。
- `source.json`：原路径和SHA256，仅本地保存。

再次同名导入会报错，不覆盖旧基线。更新规则或数据请用新名字并说明原因。

## 3. 先各一次，再各五次

```bash
python3 scripts/poc01.py --suite .local/poc01/real-original run \
  --case direct,tool --repeat 1 --label original-smoke

python3 scripts/poc01.py --suite .local/poc01/real-original run \
  --case direct,tool --repeat 5 --keep-going --label original-repeat
```

第一条失败先查看结果，不能直接继续跑批。`--suite` 和 `--config` 属于全局选项，放在 `run` 之前。

真值、日志或输出约定有误时，保留当前结果，另建修正版用例；不能修改答案追着模型输出跑。当前脚本严格比较整个 JSON；格式错误也是失败，但在人工诊断中应与数据内容错误分开。

## 4. 哪些结论成立

两组读取的是相同字节，用户业务要求相同。direct把内容放进消息，tool只给文件名并验证实际读取。结果变化说明值得检查哪些层，不构成单次因果证明。

本探针添加了固定系统说明、严格JSON输出和很小的工具集，这些与原完整OpenClaw/OpenCode运行不同。报告必须保留这些差异。探针通过只能证明当前模型服务在这组条件下可完成，不能宣称已修复原框架；后续POC02需回到现成运行时复测。

若没有原日志、原指令或独立真值，填写“原问题尚未复现”，而不是“已解决”。
