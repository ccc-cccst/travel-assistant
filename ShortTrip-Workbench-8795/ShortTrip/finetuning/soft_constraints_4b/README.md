# 4B软约束翻译微调

本目录包含两轮实际使用的BF16＋LoRA训练代码、用户提供的300条数据、固定划分、目录词表和基础模型版本记录。v2为当前版本。该模块独立生成软约束DSL、原文证据与未映射要求，行程主链路的调用入口见仓库根目录README。

数据按难度分层划分为240条训练、30条验证、30条回归，每个难度分别80、10、10条。v1评测后的30条已用于错误分析，v2继续使用这30条进行回归。标注的语义正确性需要人工复核。

## 环境与训练

以下命令在Linux服务器、仓库根目录执行。实际训练设备为RTX 3090 24GB。

```bash
python3 -m venv finetuning/soft_constraints_4b/venv
source finetuning/soft_constraints_4b/venv/bin/activate
python -m pip install -r finetuning/soft_constraints_4b/v2/requirements.txt
python -u finetuning/soft_constraints_4b/v1/run_experiment.py
python -u finetuning/soft_constraints_4b/v2/run_experiment.py
```

两轮均从固定版本的官方基础模型训练。首次运行会下载基础权重，文件存放在各轮的base_model中。v2训练本身独立，结束后的三组对比评测会加载v1适配器，因此完整复现按上述顺序执行。已有第一轮适配器时，可在v2/config.json中设置previous_adapter，路径相对于v2目录解析。

每10步保存带完整性校验的训练状态，支持优化器、调度器、随机状态恢复。v2每30步生成验证答案，以DSL整条匹配率选最佳检查点，最多恢复两次训练进程。运行状态保存在各轮experiment_status.json，输出位于outputs和evaluation目录。

## 当前结果

v2将DSL值的损失权重设为3，未映射字段值设为2，其他答案token为1，提示词为0。最长输入2048，以验证集生成结果选出第60步适配器。模型、LoRA参数文件与训练状态由运行脚本在本地生成。

回归指标、局限与本地Q8结果见[实验说明](../../docs/soft_constraint_lora_results.md)。部署入口见[本地Q8部署](../../deployment/soft_lora_v2/README.md)。
