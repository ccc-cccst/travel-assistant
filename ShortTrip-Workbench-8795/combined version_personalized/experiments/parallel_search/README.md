# 组合并行试排实验

独立入口，不接入 baseline、day_sets、combo_schedule 的默认流程，也不修改它们的文件。默认 2 个 Windows spawn 工作进程、持续收集模式，固定组合清单交错分配；每个进程仍调用原来的组合调度器与日集合/DFS。进程内继续有限试排和恢复生成器进度，进程间不共享规划器、已访问集合或可变缓存。

EXP034 首解结果保留。EXP035 扩展为三种明确停止口径：`--stop-mode budget` 默认持续到全局期限或有限清单结束；`target --candidate-limit 20` 收集20份全局去重的系统合格候选后停止；`first` 保留首解对照。目标20份是系统合格数量，生成后独立复核可能少于20；题集标注不在线控制搜索或停止。固定预算内应看独立合法数量、逐日景点集合多样性、分项质量和成本，不能预设双进程一定更好。

本实验先隔离“并行探索组合”的影响，没有同时增加全局模型缓存、改变组合排序或修改评分。有限清单默认最多 16 组，耗尽不代表需求无解。当前按清单静态分区，没有跨进程工作窃取。

## API 对照

从 combined version_1 根目录，使用现有 venv 的 Python。`DEEPSEEK_API_KEY` 仅从执行进程的后端环境读取，不写入参数或结果文件。

```powershell
$tripPython = '..\ShortTrip\.venv\Scripts\python.exe'
& $tripPython -B -m experiments.parallel_search.run prepare --query experiments/c_only/queries/easy_2.json --out result/experiments/EXP034_parallel_combos/api_fixture --max-combinations 16
& $tripPython -B -m experiments.parallel_search.run run --bundle result/experiments/EXP034_parallel_combos/api_fixture/bundle.json --out result/experiments/EXP035_parallel_candidates/api_budget_w1 --workers 1 --provider deepseek --seconds 60 --stop-mode budget --audit-query experiments/c_only/queries/easy_2.json
& $tripPython -B -m experiments.parallel_search.run run --bundle result/experiments/EXP034_parallel_combos/api_fixture/bundle.json --out result/experiments/EXP035_parallel_candidates/api_budget_w2 --workers 2 --provider deepseek --seconds 60 --stop-mode budget --audit-query experiments/c_only/queries/easy_2.json
```

切换到同产量对照时，两臂均使用 `--stop-mode target --candidate-limit 20 --seconds 1800`。每次指定新输出目录，已有结果不覆盖。

prepare 复用原话转换、住宿/交通排序和组合预检查，在搜索前冻结实际 DSL、地点表、交通方式、预算和组合清单。题集标注不进入准备或工作进程；`--audit-query` 仅在输出后复核。比较两臂时用同一个 bundle，不与历史端到端耗时直接混算。模型在搜索中仍正常调用官方 DeepSeek Flash，温度 0、thinking disabled、局部请求沿用原组合版限制。模型响应可能不同，严谨计时需交错顺序并重复，不只看一对。

## 不发 API 的集成验证

```powershell
& $tripPython -B -m experiments.parallel_search.run from-run --run-path artifacts/product/EXP033_expanded_validation/runs/easy_2__combo_schedule --out result/experiments/EXP034_parallel_combos/recorded_fixture
& $tripPython -B -m experiments.parallel_search.run run --bundle result/experiments/EXP034_parallel_combos/recorded_fixture/bundle.json --out result/experiments/EXP035_parallel_candidates/recorded_budget_w1_new --workers 1 --provider recorded --seconds 60 --stop-mode budget --audit-query experiments/c_only/queries/easy_2.json
& $tripPython -B -m experiments.parallel_search.run run --bundle result/experiments/EXP034_parallel_combos/recorded_fixture/bundle.json --out result/experiments/EXP035_parallel_candidates/recorded_budget_w2_new --workers 2 --provider recorded --seconds 60 --stop-mode budget --audit-query experiments/c_only/queries/easy_2.json
```

from-run 从既有真实日志的 admit 事件、班次、酒店和固定费用重建可唯一确定的火车组合，房间数由固定费用反推；有歧义的记录列出并跳过，不猜测班次。它冻结实际生成的 DSL 和数据，交通方式顺序固定为受 DSL 过滤的 metro/taxi/walk，预算读取实际 DSL 下界。它不是对历史所有上游决策的逐项复现，也不是从头规划。支持范围限定为可重建的多日火车样例；其他场景使用 API prepare。

每日模型建议只对完整输入 context 精确匹配后重放，未知 context 明确标为 ReplayMiss，该根后续探索资料不足，其余根可继续；不编造回答，也不计作模型证明无解。重放不模拟网络延迟，模型请求数是本地重放次数，不能当成真实 API 消费。独立复核通过才报告合法，不拿历史计划直接充当本次输出；本次仍执行真实本地日内搜索、路线查询和原合法性检查。

## 结果口径

- FIRST_PLAN.json：第一个工作进程通过原合法性检查并回传的完整行程。
- CANDIDATES.json：预算内收到的全局去重候选，包含候选到达时刻与组合号。
- COLLECTION_METRICS.json / CANDIDATE_METRICS.csv：逐份独立核验、达到1/5/10/20份独立合法候选的时刻、10/20/40/60秒等节点的产量、景点集合多样性及费用/步行/接驳/游览分项。Pareto只计不同目标向量，不把同质量的多个候选算成更多取舍；各项最优值附对应候选ID，不能拼成一条不存在的全优行程。
- SUMMARY.json：含进程启动的首份生成/收到耗时、总清理耗时、进程退出码、CPU、模型请求开始/结束数、强制回收情况及独立复核。模型总等待与 CPU 不相加当作墙钟。
- events.jsonl：两个进程实际交叠、组合 dispatch/yield、模型请求 hash 和停止顺序。
- workers/：各进程独立搜索日志与日集合事件。
- independent_translated_audit.json / independent_dataset_audit.json：生成后使用原独立核验器复查，未知不算通过。

首份候选收到后即写文件，budget/target模式继续搜索。只有first模式收到首份、target模式达到全局去重目标，或期限/清单结束时才通知进程停止。预算包括工作进程冷启动；清单提前耗尽如实报告，不填满等待伪装成持续搜索。API 单次最多等待 8 秒且受局部/全局期限约束；最多额外 10 秒用于回收。仅回收本次创建的进程。如强制回收导致 CPU 或在途请求数据缺失，会保留缺失标记，不视为零成本。并行会改变候选抵达顺序。

## 测试

```powershell
& $tripPython -B -m unittest discover -s tests -p test_parallel_pool.py -v
```

检查慢分支不阻塞其他进程、状态隔离、单进程顺序、超时与进程回收、部分工作进程失败、空组合清单、输出防覆盖、精确重放失败，以及持续收集、全局去重计数、两进程共同贡献、景点集合和质量向量去重。

## EXP035 冻结副本复现

采集中检测到主工作树的三个基础文件发生变化，哈希检查已阻止混用。EXP035 正式配对使用匹配原 fixture 的 EXP033 基础源码副本，加上本次候选收集代码；主工作树的改动未被还原。128个基础源码、177个副本文件的清单在 result/experiments/EXP035_parallel_candidates/FROZEN_SOURCE_MANIFEST.json。

从项目根目录运行以下独立副本；两臂只改 workers 和新输出目录即可。旧 fixture 在当前主工作树上可能因哈希保护而被拒绝，不应绕过检查。

```powershell
$tripPython = '..\ShortTrip\.venv\Scripts\python.exe'
$parallelEntry = 'result/experiments/EXP035_parallel_candidates/frozen_source/experiments/parallel_search/run.py'
& $tripPython -B $parallelEntry run --bundle result/experiments/EXP034_parallel_combos/recorded_fixture/bundle.json --out result/experiments/EXP035_parallel_candidates/my_budget_w2 --workers 2 --provider recorded --seconds 60 --stop-mode budget --audit-query experiments/c_only/queries/easy_2.json
```
