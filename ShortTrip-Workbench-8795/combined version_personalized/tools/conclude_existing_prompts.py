"""Finalize P006 only after all frozen original-prompt runs have finished."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools.compare_trip_recall import read,save,digest


def main(out):
    rows=read(out/'RESULTS.json');manifest=read(out/'MANIFEST.json');summary=read(out/'SUMMARY.json')
    assert len(rows)==48 and len({(r['case'],r['arm']) for r in rows})==48
    assert all(c['identical_request'] and c['shared_answers_equal'] for c in summary['controls'])
    source_root=ROOT.parent/'datasets/ChinaTravel';data_root=ROOT.parent/'ShortTrip/artifacts/data/5e0ee8a'
    integrity={'complete_runs':len(rows),'cases':len(manifest['cases']),
        'planner_code_unchanged':all(digest(ROOT/rel)==sha for rel,sha in manifest['code_sha256'].items()),
        'data_unchanged':all(digest(data_root/rel)==sha for rel,sha in manifest['data_snapshot'].items()),
        'source_csv_unchanged':all(digest(source_root/(ns+'.csv'))==v['sha256'] for ns,v in manifest['source_files'].items()),
        'same_request_both_arms':all(c['identical_request'] for c in summary['controls']),
        'same_ranking_answer_for_same_input':all(c['shared_answers_equal'] for c in summary['controls'])}
    for c in manifest['cases']:
        actual=read(out/'inputs'/(c['case']+'.json'))
        assert hashlib.sha256(actual['nature_language'].encode()).hexdigest()==c['prompt_sha256']
        assert not any(k in actual for k in ('hard_logic_py','preference_py','hard_logic'))
    integrity['original_prompt_hashes_verified']=True;integrity['reference_fields_absent_from_public_inputs']=True
    save(out/'FINAL_VERIFICATION.json',integrity)
    assert all(value is True for key,value in integrity.items() if key not in ('complete_runs','cases')), integrity
    status_mismatches=[]
    for r in rows:
        status=read(out/(r['case']+'__'+r['arm'])/'STATUS.json')
        reported=status.get('evidence',{}).get('candidate_count')
        actual=status.get('internal_candidates',0)
        if reported is not None and reported!=actual:
            status_mismatches.append({'case':r['case'],'arm':r['arm'],'evidence_candidate_count':reported,'internal_candidates':actual})
    save(out/'STATUS_CONSISTENCY_AUDIT.json',status_mismatches)
    b,n=summary['arms']['baseline'],summary['arms']['trip_api'];pairs=summary['paired_deltas_new_minus_old']
    pref=[]
    base=next(c['case'] for c in manifest['cases'] if c['uid']==manifest['base_uid'])
    for arm in ('baseline','trip_api'):
        for r in rows:
            if r['arm']==arm and (r['case']==base or r['source'].startswith('preference')):
                m=r['metrics'] or {};pref.append([r['case'],arm,r['state'],m.get('total_cost'),m.get('lodging_cost'),m.get('food_cost_share'),m.get('city_transit_minutes'),m.get('unique_attractions')])
    def fmt(v):return '—' if v is None else f'{v:.2f}' if isinstance(v,float) else str(v)
    lines=['# P006：既有prompt扩大评测与共同问题','',
        '**当前最需要改的是需求如何贯穿整个规划过程，以及失败信息如何影响后续搜索。全城模型召回可以改变候选方向，但本轮不支持直接将它升级为默认，也不能把系统自身的合法输出率当作满足原文的比例。**','',
        '## 实验范围','',
        '只使用已有CSV原文：easy、medium、human各6条，以及同一基础题已有的6类偏好变体，共24条、48次官方deepseek-flash规划。覆盖10个目的地、1–6天。原文逐字保留，无手工补偏好、预算或预约。抽样先覆盖天数，再覆盖城市/文本需求类型，固定哈希打破并列，选择发生在读取运行结果前。这是覆盖诊断，18条基础需求加6个相关变体不等于24个独立随机样本。',
        '每次180秒、目标10候选、同快照与模型、temperature=0、thinking disabled、单规划进程串行、交替顺序。固定相同输入的交通/酒店排序响应，仍实际调用API计时；日集合及全城召回实时生成。冻结代码全程运行，故障未被静默重跑覆盖。',
        'trip_api让模型从全城本地快照提出方向，并在每日候选截断前过滤已访问及其他日期预约；它没有引入实时地图/门票数据库。本轮比较这一整体开关，未另跑dedupe_only消融，不能把差异全部归因于新增模型调用。',
        '同时离线扫描604条基础题和300条偏好变体，共904条原文；参考DSL只在生成后复核，未向规划器提供题集答案。解析采用当前系统默认路径，没有为这次测试手工修补句子或开启不同的解析模式。','',
        '## 结果必须分开看','',
        '|指标|原召回|全城API召回|','|---|---:|---:|',
        f"|按当前系统规则产出合格选案|{b['current_legal']}/24|{n['current_legal']}/24|",
        f"|实际选案通过原题参考DSL|{b['reference_selected_pass']}/24|{n['reference_selected_pass']}/24|",
        f"|系统合格但原题参考未全通过|{b['reference_fail_despite_current_legal']}|{n['reference_fail_despite_current_legal']}|",
        f"|触及180秒规划上限|{b['timeout']}|{n['timeout']}|",
        f"|到时限且仍无合格选案|{b['states'].get('search_timeout',0)}|{n['states'].get('search_timeout',0)}|",
        f"|模型服务失败|{b['states'].get('model_request_failed',0)}|{n['states'].get('model_request_failed',0)}|",
        f"|收集至少10候选但首日景点前缀只有一种|{b['one_prefix_despite_10']}|{n['one_prefix_despite_10']}|",
        f"|系统合格案例首份耗时中位数/秒|{fmt(b['median_first_legal_seconds'])}|{fmt(n['median_first_legal_seconds'])}|",
        '', '中位数成功集合可能不同，不能据此单独宣布加速。原题DSL包含对“喜欢”的硬化标签，也漏掉一些体验条件，故参考未通过不全是明确硬约束错误，参考通过也不等于好玩。human_109两组因参考额外要求七人仅用一辆出租车而未通过，原文未要求一辆，此项单独标记待修订，表内保留原始结果。短途返程日没有景点不一定有问题；整趟只有往返交通则需要单独标记。',
        '',f"两组均产出系统合格选案的{len(pairs)}对中，新组首份更快{sum(p['first_legal_delta']<0 for p in pairs)}对；其中两组都通过参考DSL的只有{sum(p['reference_both_pass'] for p in pairs)}对。分项差值见SUMMARY.json，保留正负结果。",
        '', '原组4次到时限中的preference5已在约95.1秒取得首份，最终保留5候选；新组该题约35.0秒首份、收满10候选。它不是“原组没有行程”的案例。',
        '', '|两组均有系统合格选案的19对|新组更低/更短|相同|新组更高/更长|',
        '|---|---:|---:|---:|',
        *[f"|{label}|{sum(p[key]<0 for p in pairs)}|{sum(p[key]==0 for p in pairs)}|{sum(p[key]>0 for p in pairs)}|" for key,label in [('first_legal_delta','首份耗时'),('total_cost','费用'),('access_walk_minutes','已知接驳步行'),('city_transit_minutes','市内接驳'),('sightseeing_minutes','游览时长'),('unique_attractions','景点数量')]],
        '', '此表仅描述分项变化，不把更多景点/更低费用一概当作更好；还要看原文目标是否兑现。共同成功集合包含原题要求未通过的案例，不能据此宣称真实体验胜率。',
        '', '## 具体问题与证据','',
        '|问题|本轮证据|说明与适用边界|','|---|---|---|',
        '|原文到需求字段会漏要求|easy_258“选择飞机出行”仍允许飞机/火车，选案参考交通检查失败；全基础题扫描发现57条同类明确方式句式仍保留两种方式|这是解析/要求传递问题；先修复再谈对应搜索的性能。|',
        '|明确金额上限也可能完全消失|medium_052原文“花费不要超过7400元”，实际facts.budget=null，原组选案8566.85元仍通过系统检查|这是已确认的硬约束遗漏；原文遗漏不会被核验同一份缺损需求的独立检查发现。新组遇到连接故障，不能据此比较新组金额表现。|',
        '|解析还可能制造冲突|7条medium中的“6400人民币”等被截为“6400人”，导致人数冲突|有确定原文证据；地点重名要求确认则应保留，不能把所有阻塞都取消。|',
        '|软偏好和点名目标会与最终核验脱节|easy_114点名餐厅未安排；easy_196文化氛围没有可执行偏好，原题文化旅游区检查失败；medium_135桑拿酒店变成软偏好，两组最终均不满足参考设施项|需要明确硬/软/待确认，并让软要求也有兑现或取舍解释；文化氛围与单一标签是否等价需人工判断。|',
        '|检查触发过晚，重复试排明显违规候选|medium_132每餐上限70元，日志反复出现午餐1365元被拒；新组25.7万次executed_activity，180秒无结果|应把明确可判的属性限制下推到召回/动作生成，并使用当前剩余预算；不必等完整前缀排好。|',
        '|预算问题会在大量相似分支重复出现|medium_034原组32.7万次budget_lower_bound，180秒无结果；新组60.3秒首份、152秒完整流程，费用2298.38/2300元|全城召回有帮助的单例；只有1.62元快照预算余量，仍需向用户表达余量，不能承诺真实报价。|',
        '|明显冲突的交通组合仍先调用排序|easy_024两组0景点搜索，各5次返程排序，已试25个组合全因到达晚于返程被拒|仅证明已试组合的冲突，不证明全数据或现实世界无解；可以先作时间兼容连接再排序。|',
        '|候选数会掩盖决策覆盖不足|多例10个候选只有一个首日景点前缀，详见RESULTS_TABLE.csv|相同前缀可以产生有意义的用餐/时间变体，但不能替代区域、日期、酒店取舍的比较。|',
        '|合法性与旅行目的脱节|human_123两组各2个候选，选案只有广州—南京往返火车，没有任何游览，系统和参考DSL均通过|必须补充“有可用旅游体验”的交付条件；不得把零游览包装成轻松旅游。|',
        '|地点命中不足以满足体验深度|human_092要求迪士尼玩久一点，两组度假区均14:34–16:04，仅90分钟；“久一点”未映射|需地点级停留目标/可调整时长、体验说明；无需凭空给所有景点规定同一个时长。|',
        '|真实服务故障会中断规划|medium_052新组返程排序APIConnectionError，0候选|单列服务可靠性；不能据此认定算法劣于原组，也不能当无解。|',
        f'|成功状态仍保留初始化的失败证据|{len(status_mismatches)}次STATUS的evidence.candidate_count为0，但internal_candidates及实际候选池非0|cli.py先生成默认失败结果，成功后未更新该证据字段；本报告按实际候选池统计，不受此字段影响。接口状态应统一生成，防止界面和日志解释矛盾。|',
        '|评测标签有歧义/不一致|preference1组6条原文实际只要求尽量多参观，并未写少交通；不少变体本来就有多个目标|没有少交通目标不能直接算漏抽；需要逐字原文核验，保留多目标。另有“餐饮花费尽可能多，同时…景点”跨句误匹配多景点的确定例。|',
        '|参考检查可能添加原文没有的条件|human_109七人出行，原题参考要求每段出租车cars=1，原文没有此限制；两组选案使用2辆车，仅此项未通过，其余参考项通过|原题DSL通过数保留原始值，同时标记该项为参考规则待修订；不得为追求题集通过率强制七人挤进一辆车。|',
        '', '## 耗时解释','',
        'medium_132新组180秒：模型累计约69秒、CPU约104秒；路线查找约51.8万次，但实际路线计算约1.78万次、约4秒。缓存已减少路线计算，仍有大量重复节点展开和约束检查。原组同样超时。故当前难例既有模型成本，也有搜索与约束传播成本，不能只归因于API吞吐。',
        '这条难例两组日集合模型调用51/55次，唯一请求50/55个；medium_034原组48次日集合调用全部不同。因此只缓存完全一样的prompt无法解决主要重复推理，应复用带适用条件的失败证据。',
        '拒绝次数使用run.json的精确规则计数；PRUNE日志每类最多记录最初3个例子，只用于说明失败内容，不用日志行数估算全部拒绝，也不把25.7万次同类拒绝全归到一家餐馆。CPU、模型等待和嵌套计时存在重叠口径，不把所有字段相加当作总耗时。',
        '首份与最终收集时间分别列出。例如medium_115新组首份19.6秒，完整流程86.5秒；增加备选的计算成本与用户等待首份的时间不同。','',
        '## 可以怎样统一改进','',
        '**建议统一为“同一份需求账本 + 带约束传播的多方案搜索”。** 这里的账本是程序可执行的结构化状态，不是一份让模型自由解释的文字摘要。以下为设计方向，尚未改规划器、尚未证明效果。',
        '',
        '1. 原文每项要求保留来源、硬/软/待确认、适用日期/地点、数值单位、兑现状态和验证方法。模型负责理解表达和提出候选，程序核对实体与字段；不能默默丢弃“选择飞机”或把价格当人数。表单和自然语言走同一套需求表示。',
        '2. 需求进入统一约束服务：明确违法的交通、酒店和餐馆在进入排序/集合前排除；依赖行程状态的限制用剩余钱、剩余时间、未完成目标传播。全城召回、日集合、局部排程和最终检查使用同一状态，减少模块各自解释。',
        '3. 搜索维护有差异的部分方案，让不同区域、首日前缀和酒店/日期安排获得有限补全机会。已得到的合法行程保留；重复变体不能占满全部比较机会。对只剩一种可行方向的案例允许真实回退，不强凑多样性。',
        '4. 每次失败返回可复用的原因及适用条件。例如价格1365>70只排除当前餐费约束下的选项；某区域在剩余时间内无法接驳应调整当天区域/酒店/班次。超时、模型故障、数据缺失不能写成永久不可行规则。',
        '5. 交付同时检查硬要求、核心体验、费用/接驳/停留余量及未核实项。先产出一份真正可用的方案，再为有明显取舍的备选继续花预算；每个偏好告诉用户“已兑现、被取舍或无法核实”。',
        '',
        '这套方向能连接目前的DSL/需求编译、模型推荐、日集合、DFS、个性化、评分数据集和lesson，已有模块可以保留。实现上应先验证统一需求与提前过滤，再测试方向调度；不应同时替换所有模块后失去可归因的对照。局部排顺序继续使用现有DFS作为基线，之后再比较其他求解器。',
        '', '## 偏好变体的实际分项','',
        '这些是同一个已有基础题的6种既有改写，各自保留原文；结果变化不全能归因于单一偏好，因为部分变体还有多个目标。',
        '', '|原题/变体|召回|状态|费用|住宿费|餐费占比|接驳分|景点数|','|---|---|---|---:|---:|---:|---:|---:|']
    for values in pref:lines.append('|'+ '|'.join(map(fmt,values))+'|')
    lines+=['','## 复查与下一轮基线','',
        '[全部原始prompt](PROMPTS.md) · [逐项和分层报告](RESULTS.md) · [CSV](RESULTS_TABLE.csv) · [实际瓶颈与原始日志行](BOTTLENECKS.json) · [原题参考未通过项](REFERENCE_FAILURES.json) · [完整性核对](FINAL_VERIFICATION.json)',
        '', '每例目录包含原文解析、实际输入、模型账本、组合试排、所有候选、实际选案、自身核验与原题核验。SOURCE_SNAPSHOT.zip保存冻结规划器，MANIFEST.json保存题集与数据哈希。服务失败、搜索耗尽、到时限和有结果但偏好未满足都保留原样。本轮仅扩展评测与诊断，没有改算法来消除观察到的失败。',
        '', '局限：每条每臂只跑一次，模型temperature=0也不保证完全确定性；计时包括本轮采集日志的开销。召回效果受未修复的解析缺陷影响，不能把“约束被漏掉所以搜得快”计为改善。180秒截断的无结果案例属于本预算下未完成。当前仅可定位覆盖样本中的机制与失败模式，不能估计全部904条的规划成功率，也没有用户满意度统计证据。本批未覆盖固定时刻预约的兑现，不代替此前预约测试；没有人为注入服务故障或编造扰动需求。',
        '', '复现：在后端配置DEEPSEEK_API_KEY后执行`python tools/evaluate_existing_prompts.py prepare --out result/experiments/<新目录>`，再执行同目录的`run`。生成报告使用report_existing_prompts.py、diagnose_existing_runs.py、conclude_existing_prompts.py。180秒是本轮固定规划预算，未证明增加到30分钟就一定有解；本地快照费用和营业信息不是实时查询结果。']
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'report':str(out/'REPORT.md'),'integrity':integrity},ensure_ascii=False))


if __name__=='__main__':main(Path(sys.argv[1]).resolve())
