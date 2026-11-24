"""Build the isolated C-group rule variant exercised by EXP016."""

import ast
import shutil


EXTRA_RULES = '''

def required_paid_meal_floor(agent, query, obligations, restaurants):
    if restaurants is None:
        return 0
    city = query.get('target_city')
    cache = getattr(agent, '_restaurant_price_floor', {})
    key = (city, id(restaurants))
    if key not in cache:
        prices = [number(price) for price in restaurants['price']]
        cache[key] = min((activity_unit_price(price, city) for price in prices), default=0) if all(
            price is not None for price in prices) else 0
        agent._restaurant_price_floor = cache
    required = sum(o['status'] == 'required' and o['kind'] in ('lunch', 'dinner') for o in obligations)
    return cache[key] * required


def pre_route_meal_conflict(agent, poi, pending, current_day, current_time):
    if not pending:
        return False
    try:
        optimistic = agent.attraction_slot(current_time, poi['opentime'], poi['endtime'], 90)
        if optimistic is None:
            return False
        finished = current_day * 1440 + clock(optimistic[1])
    except (ValueError, TypeError, KeyError):
        return False
    for meal in pending:
        if not future_meal_possible(meal, finished):
            agent.record_prune(reason('pre_route_meal_window', meal['kind'], day=meal['day'],
                                      position=poi['name'], earliest_finish=optimistic[1]))
            return True
    return False
'''


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f"Frozen source must contain one match for {old!r}")
    return text.replace(old, new)


def build(source, target):
    rec = source / 'chinatravel/agent/nesy_agent/llm_driven_rec.py'
    tree = ast.parse(rec.read_text(encoding='utf-8'))
    method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'select_poi_time')
    first = method.body[0]
    if not isinstance(first, ast.Return) or not isinstance(first.value, ast.Constant) or first.value.value != 90:
        raise ValueError('The frozen visit duration must be 90 minutes')
    shutil.copytree(source, target, ignore=shutil.ignore_patterns('__pycache__'))
    rules = target / 'experiments/c_only/meal_pruning.py'
    text = rules.read_text(encoding='utf-8')
    text = replace_once(text, 'from rules import clock, ticket_interval',
                        'from rules import clock, future_meal_possible, ticket_interval')
    text = replace_once(text, '''    rejected, food = meal_lower_bound(agent.requirements(poi_plan),
                                     getattr(agent, 'memory', {}).get('restaurants'),
                                     go['end'], back['start'], hotel_days, query.get('target_city'))''',
                        '''    obligations = agent.requirements(poi_plan)
    restaurants = getattr(agent, 'memory', {}).get('restaurants')
    rejected, food = meal_lower_bound(obligations, restaurants, go['end'], back['start'],
                                     hotel_days, query.get('target_city'))''')
    text = replace_once(text, '''        return exceeded(query, fixed + food * query['people_number'])''',
                        '''        price_floor = required_paid_meal_floor(agent, query, obligations, restaurants)
        rejected = exceeded(query, fixed + max(food, price_floor) * query['people_number'])
        if rejected and price_floor > food and not exceeded(query, fixed + food * query['people_number']):
            rejected['rule'] = 'mandatory_meal_budget_floor'
        return rejected''')
    rules.write_text(text + EXTRA_RULES, encoding='utf-8', newline='\n')

    dfs = target / 'experiments/c_only/dfs_snapshot.py'
    text = dfs.read_text(encoding='utf-8')
    prefix, attraction = text.split('            elif poi_type == "attraction":\n')
    body, suffix = attraction.split('            # The last event in a day: hotel or go-back\n', 1)
    body = replace_once(body, '                for sea_i, r_i in enumerate(ranking_idx):',
                        '''                from meal_pruning import pre_route_meal_conflict
                pending_meals = self.missing_today(plan, poi_plan, current_day) if self.group == 'C' else ()
                for sea_i, r_i in enumerate(ranking_idx):''')
    body = replace_once(body, '                        poi_sel = self.memory["attractions"].iloc[attr_idx]',
                        '''                        poi_sel = self.memory["attractions"].iloc[attr_idx]
                        if pre_route_meal_conflict(self, poi_sel, pending_meals, current_day, current_time):
                            continue''')
    text = prefix + '            elif poi_type == "attraction":\n' + body + '            # The last event in a day: hotel or go-back\n' + suffix
    dfs.write_text(text, encoding='utf-8', newline='\n')
    for path in (rules, dfs):
        ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    return [path.relative_to(target).as_posix() for path in (rules, dfs)]
