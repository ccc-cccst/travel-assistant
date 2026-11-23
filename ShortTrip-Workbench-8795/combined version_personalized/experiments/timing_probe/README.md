# EXP002：耗时归因

本入口只使用CPU及已经存在的精确请求缓存，不提供联网/模型回退。缺少缓存会失败，不会启动本地Qwen。

```powershell
.\.venv\Scripts\python.exe -X utf8 experiments/timing_probe/run.py --out artifacts/timing_probe/EXP002_new --repeats 3
```

需要交错补测默认20候选配置时，可执行 `repeat_default.py --out <同一测量目录>`；它增加original/rank_fixed各2次常规和2次分解，并记录进程CPU时间。正式EXP002共56次搜索。

`analyze.py --out <报告目录>`读取其下的 `measurements/` 及 `HISTORICAL_TIMING.json`，验证全部候选及计数器与EXP001一致，生成 `ANALYSIS.json` 和 `REPORT.md`。所有命令拒绝覆盖已有同名结果。

运行前检查原冻结状态记录的全部源码哈希。新增目录，不覆盖旧结果或修改规划代码。

## 测量范围

1. 离线重放准备流程，核对完整冻结字段一致，保留实际命中的请求hash及其历史成功调用耗时。
2. 从原冻结状态运行四种景点排序、候选上限1/20/无上限，共12种配置；每种3次常规重复，顺序由固定种子打乱。
3. 每种另做1次嵌套分阶段计时；候选序列的指纹及软分必须与全部常规重复一致。
4. 保留全部原候选、搜索事件、约束审计以及分项计时，报告无额外计时插桩运行的中位数与范围。

各次搜索最多200个DFS入口、180秒。活动类型使用EXP001统一的规则策略，搜索中模型调用和模型缓存命中均必须为0。

## 如何解读

- `elapsed_seconds` 是冻结后搜索区间，不含准备、初始化与最终结果写盘。以其常规重复中位数比较时延。
- `driver_wall_seconds` 包含单次运行包装、环境和状态构建、搜索及结果输出。导入只在测量进程开始发生一次，另记在manifest；不是服务每个请求都要支付的成本。
- `exclusive_seconds` 扣除了被计时的子调用，可在同次搜索内相加；`inclusive_seconds` 包含子调用，跨类别不可相加。
- `search_remainder` 的exclusive部分包括未单独包裹的搜索逻辑、行程复制、部分预检查和日志处理，不能称作纯递归/回溯耗时。其inclusive部分接近整段DFS时间。
- `probe_events` 只涵盖指定的事件/回调方法，不包含所有原程序打印或实验中的行程复制。
- `ranking` 包括景点和餐厅基础排序及约束重排；其inclusive值与路线、DSL等存在重叠。
- 独立计时运行的开销和系统负载可能改变墙钟时间。计时插桩结果用于分解；不要直接拿它与常规运行比较算法快慢。
- 首候选后继续搜索的时间不是已证明可以无损删除的时间。更多候选也可能提供多样性；本例评分不改善不代表其他需求也如此。

## 不能直接相加的阶段

准备重放沿旧实验流程在首次景点约束重排前停止，首次基础排序已经发生；冻结后的DFS从入口重启，会再次基础排序。旧准备还额外调用一次餐厅排序来冻结推荐列表。因此不能把这两个测量区间直接相加并宣称是真实端到端延迟。

历史请求记录来自多次准备尝试，仅成功调用留有计时，失败调用没有完整账本。其和表示历史成功请求累计耗时，不是某次连续运行总时长，也不能再加上包含在其中的最后一次调用。

真实Qwen活动决策下，分支选择还可能改变后续模型请求数量和提示词长度。本实验没有运行该模式；也没有测试分支间预算重分配的因果收益。

主要结果写入 `COMPARISON.json`，完整解释写入上层实验报告，并追加到项目根目录 `EXPERIMENT_LOG.md`。

## EXP008 实时 ShortTrip 与 ChinaTravel 对照

从 ShortTrip 根目录运行。需要同级的 ChinaTravel Git 仓库、本机沙盒、Qwen GGUF、CUDA 运行库以及可用的 GPU。

```powershell
$out = "artifacts/live_timing/EXP008_run"
.\.venv\Scripts\python.exe -B -X utf8 experiments/timing_probe/live_suite.py --out $out
.\.venv\Scripts\python.exe -B -X utf8 experiments/timing_probe/live_report.py $out --out "$out/analysis"
```

每次使用新的目录。套件将原版提交和 ShortTrip 当前跟踪源码分别复制到隔离目录，并复制同一份沙盒。默认使用 easy_1 与 easy_3、相同的 Qwen 权重和请求参数、360 秒端到端截止。每组启动独立的本地 GPU 模型服务，结束后释放。计时从工作进程导入模块开始，服务加载及事后独立校验单列。

原版按首解停止，ShortTrip C 组保留 20 候选上限。报告按首份内部候选、共同独立校验、完整运行三项呈现。完整运行的停止目标有差异。模块独占耗时扣除子阶段，可与其他独占耗时相加。每条原始请求只保存阶段、时延和 token 数，模型诊断日志保存在各次运行目录。
