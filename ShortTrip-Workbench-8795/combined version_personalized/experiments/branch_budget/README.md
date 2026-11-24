# EXP003 首景点分支轮转

普通 DFS 和轮转 DFS 共用同一份冻结状态、规则活动策略、景点排序、校验与原软惩罚。每对运行只改变搜索任务的推进顺序。

实验入口把原 C 组 DFS 的递归调用转换为生成器调用，在 DFS 入口处暂停。到达第一个景点时，轮转把这个前缀的活动列表和访问状态保存为独立任务。递归栈保留每层的候选、交通方式和循环进度。相同首景点的交通备选归入同一个分支组。每轮给每个已引入的分支组一批入口额度，任务暂停后回到组内队列。分支内某个任务完成后，其余交通备选仍可继续。协调任务逐步引入后续分支。旧 DFS 在移交任务后会打印回退信息，实际进度以分支的引入、切片和完成计数为准。

运行前校验冻结源文件。节点预算阶段复查三种可映射到历史 EXP001 的普通 DFS 候选序列，逐指纹及逐原惩罚对照。每次运行的在线选择仍按原惩罚，平分时保留先发现的方案。新规则评分只用于事后辅助比较。所有搜索共用一个预算，在线初始化、分支发现、校验和方案发布计入墙钟。

在 ShortTrip 根目录运行：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest experiments.branch_budget.test_search
.\.venv\Scripts\python.exe -X utf8 -m experiments.branch_budget.run --stage nodes --out artifacts/branch_budget/new_nodes
.\.venv\Scripts\python.exe -X utf8 -m experiments.branch_budget.run --stage wall --out artifacts/branch_budget/new_wall
.\.venv\Scripts\python.exe -X utf8 -m experiments.branch_budget.analyze artifacts/branch_budget/new_nodes artifacts/branch_budget/new_wall --out artifacts/branch_budget/new_analysis
```

默认量子为 10 次 DFS 入口。节点阶段运行 6 种顺序 × 2 方法，配额为 200 次入口并设 180 秒保护。墙钟阶段按 5、15 秒分别做 6 种顺序 × 2 方法 × 3 次，每一对交错运行。所有输出使用新目录。

节点阶段包含一个与旧实验相同的计数边界。第 200 次入口计数后，C 组入口的预算检查结束搜索。墙钟阶段按已写盘的方案发布时间截止。进行中的原子操作允许结束，因此进程结束时刻可能略晚于预算。

各运行目录保存全部候选、事件、分支工作量和运行日志。分析目录的报告使用独立审核通过的选中方案，并列出原惩罚和快速规则评分。首景点集中度以实际进入各首景点分支的 DFS 入口为分母，公共前缀耗时保留在总墙钟中。路线请求计数来自搜索使用的路线收集函数，独立审核开销在搜索计时之后。

分析同时生成 `breakdown/REPORT.md` 和 `breakdown/pairs.csv`，包含交通、步行、游览、时段、费用、各规则分和搜索开销。对于已存在的分析目录，可以从保存的方案与评分生成分项表。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m experiments.branch_budget.breakdown artifacts/branch_budget/EXP003_easy_1_analysis --out artifacts/branch_budget/EXP003_easy_1_breakdown
```

用有 Matplotlib 的 Python 可生成预算集中度与最坏原惩罚的对照图。

```powershell
python -X utf8 -m experiments.branch_budget.plot artifacts/branch_budget/new_analysis/analysis.json --out artifacts/branch_budget/new_analysis/comparison.png
```

## EXP005 跨需求验证

默认排序使用 C 组的原地点排序，`213` 和 `312` 分别将原第二、第三位移到最前。每次搜索限 15 秒、20 条候选。评测复查 5 秒和 15 秒前已发布的方案，按需求汇报默认顺序和排序干预的合法率、行程分项及搜索开销。纯离线输入使用本地基准硬约束和推荐回退。已有 Qwen 快照单列，两组搜索都使用规则活动选择。

~~~powershell
$expRoot = 'artifacts/branch_budget/new_suite'
.\.venv\Scripts\python.exe -X utf8 -m experiments.branch_budget.offline_inputs --out "$expRoot/offline_inputs"
Get-ChildItem "$expRoot/offline_inputs" -Recurse -Filter frozen.json | ForEach-Object {
    $expCase = $_.Directory.Name
    .\.venv\Scripts\python.exe -X utf8 -m experiments.branch_budget.run --source $_.FullName --out "$expRoot/runs/$expCase" --stage wall --seconds 15 --repeats 1 --orders 123 213 312 --ranking original --candidate-limit 20
}
.\.venv\Scripts\python.exe -X utf8 -m experiments.branch_budget.run --out "$expRoot/runs/qwen_easy_1" --stage wall --seconds 15 --repeats 1 --orders 123 213 312 --ranking original --candidate-limit 20
.\.venv\Scripts\python.exe -X utf8 -m experiments.branch_budget.cross_case $expRoot --out "$expRoot/analysis"
~~~
