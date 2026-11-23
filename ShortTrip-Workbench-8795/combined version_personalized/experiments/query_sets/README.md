# 10 城与西安验收题

[ten_city_xian_v1.json](ten_city_xian_v1.json) 保存 30 条中文提示词。每档 10 条，其中 3 条以西安为目的地。各档均覆盖往返交通。

- **easy** 提供天数、人数和城市，附少量明确要求，例如交通方式、房间数或一个景点。
- **medium** 组合可核验的约束，例如限价、返程发车时间、景点和餐厅。
- **human** 用自然对话表达多项约束和软偏好。硬约束计入通过率，软偏好仅作定性复核。

运行时生成按 `easy/`、`medium/`、`human/` 划分的单题 JSON。每个用例的评估约束在模型运行前由现有 DSL 编译器生成，只在评估阶段使用。EXP014 在原有城市和西安共用 expanded11 快照，首条内部校验合格的候选进入独立评估。全量验收使用本地 Qwen3.5-4B Q8、固定随机种子 20261116 和空翻译缓存。每例限时 300 秒，同一模型服务串行处理各个用例。实验记录见 [EXP014](../../EXPERIMENT_LOG.md#exp014-当前版本三层用户提示词与西安数据接入验收)。

```powershell
./experiments/query_sets/Run-Local.ps1 -Out artifacts/query_checks/EXP014_reproduction -Seconds 300 -Compact
```

已运行同一模型的本地服务时，可以直接调用批跑入口。

```powershell
./.venv/Scripts/python.exe -B -X utf8 -m chinatravel.environment.sandbox run -- -m experiments.query_sets.run --out artifacts/query_checks/EXP014_reproduction --seconds 300
```

加 `--prepare-only` 可只检查数据，`--only st14_e01` 可用独立输出目录筛选用例。结果目录包含 `DATA_CHECK.json`、`REPORT.md`、`RESULTS.json`、`STATUS.json` 以及逐例 `runs/<UID>/profile/plan.json` 和请求时序记录。出行程与全部约束通过分别统计。快照的价格和班次仅用于实验。
