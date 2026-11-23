# ShortTrip 个性化版

当前交付入口为 `/tripspec`。网页使用 TripSpec、旅行快照、逐日构想、完整试排与独立核验，一次最多 15 套候选，共享 60 秒搜索预算，展示首推及最多两份备选。下文的 “10 份／1800 秒” 属于旧版 `plan` 实验入口。模块说明、首推规则和已知边界见 [当前架构](ARCHITECTURE.md)。

独立于 `combined version_1`。沿用融合版日集合、组合调度和硬约束核验，显式偏好参与候选召回、模型每日集合推荐和方案选择。安装 V20 后默认读取十二城数据。**旅行天数接受正整数，没有1–3天限制。** 多城链路另行支持。

## 规划流程

固定采用“生成多份完整行程候选 → 合法性核验后入池 → 按偏好与体验分项选优”。每日景点集合只是中间候选；最终比较的完整行程包含往返、住宿、每日活动、用餐和接驳。

默认目标为10份通过系统检查的完整候选，总搜索预算1800秒。首份候选生成后继续搜索，达到候选目标、时间预算或当前搜索范围耗尽后，从已有候选池选出一份。此前每例3候选是试跑参数。独立审核逐份记录，并核对实际选中的那份；最终展示须通过审核。

费用、兴趣命中、已知接驳步行、市内通行、游览时长、节奏和未满足项均保留。当前选优是已生成候选池内按既定规则的最优，不表示穷尽所有可能路线。当前按完整行程指纹去重，时间或交通变化也可能算另一份候选；实质路线多样性仍需单独评价。

## 启动

要运行当前四模块的网页，请使用下面的命令并打开 [TripSpec 行程助手](http://127.0.0.1:8780/tripspec)。已核验行程中的“换一家交通更方便的酒店”可以预览整团费用、接驳与游览时长的变化。采纳后可收藏页面地址，之后重新打开，还可返回原方案。[交付与换酒店验收](result/experiments/P036_delivery_adoption/REPORT.md)

```powershell
.\Run-Personalized.ps1 -Web -Port 8780
```

在本目录执行，复用相邻ShortTrip的Python环境和数据快照：

```powershell
.\Run-Personalized.ps1 -Web -Port 8782
```

访问 http://127.0.0.1:8782/ 。页面支持城市、天数、人数、预算、交通、兴趣、节奏、移动、菜系、酒店偏好、预约与文字补充，先核对需求，再提交规划。

官方模型为deepseek-flash，密钥从启动后端的环境变量DEEPSEEK_API_KEY读取；环境变量在本机终端或系统设置配置，浏览器不接触密钥。`tools/live_validation.py`另支持关闭回显的终端输入，凭据只存运行进程。

```powershell
.\Run-Personalized.ps1 -InputFile examples/museum.json -Seconds 1800 -Candidates 10
```

本地Qwen仍可用 `-Provider qwen`，默认端点http://127.0.0.1:18080/v1，需实际启动服务。结果写 `result/manual/<时间戳>/`；`-Out`可指定新的目录，已有目录不覆盖。

## 十二城 V20 数据

首版行程规划开放北京、上海、南京、苏州、杭州、深圳、成都、武汉、广州、重庆。西安和厦门暂缓开放，出发地和目的地均适用。V20 数据仍按十二城安装，`facts` 与历史复查可读取两城。开放状态由 `chinatravel/personalization/city_scope.py` 统一管理。

新启动的网页和 `personalize.py` 默认读取 `../ShortTrip/artifacts/data/6de12f4`，对应 [V20 固定提交](https://github.com/ruojimy/chinatravel-five-city/commit/6de12f41ce0e523afade7162807a5df3ea82b2bc)。本地校验公开收据和 625 个快照文件。十一城 `5e0ee8a` 仍用于兼容证据和历史复现，可显式传入 `--data ../ShortTrip/artifacts/data/5e0ee8a` 选择旧快照。

西安和厦门的新版景点类别、菜系、地理位置及有值的参考价格和营业信息进入本地目录。按日期收集的交通引用走单独的只读查询。以北京至厦门 2026-11-20 的航班为例：

```powershell
@'
{"from_city":"北京","to_city":"厦门","mode":"airplane","travel_date":"2026-11-20"}
'@ | Set-Content -Encoding UTF8 "$env:TEMP\shorttrip-v20-query.json"
& '..\ShortTrip\.venv\Scripts\python.exe' -B -X utf8 -m chinatravel.environment.sandbox --data '../ShortTrip/artifacts/data/6de12f4' tool intercity_search "$env:TEMP\shorttrip-v20-query.json"
```

`dated_observations` 只返回所查日期的展示记录，包含来源和未知余票标记。历史铁路报价可在同一只读入口查看。餐饮、住宿、景点的数字按快照参考解释。

## 旅行事实查询

旅行事实模块统一提供地点及其营业时段、价格状态、城际记录与离线接驳估算。结果带数据版本和来源证据。历史价格、估价、假设价、静态参考与未知分开表示。查询不需要模型密钥。

```powershell
& '..\ShortTrip\.venv\Scripts\python.exe' -B -X utf8 personalize.py facts --input result/experiments/P008_world_facts/SHANGHAI_INPUT.json --out result/manual/world_facts_new
```

已确认的 `tripspec_v1` 可直接作为 `facts --input` 的 JSON 输入，得到带需求版本、点名地点和筛选证据的 `WorldSnapshot`。也可向本地网页后端 `POST /facts` 发送相同 JSON。若要指定候选上限，发送 `{"action":"trip_snapshot","spec":<TripSpec>,"limit":12}`。通用查询支持 `snapshot`（默认）、`place`、`intercity` 和 `route`。复跑与逐题结果见 [P010 报告](result/experiments/P010_trip_world_snapshot/REPORT.md)。

## 输入

### 从快照生成体验构想

```powershell
& '..\ShortTrip\.venv\Scripts\python.exe' -B -X utf8 personalize.py sketch --input result/experiments/P010_trip_world_snapshot/run_02/human_69/WORLD_FACTS.json --out result/manual/sketch_new
```

结果 `TRIP_SKETCHES.json` 含每套构想的逐日地点和固定预约。带 `--with-model` 时，从本机环境读取 `DEEPSEEK_API_KEY` 发出一个批量构想请求。缺凭据或模型返回异常时仍可获得程序构想和原因。 [三条固定快照验收](result/experiments/P011_trip_sketches/REPORT.md)

### 试排构想与顺路优化

从个性化版目录回放三条固定用例。每次使用新的输出目录。

```powershell
& '..\ShortTrip\.venv\Scripts\python.exe' -B -X utf8 tools/replay_sketch_planning.py --out result/experiments/P012_sketch_planning/my_run
```

单条试排可用 `personalize.py trial --input <TripSpec> --snapshot <WorldSnapshot> --layouts <TripSketches> --out <目录>`。首份通过现有核验的候选保存为 `FIRST_CANDIDATE.json`。顺路优化可用 `personalize.py optimize-day --input <TripSpec> --snapshot <WorldSnapshot> --plan-file <候选文件> --day 3 --out <目录>`。两者都使用本地 V20。 [试排证据](result/experiments/P012_sketch_planning/REPORT.md)

### 独立评估与方案比较

用 `personalize.py assess --input <TripSpec> --snapshot <WorldSnapshot> --archive <候选档案> --out <目录>` 生成 `PLAN_ASSESSMENT.json`。结果含逐项硬核验、费用与接驳等质量分项、软偏好兑现、推荐和备选差值。 [实测和复跑](result/experiments/P013_plan_assessment/REPORT.md)

### 从确认页生成旅行方案

启动 `python -m chinatravel.personalization.web --port 8780` 后打开 `http://127.0.0.1:8780/tripspec`。核对需求后点击「按确认后的需求规划」。首份经评估的行程会先显示，最终可比较推荐和备选，并按天查看顺路安排。启动进程设置 `DEEPSEEK_API_KEY` 时尝试单次模型构想。需要离线跑单条时可用 `personalize.py trip-run --input <TripSpec> --out <新目录>`。 [运行与验收](result/experiments/P014_integrated_planning/REPORT.md)

[博物馆示例](examples/museum.json)、[四天预约](examples/four_day_appointment.json)、[六天示例](examples/six_days.json)可以直接运行或修改。

```json
{
  "start_city": "南京",
  "target_city": "上海",
  "days": 4,
  "people_number": 2,
  "intercity_modes": ["train"],
  "nature_language": "其他偏好和已定安排可以写在这里",
  "profile": {
    "interests": ["museum"],
    "pace": "relaxed",
    "mobility": "less_transit",
    "cuisines": ["本帮菜"],
    "hotel_features": ["行李寄存"]
  }
}
```

- interests：museum、history、nature、park、city、amusement，页面按目的地数据生成可用选项。
- pace：relaxed、balanced、full。mobility：less_walking、less_transit、public_transport。
- focus：balanced、cost、lodging_cost、food_share、more_attractions、near_restaurants。
- anchor：已知地点，偏好在周边游览；目前分项是快照坐标的直线距离，单列实际接驳。
- cuisines、hotel_features使用目的地实际属性；required_hotel_features表示必须满足，未知不能通过。
- 明确约束还包括预算、房间数、每间床数、交通方式、必去/排除地点、返程截止、预约及每日接驳步行/通行上限。表单与文字冲突时产生待确认项。

`request.json`保留原文、字段证据和未映射要求。模型参与每日地点集合推荐；Python入口加 `--interpret-model` 可额外调用一次模型补充自由文本软偏好，输出受原文证据与允许值约束。无法执行的硬条件阻止已核验交付，未知软偏好单列。

## 转换和离线检查

转换题集，不调用模型：

```powershell
& '..\ShortTrip\.venv\Scripts\python.exe' -B -X utf8 personalize.py convert --out result/experiments/conversion_new
```

覆盖604条基础题、300条偏好变体、50条基题副本、30条回归和60条西安单城题，共1044条来源记录，含重复基础需求。4–6天的72条基础题全部保留。requests.jsonl供规划器读取；references.jsonl只供独立评价；conversion_audit.jsonl保存待确认与未映射项。

准备单条需求，不搜索：

```powershell
& '..\ShortTrip\.venv\Scripts\python.exe' -B -X utf8 personalize.py prepare --input examples/six_days.json --out result/manual/prepare_six_days
```

重排已有真实候选，不重跑模型：

```powershell
& '..\ShortTrip\.venv\Scripts\python.exe' -B -X utf8 personalize.py replay --input '../combined version_1/experiments/c_only/queries/easy_1.json' --pool '../combined version_1/artifacts/product/EXP031_full_factorial/run/easy_1__api__baseline/pool.json' --out result/experiments/replay_new
```

## 结构和验证

| 位置 | 内容 |
|---|---|
| chinatravel/personalization/ | 需求、特征目录、编译、偏好策略、评价、规划适配和后端 |
| experiments/timing_probe/ | 复制的日集合与组合调度，新增显式偏好hook |
| web/index.html | 真实后端的表单、确认和规划状态 |
| BASELINE_MANIFEST.json | 245个复制基线文件的来源哈希 |
| result/experiments/ | 转换、候选重排、完整搜索检查、真实试跑与浏览器证据 |
| EXPERIMENT_LOG.md | 假设、失败、修正和实测结果 |
| docs/IMPLEMENTATION_PLAN.md | 架构方案，已取消1–3天范围限制 |

每条运行保留request、query_bundle、plan、STATUS、independent_audit；planning/下有候选池、路线、实际选案指纹、模型账本和模块计时。审核跟随实际选案，不能按旧分数另选一份。

测试：个性化、旧评分、日集合、组合调度和候选数检查共42项通过。三条真实DeepSeek试跑（含四天预约）均生成3份独立合法候选；浏览器端另实际生成并展示一份已核验行程。完整报告见 [P001验证](result/experiments/P001_report/REPORT.md)。固定历史推荐ID的回复检查另外保存，不计入真实模型性能。

## 错误记录与运行经验

[LESSONS.md](LESSONS.md)记录已确认的问题与解决办法。规划前读取[已验证规则](lessons/rules.json)，保存命中记录与规则快照；当前包括交通可用性、预约冲突和官方模型压缩兼容设置。

失败会返回稳定错误编号、阶段、具体消息与证据，写本次运行ERROR.json和全局result/incidents/。页面可展开错误详情。新错误先记录待复盘，只有经过验证才转为防护规则；时间耗尽与证据不足不会生成永久黑名单。

```powershell
& '..\ShortTrip\.venv\Scripts\python.exe' -B -X utf8 personalize.py lessons-report
```

此命令生成按错误特征归组的复盘Markdown，位于result/lessons/，不自动修改已验证规则。

## 当前边界

评分是待校准的工程初值。转换成功或ready不等于全部软体验都可执行；需同时看待确认项和未知分项。步行仅覆盖接驳，价格、设施和开放信息来自静态快照，需现实核实。独立咖啡停留、无障碍/过敏安全和部分外部已订项目仍待专门实现。

## 全城模型召回实验（P005）

在选择交通和酒店前，官方DeepSeek从本地全城景点表提出区域/主题方向。每日候选最多12个，先保留必去/当日预约，再最多6个全城推荐，其余保留原有偏好与地理候选；成功启用时在截断前去除已访问和其他日期预约。后续每日集合推荐、DFS排顺序与时间、硬核验、候选池选优继续沿用。

该模块目前为实验开关，默认baseline。trip_api一次性输入全城ID、类型、开放时间、参考价、坐标、确认需求与偏好；最多900输出token/30秒，非法ID或模型失败保存回退原因。模型方向不构成行程可行性证明。dedupe_only只调整截断前去重，用于消融。

```powershell
# 在后端环境中配置DEEPSEEK_API_KEY后执行，密钥不进入参数或结果文件。
& '..\ShortTrip\.venv\Scripts\python.exe' -B -X utf8 personalize.py plan --input 'result/experiments/P005_trip_recall_v2/inputs/guangzhou.json' --candidate-recall trip_api --seconds 180 --candidates 10

# 对照：其他参数相同，只将trip_api替换为baseline；消融替换为dedupe_only。
# 每轮输出使用新目录；不指定--out时自动生成新目录。
```

模型输入/方向/回退记录见planning/trip_recall.json；真实API调用见planning/requests.jsonl，其中trip_candidate_recall单独计时。第1轮失败及修复留在实验记录中，不与有效结果合并。完整流程对照见[result/experiments/P005_trip_recall_v2/REPORT.md](result/experiments/P005_trip_recall_v2/REPORT.md)，三臂排序控制对照见[result/experiments/P005_trip_recall_control/REPORT.md](result/experiments/P005_trip_recall_control/REPORT.md)。三条需求每臂一次，尚不足以证明普遍无退化。

P005实现与分析汇总见[result/experiments/P005_REPORT.md](result/experiments/P005_REPORT.md)。有效15次规划仅涉及3条不同需求；最终74项回归通过。当前证据不支持默认切换新召回。

## 既有题集扩大评测（P006）

用户指定使用原有prompt。本轮仅从datasets/ChinaTravel原始CSV取文本：easy、medium、human各6条，加同一基础题已有的6类偏好变体，24条×2臂；另外离线检查604条基础题和300条偏好变体的解析。没有自编场景或手工补要求。覆盖10个目的地、1–6天，18条基础需求与6条相关变体不能视作24个独立随机样本。

[主报告](result/experiments/P006_existing_prompts/REPORT.md)、[逐项分层结果](result/experiments/P006_existing_prompts/RESULTS.md)、[原始prompt及来源](result/experiments/P006_existing_prompts/PROMPTS.md)、[明细CSV](result/experiments/P006_existing_prompts/RESULTS_TABLE.csv)。LESSONS.md的L013–L017记录需求丢失、重复失败、空行程、参考规则和状态证据问题；这些新诊断尚未启用自动修复规则。

对照baseline与trip_api，使用同一官方deepseek-flash、数据快照、180秒规划预算和10候选目标。相同输入的交通/酒店排序使用首个真实回复控制差异，后续仍调用API计真实成本；日集合/全城推荐实时生成。参考DSL隔离，仅用于事后审核。整个实验期间不修改规划器，保留服务失败、超时和偏好未满足；网页默认召回仍为baseline。

复跑需使用**新目录**，并提前在后端配置DEEPSEEK_API_KEY；结果不保存密钥：

```powershell
& '..\ShortTrip\.venv\Scripts\python.exe' -B -X utf8 tools/evaluate_existing_prompts.py prepare --out result/experiments/P006_rerun
& '..\ShortTrip\.venv\Scripts\python.exe' -B -X utf8 tools/evaluate_existing_prompts.py run --out result/experiments/P006_rerun
```

不调用模型、只重新汇总已保存的结果：

```powershell
& '..\ShortTrip\.venv\Scripts\python.exe' -B -X utf8 tools/report_existing_prompts.py result/experiments/P006_existing_prompts
& '..\ShortTrip\.venv\Scripts\python.exe' -B -X utf8 tools/diagnose_existing_runs.py result/experiments/P006_existing_prompts
& '..\ShortTrip\.venv\Scripts\python.exe' -B -X utf8 tools/conclude_existing_prompts.py result/experiments/P006_existing_prompts
```

最后一条仅在48次全部结束、请求和排序控制一致、规划器与数据哈希未变时生成结论。系统自身规则通过、原题参考通过、实际旅行体验是三个不同判断，不能只报告一个“成功率”。

## TripSpec 需求确认（P007）

新版入口接受表单、自然语言或两者结合，生成带来源的事实、硬要求、软偏好和待确认项。文字提交最多调用一次 DeepSeek Flash，纯表单无需模型。用户解决冲突后会保存新版本，按请求 ID 可重新打开。确认结果保存在 `result/tripspec/`。

```powershell
& '..\ShortTrip\.venv\Scripts\python.exe' -B -X utf8 -m chinatravel.personalization.web --port 8791
```

打开本机 `127.0.0.1:8791/tripspec`，旧主页也有「新版需求确认」入口。使用自然语言前在启动环境中配置 `DEEPSEEK_API_KEY`。本阶段交付以确认后的 TripSpec 为终点，现有行程生成仍走原入口。

真实 API 冻结对照、修复回放、逐字段结果和复查命令见 [P007 报告](result/experiments/P007_tripspec_evaluation/REPORT.md)。
