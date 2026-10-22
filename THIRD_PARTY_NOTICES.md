# 第三方代码与数据说明

## ChinaTravel 源码

- 来源：https://github.com/LAMDA-NeSy/ChinaTravel
- 固定提交：`f445e0011f42594fcc29b8c752ece06ded9a8218`
- 许可：MIT，完整原文保留在 `ChinaTravel/LICENSE`。
- 集成方式：复制上游 Git 已跟踪的源码文件，不携带上游 `.git`、下载数据、模型结果或本机环境。
- 已带入的本地修改：`tests/test_runtime_configuration.py` 对 Windows `USERPROFILE` 的测试兼容修复。

ChinaTravel 的论文、算法、数据和原始基线归原作者所有。新增运行工具和后续项目改进应独立说明，不将上游实现作为团队原创成果。

## 数据

数据按 `UPSTREAM.json` 中的官方版本下载，原始文件校验记录见 `download_manifest.json`。数据集不包含在本次源码上传中；使用和再分发应查看官方数据仓库说明及许可。
