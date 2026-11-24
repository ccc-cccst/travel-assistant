# 早期地点选择的初步验证（EXP001）

验证三个问题：一个开头是否消耗了大部分搜索预算；20 条候选是否共享开头；改变早期地点顺序能否改变相同预算下的可行性与质量。

实验记录总入口在项目根目录 EXPERIMENT_LOG.md。原 C 组实验和主程序不被此实验修改。

## 一键运行

在 ShortTrip 根目录的 PowerShell 中执行：

~~~powershell
# 新实验目录；准备阶段调用本地 Qwen，四组搜索默认统一使用规则活动策略。
.\Run-PrefixExperiment.ps1 -Case easy_1 -Nodes 200 -Seconds 180 -ExperimentName EXP001_easy_1

# 重用同一冻结状态，检查取消20候选上限后的现象。
.\Run-PrefixExperiment.ps1 -ExperimentName EXP001_easy_1 -Nodes 200 -Seconds 180 -Candidates 0

# 原C组Qwen活动决策：相同节点预算；另有时间保护。
.\Run-PrefixExperiment.ps1 -ExperimentName EXP001_easy_1 -Nodes 200 -Seconds 300 -ActivityPolicy qwen
~~~

同一目录、活动策略、节点预算、候选上限的结果不允许覆盖。要重复同配置，创建新的实验目录，或直接使用下方命令传入新的 label。

可选案例：experiments/c_only/queries 下的 easy_1 至 easy_5、human_1 至 human_5。
初步验证建议先运行 easy_1，再补 easy_4（指定餐厅）与 human_1（预算、必去地点）。
三例不足以作总体性能结论；这些案例用于方法调试，不用于训练。

## 四个实验组

| 组别 | 地点基础排序 | 首次景点决策干预 |
| --- | --- | --- |
| original | 原实现 | 无 |
| rank_fixed | 修正索引排列与逐地点名次混用 | 无 |
| second_first | 同 rank_fixed | 将最终候选排序第2位提前，其余顺序不变 |
| third_first | 同 rank_fixed | 将最终候选排序第3位提前，其余顺序不变 |

仅修正实验子类的景点基础排序；餐厅排序在四组中完全相同，保留原实现。
original 对 rank_fixed 用于检查排序问题；rank_fixed 对另外两组用于检查早期地点选择。
干预在“尚未访问任何景点”的节点生效，随后各组使用同样的深层排序。
它不强制接纳该地点：营业时间、路线、用餐和硬约束检查仍可拒绝；以实际 branch_enter 为准。

## 公平性与范围

准备阶段先移除数据集的 oracle 约束，再由本地 Qwen 生成、验证约束。
沿真实 C 组流程运行至首个景点决策点，冻结当前部分行程、去返程票、酒店、房间、已访问状态、需求翻译、POI表和推荐列表。
四组都从这一状态重新开始，外层交通酒店搜索不参与本实验。
冻结文件记录源文件哈希；代码变化后必须重新准备，避免新旧程序混跑。
本地4B在酒店排序上曾生成过长解释，因此该准备请求设2048输出token上限；其余请求4096。
格式解析失败时保留原程序回退，并在 frozen.json 的 prepare_format_errors 中记录，四组共享同一外层组合。

rules 模式固定活动类型规则，隔离地点选择的影响；不能把它称为原版 Qwen 的端到端表现。
qwen 模式保留原 C 组活动类型决策，按完整提示词、参数和模型缓存输出；相同状态重用同一回答。
不同组可能访问不同状态，因此缓存命中比例不同；不直接把墙钟耗时差异归因于搜索算法。
主预算是进入 DFS 的次数，另有墙钟保护；原 search_nodes 含全表排序和交通尝试，单独报告。
prepare 的秒数限制针对准备阶段；run 的秒数分别作用于每组，准备和实际搜索耗时分开报告。
规则模式的搜索无需再次请求模型；准备和 qwen 模式只访问 localhost。

## 输出与判读

所有输出保存在 artifacts/prefix_probe 下，由 Git 忽略，不覆盖原 c_only 结果。

- frozen.json：可复用的真实搜索起始状态和代码哈希。
- prepare.log、llm_cache：准备日志和精确请求缓存。
- 每组 summary.json：预算、结果、前缀多样性、各分支开销、首次候选开销、剪枝与验证深度。
- 每组 events.jsonl：逐 DFS 入口、首地点排序、分支进入退出、剪枝、候选发现事件。
- 每组 candidates.json、selected.json：完整候选、原软评分、选中行程。
- 每组 search.log：原搜索过程日志。
- REPORT.md、comparison.json：四组比较，含无结果与错误状态。
- DATASET_HARD_AUDIT.json：独立使用数据集标准约束复查候选，仅在规划完成后执行。

一键脚本会自动进行独立硬约束复查。直接使用 run.py 时，可在搜索完成后运行：

~~~powershell
.\.venv\Scripts\python.exe -X utf8 experiments/prefix_probe/audit.py --out artifacts/prefix_probe/EXP001_manual
~~~

优先检查：

1. branches 中各首景点的 DFS 次数、search_nodes、耗时，以及 candidates_added。
2. unique_prefix_1、unique_prefix_2 与 dominant_prefix_1_share；无景点的空前缀单独存在，不能误作景点。
3. discovery_curve 中首次合法方案的位置与最好分数随搜索进展的变化。
4. 不同开头组在相同预算下的成功情况与软分差异，不能只保留成功组。
5. 剪枝和验证的 activity_depth；入口后活动越多才被拒绝，说明已付出更多探索，但不能只据此断言整个前缀都无解。
6. score_unknown_count 与 score_coverage，避免把缺失信息当成低代价。

达到候选上限的组，应结合 Candidates=0 复核；触及墙钟上限、DFS预算未用满的组应标为受时间截断。
同一节点预算并不保证相同运行时间，因此两类指标都保留。
单个状态的差异只说明条件敏感性，不证明某个首景点普遍较差或主要耗时来源已确定。

## 分开准备与运行

~~~powershell
.\.venv\Scripts\python.exe -X utf8 experiments/prefix_probe/run.py prepare --case easy_1 --out artifacts/prefix_probe/EXP001_manual --seconds 300
.\.venv\Scripts\python.exe -X utf8 experiments/prefix_probe/run.py run --out artifacts/prefix_probe/EXP001_manual --nodes 200 --seconds 180
.\.venv\Scripts\python.exe -X utf8 experiments/prefix_probe/run.py run --out artifacts/prefix_probe/EXP001_manual --nodes 200 --seconds 180 --candidates 0
.\.venv\Scripts\python.exe -X utf8 experiments/prefix_probe/run.py run --out artifacts/prefix_probe/EXP001_manual --variant second_first --label retry_1 --nodes 200 --seconds 180
.\.venv\Scripts\python.exe -X utf8 experiments/prefix_probe/run.py report --out artifacts/prefix_probe/EXP001_manual --label retry_1
.\.venv\Scripts\python.exe -m unittest discover -s experiments/prefix_probe -p "test_*.py"
~~~
