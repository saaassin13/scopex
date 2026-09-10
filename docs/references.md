# 外部依据与版本说明

核对日期：2026-09-10。链接用于查协议和硬件，不是本项目性能证明。latest页面会变化，实际执行记录必须注明已安装版本。

1. [vLLM Tool Calling](https://docs.vllm.ai/en/latest/features/tool_calling/)：工具定义、自动工具选择、模型相应parser与chat template。脚本不猜具体模型应选哪个解析器。
2. [vLLM OpenAI-Compatible Server](https://docs.vllm.ai/en/latest/serving/openai_compatible_server/)：本地HTTP接口与额外请求参数。REST payload与SDK extra_body不是相同嵌套结构。
3. [vLLM Multimodal Inputs](https://docs.vllm.ai/en/latest/features/multimodal_inputs/)：Chat Completions中的image_url和base64数据输入、多图相关限制。发出图片不证明模型已正确理解画面。
4. [vLLM Reasoning Outputs](https://docs.vllm.ai/en/latest/features/reasoning_outputs/)：推理输出与对应解析器。显示/输出字段、服务默认和实际推理行为需分开核对。
5. [NVIDIA DGX Spark Hardware](https://docs.nvidia.com/dgx/dgx-spark/hardware.html)：硬件与统一内存说明；不能据此推导本项目任务延迟与成功率。

没有给“千问3.8 27b fp8”强行指定模型仓库、默认thinking或parser；精确身份由Spark实际/model服务与本地权重配置确认。协议兼容不等于功能和质量等价。

本文档中的交付目标、POC阶段门与规划区间来自已确认需求及实验设计，不是摘抄厂商基准或已有Spark实测。
