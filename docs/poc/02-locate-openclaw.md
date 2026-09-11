# POC02-A：PATH 找不到 OpenClaw 时定位已有安装

接续 [POC02 准备手册](02-openclaw-runbook.md)，不重新准备日志、不重跑模型、不重装组件。

## 现象和判断边界

准备摘要中的 `host_cli_found=false` 只表示当前 Python 进程继承的 PATH 没有 `openclaw`。此前准备脚本不会检查主机进程或 systemd 启动项，因而可能漏掉源码启动、非当前 Node 版本的全局安装或其他账号启动的服务。

`openclaw-sandbox:bookworm-slim` 是官方工具沙箱镜像，不是 Gateway 主程序。存在同名运行容器不能证明 Gateway 当前存活，也不能证明新测试的文件隔离已完成。不得进入该沙箱安装或寻找 Gateway 来替代真正入口。

当前官方文档分别说明 [工具沙箱与 Gateway 的区别](https://docs.openclaw.ai/gateway/sandboxing) 及 [Node/PATH 未包含全局 bin 时的命令缺失](https://docs.openclaw.ai/install/node)。查阅日期 2026-09-11；本步骤不依据最新文档自动升级用户已安装版本。

## 执行

在 Spark 主机、scopex 仓库根目录执行，不使用 sudo。有本地改动先审阅，不执行 reset/clean。

```bash
git status --short
git pull --ff-only
python3 -m unittest discover -s tests -p 'test_poc02_locate.py' -v
python3 scripts/poc02_locate.py
```

定位脚本只输出元数据，不创建新测试目录，不修改上一轮准备报告、日志、标准答案、配置或服务。以下内容会被检查：

- 可读的 `/proc` 中 OpenClaw 相关进程：PID、UID、exe、cwd、服务名，以及初始程序/紧邻的 Node 脚本路径。不读取 `/proc/*/environ`。
- 用户级与系统级 systemd：相关已加载服务及默认服务名，只执行 list-units/show。ExecStart 在本地解析后只保留程序路径，不显示完整参数、Environment 或 stderr。未识别的包装命令标记 unknown，不猜命令、更不执行。
- 常见 nvm/pnpm/mise/fnm/npm 安装路径，以及进程、服务指向的目录附近的 package.json。只读取包名、版本和入口，不执行包脚本；不遍历整块磁盘。

`packages[].package_version` 是 package.json 的静态记录，不是本次执行 `openclaw --version` 的成绩。有多个版本时保留全部候选，再用运行中的进程/服务路径确定使用哪一份。进程可能已退出、权限不足、运行于另一容器命名空间；`null`/`unknown` 不可解释为没有安装。

自定义源码目录可显式提供，不递归遍历：

```bash
python3 scripts/poc02_locate.py --source-dir /实际源码或包目录
```

该路径由操作者提供；不从模型生成的答案或日志中的指令执行任何代码。

上一轮 Docker 清单只按名称筛选。为避免漏掉自定义命名的 Gateway，在确认仍使用同一台 Spark 的本机 Docker 上下文后，可额外查看**全部容器的名称、镜像、状态**：

```bash
docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
```

不执行完整 docker inspect、不打印容器环境变量或完整命令，不 docker exec，不重启/删除旧沙箱。

## 结果如何使用

`LOCATED_METADATA_ONLY_NOT_RUN` 只表示完成查找，不是实例启动、权限隔离或参数传递通过。退出码 0 不表示发现了可用版本；查看 processes/services/packages 的实际内容。

先提交定位输出和容器名称列表；不需要粘贴完整 systemd unit、ExecStart、OpenClaw 配置、令牌、历史会话或生产日志。自动输出避免完整命令参数，但路径与容器名仍可能是内部部署信息，发送前审阅。

找到真实入口和版本后，才复用已有安装设置独立测试配置、验证挂载隔离和实际模型出站请求；此前准备的 reference/task/input 材料继续复用。未找到时记录覆盖范围和缺失信息，不用重新安装来掩盖入口未知。

## 本次实际校验

2026-09-11，在本地 Linux 容器 Python 3.13.5 执行上述定位脚本的 **18 项离线测试，全部通过**，命令行 `--help` 正常。另通过 Python 3.10 语法解析检查，不声称在 Python 3.10 实机运行。

覆盖程序与脚本路径提取、不泄漏 token/Environment、不可解析服务入口保留 unknown、包名/版本过滤、入口越界、无效 JSON、模拟 /proc、服务只读命令、已知目录查找和权限/缺失元数据处理。测试用临时文件、模拟进程和模拟 systemctl，不启动 OpenClaw，不连接 Docker 或模型。没有在用户 Spark 上执行，未修改原 POC 探针及评分，也未重新声明旧测试套件成绩。
