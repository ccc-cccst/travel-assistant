# ShortTrip 独立评分与人工校准

实现版本：`shorttrip_eval_v0.1`。评分公式与统计口径见 [DESIGN.md](DESIGN.md)。这是独立离线评测器，不会修改DFS、替换其候选选择器或自动启动模型。

## 快速使用

在ShortTrip根目录运行，输出目录必须是新目录：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m experiments.evaluation.run --input artifacts/prefix_probe/EXP001_easy_1/rules_n200_c0 --out artifacts/evaluation/my_fast_run --baseline rank_fixed
```

默认`fast_rules_v0`，CPU离线运行，模型请求数为0。对每个方法实际选择的最终行程做独立合法性检查，再计算Q/P/S，输出REPORT.md、summary.json、plan_scores.jsonl、run_metrics.jsonl、pairs.jsonl、budget_curve.jsonl和blind_records.jsonl。

可选`--all-candidates`评估候选池，供评分诊断/人工盲选使用；它不会改变主报告中算法实际选中的方案。历史日志缺少发布时间，严格预算指标显示不适用，旧结果仅进入历史回放对比。

## 两种评分配置

| 配置 | 质量 | 偏好 | 模型不可用时 |
| --- | --- | --- | --- |
| fast_rules_v0 | 交通、节奏、换酒店、重复访问，固定权重归一化 | 已复核且可以规则计算的偏好 | 正常运行；需要语义评价的偏好保持未评 |
| full_v0 | 上述规则加体验价值 | 规则偏好加明确自由文本偏好 | 完整Q/P/S按所缺分项留空，不换算为快速分 |

Q/P/S为0—100，越高越好；旧total_penalty越低越好，单独展示。未知、未配置、无偏好、不适用、失败分别处理，不能用0或100混代。非法方案不获得正式质量分。

默认权重、节奏区间和3分平局容差尚未经过充分人工校准，输出明确标为工程代理评分。尤其，当前节奏规则会对占满可用时段的行程扣分；这是否符合不同用户的感受，需要独立验证。

## 配置和需求

`--config <json>`覆盖config.v0.json中的配置。支持quality、validation、judge、tie_margin等节；有效模型配置与实现/资源哈希会记录到manifest。

`requirements.v0.json`是助手依据10个原始需求做的静态条目核对，不是真人偏好标注。硬预算、必去地点等继续由数据集标准约束复核；不再作为重复软奖励。需求内容变化时原核对自动失效。新需求可以通过manifest提供requirements，或单需求运行时使用`--requirements <json>`。

规则偏好示例（从明确用户原话提取后冻结）：

```json
{
  "reviewed": true,
  "items": [{
    "id": "walking", "kind": "soft", "method": "rule",
    "feature": "walking_minutes", "mapping": "upper_bound",
    "thresholds": {"target": 45, "scale": 45},
    "aggregation": "day_equal", "missing_policy": "unknown",
    "weight": 1, "evidence": ["每天步行最好控制在45分钟内"]
  }]
}
```

这两个45分别是用户目标与预先登记的超额容忍尺度；不得从新算法结果倒推。无偏好写`{"reviewed":true,"items":[]}`；未核对写`{"reviewed":false,"items":[]}`。完整字段/映射见features.py模块说明。

## 语义评分接口

judge.py使用OpenAI兼容的`/chat/completions`接口，支持本地Qwen。固定量表只输出体验E、明确语义需求满足度、活动ID证据和简短解释。输入隐藏方法身份，资源只发送行程引用的条目；完整资源哈希仍参与缓存键。

默认cache_only且模型精确版本未填写。真正调用需要配置`judge.mode="enabled"`、精确`model_version`（如本地模型文件SHA256）并显式传`--allow-judge-requests`。API密钥只从`api_key_env`指定的环境变量读取，不写入配置或报告。本轮没有开启真实请求。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m experiments.evaluation.run --input artifacts/prefix_probe/EXP001_easy_1/rules_n200_c0 --out artifacts/evaluation/my_full_run --config my_judge_config.json --profile full_v0 --judge-cache artifacts/evaluation/shared_judge_cache --allow-judge-requests
```

精确请求缓存包含需求、行程、资源、模型版本、量表和推理参数；复用不再计新模型调用。输出格式错误最多尝试2次，失败记unknown，保留原因。比较评分稳定性时使用不同缓存目录进行独立重复，不把缓存命中当成重复推理。

## 验证评分是否反映好坏

程序测试只能验证约束、计算和统计逻辑；不能替代用户体验验证。流程为：

1. 检查受控边界：违规不被高体验分抵消、缺失信息不奖励、偏好改变能影响P、低于原阈值的交通差异能被记录等。
2. 对真实合法候选生成盲选材料，隐藏算法和自动分数，保留完整时间、地点、接驳与费用。
3. 收集真人选择A/B/差不多/无法判断及原因，记录实际标签，不使用评分器自己生成真值。
4. 用开发需求调整权重/量表，冻结后在不同需求组上验证一致性；同时看不一致案例的原因。一次选对不证明评分可靠。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m experiments.evaluation.calibration export --records artifacts/evaluation/my_fast_run/blind_records.jsonl --out artifacts/evaluation/my_blind_review --max-pairs 12
```

导出blind_review.md与labels.template.jsonl。仅将这两份提供给标注者；private_key.json含方法与自动分数，不用于盲选。模板的winner全为null，填写后另存labels.jsonl。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m experiments.evaluation.calibration assess --key artifacts/evaluation/my_blind_review/private_key.json --labels artifacts/evaluation/my_blind_review/labels.jsonl --out artifacts/evaluation/my_blind_review/assessment.r1.json
```

按query_group整组拆分开发/验证集，同源改写不能跨集合。人工一致率按独立需求组等权，同时展示覆盖率、平局、未知和模型缺分。未标注时一致率为null；一个需求的很多行程对仍只是pilot，不建立泛化结论。

## 新实验的严格预算证据

旧日志不改写。新规划器可显式接入recorder.RunRecorder：在初始化前开始计时，完成自己的在线校验/选优后publish(plan)，结束时finish(reason)。publish会深度冻结快照，避免DFS回溯修改历史候选；计时在快照写入后记录。记录器不负责选择方案。

每个记录器生成可直接评测的manifest.json；多方法实验将各runs条目放进一个schema_version=1的manifest。每条至少包括run_id、method、case_id、query、resources、requirements、scope、protocol=published_v1、budget={kind:wall_seconds,limit:秒数}、repetition、order_id、events、selected_plan、candidates。JSON对象可内嵌，路径相对manifest解析。

评测只承认B之前的incumbent_published。没有发布证据、事件乱序、结束后新增发布均为测量unknown；不能使用最终selected.json倒填截止前状态。不同预算、需求、资源、评分配置或初始顺序不能误配。

首个结果晚到、最终交付晚到、用尽预算和硬超时分别记录。离线评测成本与规划耗时分开；只有将评分器用于在线选优时，才把相应成本纳入规划预算。

## 当前已验证范围

- 离线复核EXP001四组共595条候选记录，去重后524条行程；仍只有easy_1一个独立需求。
- 所选方案报告位于artifacts/evaluation/EXP004_fast_v0。完整候选池复测位于artifacts/evaluation/EXP004_pool_v0_r2，附VERIFICATION.json。
- 12对真实候选盲选材料位于artifacts/evaluation/EXP004_blind_pilot_v0。实际人工标注与评估文件逐次追加，空模板保留。
- 已取得P0001的一条用户标注。自动分和用户都选A。用户关注游览偏晚，规则排序主要反映交通负担。时段平移探针已确认快速分对这一变化不敏感。
- 当前独立validator支持原C组的单目的地往返协议；其他协议明确标为未支持。真实语义评分、多人一致性与跨需求泛化仍待验证。

测试：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s experiments/evaluation -p "test_*.py"
```

测试中的HTTP为mock；生产模型/GPU不启动。输出路径拒绝覆盖，新增运行使用新目录。
