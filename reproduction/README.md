# 复现工具

从项目根目录运行 `Setup-Project.ps1`。下载器读取 `UPSTREAM.json`，固定源码来源与数据版本；查询准备脚本生成官方离线查询以及 `reproduction/splits/` 中的实验子集。

- `Start-Reproduction.ps1`：指定模型 ID、接口地址和版本的统一入口。
- `Start-DeepSeekFlash.ps1` / `Start-Doubao.ps1`：历史实验配置的便捷入口。
- `run.py`：隔离运行参数、方法名称、缓存和响应记录。
- `check_environment.py`：无模型调用的依赖与数据检查。
- `run_unit_tests.py`：运行上游离线测试。
- `run_flash_preview.py`：在固定样本上执行有外部时限的预览；会调用模型并产生费用。
- `analyze_flash_patterns.py` 等分析脚本：读取历史完整运行记录生成诊断，需要另行准备相应结果和审计文件；克隆本仓库不会自动获得历史实验文件。

密钥不写入文件，PowerShell 入口在需要调用模型时隐藏读取。仅下载与环境检查不会运行模型。

数据、运行日志、模型结果、个人配置和虚拟环境均不提交。小型样本选择和模型配置可跟随代码提交，运行前需核实服务端模型版本是否变化。
