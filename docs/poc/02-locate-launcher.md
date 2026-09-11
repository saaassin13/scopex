# POC02-A：Gateway 已运行，但入口只定位到 sg

接续 [入口定位](02-locate-openclaw.md)。这是定位工具的修补，不是模型或 Gateway 配置修复。

## 现有证据与边界

用户报告中，用户级 openclaw-gateway.service 为 loaded/active，并存在属于该服务的 openclaw-gatewa 进程；入口元数据只显示 /usr/bin/sg。由此可以确认存在活动服务及相关进程，但尚未取得真正的 OpenClaw 包入口与版本，也未验证请求能完成。

原定位器的 program_paths 只提取初始程序和紧邻的 Node 脚本，没有展开 sg 内层命令。因此把包装器找到误写成 paths_found，却不能继续定位包。这是检测能力缺口，不能据此让用户重新安装、重启或修改权限。

sg 的手册说明它用指定组身份经 /bin/sh 执行命令。这里具体组名、用它启动的原因、MainPID 与 Gateway PID 的父子链，以及 /proc/exe/cwd 不可读取的原因，尚未取得完整证据，不做确定性推断。参考：[Debian sg(1) 手册](https://manpages.debian.org/bookworm/login/sg.1.en.html)，2026-09-11 查阅。

## 本次修补

在原 scripts/poc02_locate.py 中增加简单 sg、sh/bash、env、exec 包装命令的静态路径解析，结果继续交给原 package.json 查找逻辑。支持常见绝对 Node/脚本路径和简单 cd /目录 && exec 形式；不会执行命令、展开环境变量、求值 eval 或执行包内代码。完整命令参数和环境变量赋值不输出。

systemctl show 是展示文本，可能丢失引号边界。因此新状态 wrapped_paths_candidate 只表示找到了内层路径候选，不是可直接执行的完整命令。复杂 shell 脚本、动态路径、无法解析的包装器仍保留未知。WorkingDirectory 不是普通绝对路径时，不用于拼接出不存在的相对入口。

## Spark 上执行

在仓库根目录执行，不使用 sudo；工作区有代码修改时先审阅，不 reset/clean：

```bash
git status --short
git pull --ff-only
python3 -m unittest discover -s tests -p 'test_poc02_l*.py' -v
python3 scripts/poc02_locate.py
```

通配符 test_poc02_l*.py 只选择定位器和本次 launcher 测试，预期共 34 项。它不选择 prepare 测试，也不运行模型。

重点看 services 中 program_paths、entry_parse，以及 packages 中 package_root/package_version/entry_file。不必再次列 Docker 容器，也不必重新执行 poc02_prepare.py。此前的输入目录、reference.json、task.txt 和 POC01 记录原样保留。

若 entry_parse=wrapped_paths_candidate 且 packages 有结果，说明静态入口查找推进成功；接下来仍须独立实例配置、隔离和实际出站请求检查。不能直接把默认生产会话当作已完成隔离的测试会话。

若仍为 wrapper_only 或 packages 为空，不再以猜路径、换权限或重装作为解决方案。操作者可在本机查看已确认服务的 ExecStart，仅摘出真正的 Node 可执行文件与 OpenClaw 入口路径；不要公开完整启动命令、令牌或 Environment。复杂包装脚本不由定位器执行。

## 本次实际测试记录

2026-09-11，本地 Linux 容器 Python 3.13.5：原 18 项定位测试 + 新增 16 项包装解析测试，共 **34 项全部通过**。运行了上述 unittest 命令和 poc02_locate.py --help；对脚本与两份测试做了 Python 3.10 语法解析检查，不声称实际运行于 Python 3.10。

新增覆盖：sg 带/不带 -c、登录标记、systemctl 展平引号、嵌套 shell/exec/env、字面目录切换、环境赋值及鉴权参数不输出、复杂管道和求值表达式保留未知、已知 Node 无参选项、不把 --eval 内容当脚本、带标记 cwd 不拼成假路径、语法损坏、wrapper_only 状态，以及模拟包从内层脚本路径被找到。测试未运行 OpenClaw、sg、模型或 Docker；真实服务字符串和 Spark 包版本仍需现场输出验证。

本次未修改 POC01 主探针、评分、样本或已运行结果，也未修改 OpenClaw 的系统服务文件。34 项是定位工具测试成绩，不是 Agent 完成率。
