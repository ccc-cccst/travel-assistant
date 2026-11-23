"""Summarize P070 artifacts without making or repairing any travel plans."""
import json
from pathlib import Path
from check_ppt_scenario import ROOT, OUT, CASES, load, save

def review_product(arm,case):
    folder=OUT/arm/case
    row=load(folder/'row.json')
    spec=load(folder/'SPEC.json')
    entry={'arm':arm,'case':case,'row':row,
           'extracted':[{k:i.get(k) for k in ('field','operator','strength','value','scope')} for i in spec['items']]}
    if not (folder/'RESULT.json').exists(): return entry
    result=load(folder/'RESULT.json')
    if not result.get('group'):return entry
    recommended=next(v for v in result['group']['versions'] if v['id']==row['candidate'])
    plan=recommended['plan']
    report=load(ROOT/'result/manual'/row['job']/'PLAN_ASSESSMENT.json')
    assessed=next(c for c in report['candidates'] if c['id']==row['candidate'])
    trains=[{'day':d['day'],'service':a.get('TrainID'),'from':a['start'],'to':a['end'],
             'departure':a['start_time'],'arrival':a['end_time']}
            for d in plan['itinerary'] for a in d['activities'] if a['type']=='train']
    entry['checks_from_raw_text']={
        'two_people':plan['people_number']==2,
        'three_days':len(plan['itinerary'])==3,
        'nanjing_to_shanghai':plan['start_city']=='南京' and plan['target_city']=='上海',
        'roundtrip_highspeed_in_snapshot':len(trains)==2 and all(t['service'].startswith('G') for t in trains),
        'return_nanjing_day3':bool(trains) and trains[-1]['day']==3 and '南京' in trains[-1]['to'],
        'reference_budget_le_6000':assessed['quality_vector']['total_cost_cny']<=6000,
        'museum_present':any(a['type']=='attraction' and '博物馆' in a.get('position','') for d in plan['itinerary'] for a in d['activities']),
        'shanghai_cuisine_in_snapshot':any(g['field']=='cuisine' and g['value']=='本帮菜' and g['status']=='matched' for g in assessed['goals']),
    }
    if case=='appointment_canonical':
        entry['checks_from_raw_text']['appointment_day2_1500_1600']=any(d['day']==2 and a.get('position')=='上海博物馆' and a['start_time']=='15:00' and a['end_time']=='16:00' for d in plan['itinerary'] for a in d['activities'])
    entry.update(metrics=assessed['quality_vector'],trains=trains,
        goals=[{k:g.get(k) for k in ('field','value','status','metric','observed','direction')} for g in assessed['goals']],
        timeline=[{'day':d['day'],'activities':[{'type':a['type'],'place':a.get('position',a.get('_title')),'start':a['start_time'],'end':a['end_time']} for a in d['activities']]} for d in plan['itinerary']],
        first_including_interpretation_s=round(row['interpret_s']+row['first_s'],2),
        final_including_interpretation_s=row['elapsed_s'],
        model_usage={'interpretation':row.get('model_interpretation'), 'planning':row.get('model_planning')})
    return entry

def main():
    products=[]
    for arm in ('shorttrip_deepseek','shorttrip_qwen'):
        products.extend(review_product(arm,c) for c,_ in CASES)
    products.append(review_product('shorttrip_deepseek_supplement','appointment_canonical'))
    native={c:load(OUT/'chinatravel_deepseek_r03_effective'/c/'row.json') for c in ('basic','preferences','appointment')}
    direct=load(OUT/'direct_deepseek/SUMMARY.json')
    repaired=load(OUT/'direct_repair_deepseek/SUMMARY.json')
    save(OUT/'REVIEW.json',{'product':products,'native':native,'direct':direct,'direct_repair':repaired})
    lines=['# P070：PPT 贯穿需求的实际测试','',
        '运行日期：2026-11-23。旅行日期表单：2026-11-27。目的：选择能实际演示、也能暴露能力边界的贯穿案例。本次是构造案例试验，不报告总体准确率或架构加速倍率。','',
        '## 1. 固定需求','']
    for name,raw in CASES:lines.extend([f'**{name}**',f'> {raw}',''])
    lines.extend(['原始需求未说明预约地点、时长，且“晚上返回”未被当前接口落实为可执行时间条件。因此原始需求只做解析测试，不补造预约。',
        '补充诊断 `appointment_canonical` 只将“上海博物馆（人民广场馆）”替换为“上海博物馆”。不覆盖原预约案例。','',
        '## 2. 比较设置与边界','',
        '- 当前产品：8795 的真实解析和规划接口；DeepSeek Flash 与本地 qwen3.5-4b-q8；产品原有搜索 60 秒、15 候选停止规则。',
        '- ChinaTravel：f445e00 的 LLMDrivenAgent、原始 DSL 翻译、排序、DFS 和路线工具；DeepSeek Flash，180 秒外层保护。模型调用可能使外层时间略超限。',
        '- 直接生成：复用当前产品已提取需求及同一 WORLD_FACTS，DeepSeek 一次排程，由相同路线数据补接驳并核验；4096 输出 token、60 秒请求超时。不含自然语言解析耗时。',
        '- 反馈修正：在直接生成基础上再给一次模型调用，提供原草案、计算后的路线、具体核验错误；没有搜索。这是额外修正机会，单列成本。',
        '- 原版第一次运行的 cwd 错误为实验接线问题。第二次直接绑定原始 V20 CSV 出现名称/坐标不兼容：上海719条景点里359条英文名没有同名POI。两次失败保留，不能算原算法能力差。',
        '- `_r03_effective` 改用当前产品实际可见的地点表和 POI；见 EFFECTIVE_DATA.json。城际表仍来自同一V20文件。原版和当前产品的路线估算、候选筛选、提示和停止规则仍不同，不构成严格同预算消融。',
        '- 当前产品的 TripSpec 与原版生成的 hard_logic/hard_logic_py 分别记录，不能混称同一套DSL成功率。程序核验依据固定快照，不等于当日余票、房型、价格或开门情况已查询。','',
        '## 3. 当前产品结果','',
        '| 模型 | 需求 | 实际结果 | 首份含解析（秒） | 总候选 / 展示备选 |',
        '|---|---|---|---:|---:|'])
    for p in products:
        r=p['row'];label='DeepSeek' if 'deepseek' in p['arm'] else '本地Qwen'
        lines.append(f"| {label} | {p['case']} | {r.get('outcome',r.get('state'))} | {p.get('first_including_interpretation_s','—')} | {r.get('candidate_count','—')} / {r.get('alternatives','—')} |")
    lines.extend(['','首份时间来自客户端观测；包括需求解析与提交后首次看见候选。机器缓存、模型缓存及运行顺序不同，不据此比较三条需求的难度。','',
        '普通和偏好需求在云端/本地生成的首推分别相同：模型接入成功不意味着两种模型的排程能力相同。本地两条的模型草图采用数均为0，最终结果包含程序构造与核验的贡献。',
        '明确预约（带馆名括号）在DeepSeek下解析成功，后续地点落库失败；本地Qwen在预约/日期来源校验处需确认。规范名称的补测成功，不能宣称原名称已修复。','',
        '## 4. 真实行程分项与原话核对','',
        '| 需求 | 整团参考费用 | 市内接驳分钟 | 接驳步行分钟 | 游览分钟 | 景点数 |',
        '|---|---:|---:|---:|---:|---:|'])
    for p in products:
        if 'deepseek' not in p['arm'] or 'metrics' not in p:continue
        q=p['metrics']
        lines.append(f"| {p['case']} | {q['total_cost_cny']} | {q['city_transit_minutes']} | {q['access_walk_minutes']} | {q['visit_minutes']} | {q['distinct_attractions']} |")
    lines.extend(['','`checks_from_raw_text` 在 REVIEW.json 中逐项检查人数、天数、城市、往返高铁、第三天返程、参考预算、博物馆、本帮菜及补测预约。结果均成立于生成行程及快照。',
        '加入“少走路、轻松”后：接驳64→59分钟，但接驳步行8→10分钟，费用3074.76→3969.74元。游览420→330分钟、景点6→4只描述安排变化，不证明更舒服。景区内部步行缺数据，不能宣称全程少走路已达成。',
        '基础首推去程G7001 07:00–08:39；第三天G7114 17:57–20:14返回南京。虽然实际结果在晚上抵达，原始“晚上返回”没有对应时间条件，不能用碰巧满足替代约束提取。','',
        '## 5. ChinaTravel 原版结果与DSL诊断','',
        '| 需求 | 结果 | 实际耗时秒 | 模型调用 | 模型累计秒 | 输入/输出token |',
        '|---|---|---:|---:|---:|---:|'])
    for c,r in native.items():lines.append(f"| {c} | {r['state']} | {r.get('elapsed_s',0):.2f} | {r.get('model_calls','—')} | {r.get('model_seconds',0):.2f} | {r.get('input_tokens','—')} / {r.get('output_tokens','—')} |")
    lines.extend(['','180秒没有最终方案只表示本次预算内未找到；不得写成需求无解或30分钟也找不到。','',
        '普通和偏好需求生成的可执行DSL含有：每段城际交通起点必须为南京、终点必须为上海；又要求最后一天返回南京，两者矛盾。用当前真实往返行程作为反例，原版该约束返回False。见 DSL_COUNTEREXAMPLES.json。',
        '原始含糊预约的DSL则从result=True开始，找到15:00活动时仍只赋True；没有任何15:00活动也通过。反例实际返回True。这说明语法通过不能说明约束语义正确。',
        '本次“少走路”还被原版转换成硬性交通方式限制，偏好需求增加了用户没有指定的单间/床型要求。高铁只映射为train，原版搜索日志选了T135，不能当作满足高铁。原始缺失预约也没有完善澄清流程。',
        '因此本次数据支持检查“需求→约束→候选信息→搜索”的整条链路，不能单凭超时归因DFS。','',
        '## 6. 直接生成与一次反馈修正','',
        '| 需求 | 首次核验通过数 | 首次问题 | 一次修正后通过数 | 修正后问题 |',
        '|---|---:|---|---:|---|'])
    for a,b in zip(direct,repaired):
        lines.append(f"| {a['case']} | {a['verified']} | {', '.join(a.get('rejections',{})) or a['state']} | {b['verified']} | {', '.join(b.get('rejections',{})) or b['state']} |")
    lines.extend(['','基础首次草案19:00发车，计算后18:48才到站，只有12分钟，低于事先写入提示和核验器的60分钟缓冲。不能说一定误车，但不满足本试验政策。预约首次草案19:18才到19:00班次的车站，属于真实时序冲突。',
        '预约草案其实排了第二天上海博物馆15:00–16:00；appointment_time/requirement_evidence还包含系统馆名别名落库问题，不应将这一项全归因模型漏预约。它仍有营业时间/接驳等独立错误。',
        '单次生成和单次修正结果不代表通用模型上限。要判断搜索是否必要，应继续做固定题集、同一预算、同一规则与路线、重复运行的对照。','',
        '## 7. PPT建议与复查入口','',
        '主线先使用basic原文；偏好、明确预约、模糊预约作为递进测试。不要把所有复杂条件揉成一条已经全面解决的示范。日期从表单提供，价格和班次按快照标注。',''])
    for p in products:
        if 'deepseek' in p['arm'] and 'metrics' in p:
            lines.append(f"- [{p['case']} 实际行程]({p['row']['url']})")
    lines.extend(['','复现脚本：tools/check_ppt_scenario.py、tools/check_ppt_supplement.py、tools/summarize_ppt_scenario.py。原始输入、模型回复、DSL、快照、核验、失败日志均保留在此目录。正式复跑需要新标签/目录，禁止覆盖本次结果。'])
    (OUT/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(str(OUT/'REPORT.md'))

if __name__=='__main__':main()
