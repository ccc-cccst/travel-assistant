# 软约束翻译Q8本地部署

部署目标为第二轮BF16 LoRA选出的第60步适配器合并后的Q8_0 GGUF。server.py管理Windows上的llama.cpp Vulkan服务，translate.py使用固定prompt.txt调用软约束翻译接口。本目录可独立运行，接入行程生成主链路需要显式配置调用。

## 准备文件

将llama.cpp b11960的Windows Vulkan发行包解压至runtime/llama-b11960-vulkan，使llama-server.exe位于deployment.json指定路径。对应源码提交为8642541b255bfb5dc39cd351a178be09013a55d6。[llama.cpp发行页面](https://github.com/ggml-org/llama.cpp/releases/tag/b11960)

将已合并的模型保存为models/Qwen3.5-4B-SoftDSL-v2-Q8_0.gguf。当前实际部署文件大小为4,482,402,816字节，SHA256如下。

```text
9bef2b4b7560e295955ba485f6f9371848858fbb9bed4cc87be0f86700d95565
```

重新导出时，在训练环境执行以下命令，将converter参数替换成同版本llama.cpp的convert_hf_to_gguf.py路径。输出保存在实验目录deployment_export中，重新导出的文件哈希应单独记录。

```bash
python deployment/soft_lora_v2/conversion/export_server_model.py finetuning/soft_constraints_4b/v2 --converter /path/to/llama.cpp/convert_hf_to_gguf.py
```

## 启动与调用

在仓库根目录运行以下命令。Python客户端与进程管理使用标准库。

```powershell
python .\deployment\soft_lora_v2\server.py start
python .\deployment\soft_lora_v2\translate.py "我想去上海玩，想尝尝当地菜。"
python .\deployment\soft_lora_v2\server.py status
python .\deployment\soft_lora_v2\server.py stop
```

接口地址为http://127.0.0.1:18082/v1，模型名shorttrip-soft-dsl-v2，上下文4096。路径、端口与模型名在deployment.json中配置。

30条回归中DSL整条匹配29条，DSL、证据和未映射覆盖联合匹配26条。详细结果见[实验说明](../../docs/soft_constraint_lora_results.md)。
