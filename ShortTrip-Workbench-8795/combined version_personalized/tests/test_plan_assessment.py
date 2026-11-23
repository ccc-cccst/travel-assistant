from copy import deepcopy
import unittest

from chinatravel.personalization.cli import ROOT, read
from chinatravel.personalization.plan_assessment import _goal_results, _hard_checks, select_candidates


P010 = ROOT / 'result/experiments/P010_trip_world_snapshot/run_02'


def place_evidence(grounding, name):
    p = next(p for p in grounding['records'] if p['name'] == name)
    return {'id': p['id'], 'name': name, 'day': 1, 'tags': p['tags']['values']}


def candidate(name, cost, transit, walk, visit, goals=()):
    return {'id': name, 'goals': list(goals),
            'quality_vector': {'total_cost_cny': cost, 'city_transit_minutes': transit,
                               'access_walk_minutes': walk, 'visit_minutes': visit}}


class AssessmentTests(unittest.TestCase):
    def test_extra_visit_is_recommended_only_with_a_small_tradeoff(self):
        parent = candidate('original', 2000, 150, 30, 300)
        for cost, transit, walk, minutes, preferred in [
            (2010, 155, 30, 390, 'fuller'), (2150, 155, 30, 390, 'original'),
            (2010, 200, 30, 390, 'original'), (2010, 155, 60, 390, 'original'),
            (2010, 155, 30, 315, 'original')]:
            child = {**candidate('fuller',cost,transit,walk,minutes), 'visit_refined_from':'original'}
            self.assertEqual(select_candidates([parent,child])['recommended_id'], preferred)

    def test_local_budget_excludes_ticket_and_hotel_but_counts_terminal_transfer(self):
        plan = {'itinerary': [{'day': 1, 'activities': [
            {'type': 'train', 'cost': 800, 'transports': [{'cost': 60}]},
            {'type': 'accommodation', 'cost': 350, 'transports': []},
            {'type': 'lunch', 'cost': 100, 'transports': []},
        ]}]}
        req = {'id': 'r', 'field': 'budget', 'operator': 'lte', 'unit': 'CNY/person', 'strength': 'hard',
               'scope': 'without_intercity_and_lodging', 'value': 80, 'source': {}}
        report = {'total_cost': 1310, 'issues': []}
        checked, = _hard_checks([req], [], plan, report, 2)
        self.assertEqual(checked['observed'], {'cny_per_group': 160, 'limit': 160})
        self.assertEqual(checked['status'], 'passed')
        plan['itinerary'][0]['activities'][0]['transports'][0]['cost'] = 61
        report['total_cost'] = 1311
        checked, = _hard_checks([req], [], plan, report, 2)
        self.assertEqual(checked['status'], 'violated')

    def test_exemplar_accepts_a_related_attraction_only_with_catalog_evidence(self):
        landmark = {'id': 'attraction:1', 'kind': 'attraction', 'tags': {'values': ['游乐园']}}
        grounding = {'requirement_id': 'r', 'status': 'found', 'records': [landmark]}
        req = dict(id='r', source={}, field='experience_example', value='欢乐谷', strength='fact',
                   operator='example_of', scope='total_trip')
        snap = {'place_groundings': [grounding], 'requirements': [req]}
        other = {'id': 'attraction:2', 'tags': ['游乐园']}
        def status():
            goal, = _goal_results(snap, [other], {})
            return goal['status']
        self.assertEqual(status(), 'matched')
        other['tags'] = ['博物馆']
        self.assertEqual(status(), 'unmet')
        grounding['status'] = 'ambiguous'
        self.assertEqual(status(), 'insufficient_evidence')

    def test_disney_town_does_not_count_as_the_exact_resort(self):
        snapshot = read(P010 / 'human_69/WORLD_FACTS.json')
        original, = snapshot['place_groundings']
        town = place_evidence(original, '迪士尼小镇')
        resort = place_evidence(original, '上海迪士尼度假区')
        ambiguous, = _goal_results(snapshot, [town], {})
        self.assertEqual(ambiguous['status'], 'insufficient_evidence')
        self.assertEqual(ambiguous['reason'], 'ambiguous_reference')

        confirmed = deepcopy(snapshot)
        grounding, = confirmed['place_groundings']
        grounding.update(status='found', query=resort['name'],
                         records=[p for p in original['records'] if p['id'] == resort['id']])
        item = next(i for i in confirmed['requirements'] if i['id'] == grounding['requirement_id'])
        item['value'] = resort['name']
        missed, = _goal_results(confirmed, [town], {})
        matched, = _goal_results(confirmed, [resort], {})
        self.assertEqual(missed['status'], 'unmet')
        self.assertEqual(matched['status'], 'matched')

    def test_recommendation_and_cheaper_tradeoff_preserve_raw_figures(self):
        short = candidate('short', 7400, 55, 6, 300)
        cheap = candidate('cheap', 1700, 750, 105, 180)
        dominated = candidate('dominated', 7800, 68, 10, 280)
        result = select_candidates([short, cheap, dominated])
        self.assertEqual(result['recommended_id'], 'short')
        self.assertEqual(result['frontier_ids'], ['short', 'cheap'])
        alternate, = result['alternatives']
        self.assertEqual(alternate['id'], 'cheap')
        self.assertEqual(alternate['delta_from_recommended'], {
            'total_cost_cny': -5700, 'city_transit_minutes': 695,
            'access_walk_minutes': 99, 'visit_minutes': -120,
        })

    def test_an_explicit_user_measure_drives_selection(self):
        price = dict(requirement_id='p1', field='total_cost', operator='minimize',
                     direction='minimize', status='measured')
        short = candidate('short', 7400, 55, 6, 300, [{**price, 'observed': 7400}])
        cheap = candidate('cheap', 1700, 750, 105, 180, [{**price, 'observed': 1700}])
        self.assertEqual(select_candidates([short, cheap])['recommended_id'], 'cheap')

    def test_single_or_empty_returns_no_artificial_alternative(self):
        result = select_candidates([candidate('only', 500, 29, 10, 65)])
        self.assertEqual(result['recommended_id'], 'only')
        self.assertFalse(result['alternatives'])
        self.assertEqual(result['reason'], 'only_verified_candidate')
        self.assertIsNone(select_candidates([])['recommended_id'])

    def test_cost_gap_below_display_threshold_does_not_make_a_backup(self):
        result = select_candidates([
            candidate('minutes', 500, 29, 10, 65), candidate('pennies', 498, 30, 10, 65),
        ])
        self.assertEqual(result['recommended_id'], 'pennies')
        self.assertFalse(result['alternatives'])

    def test_small_transfer_gap_leaves_room_for_cost_to_decide(self):
        short = candidate('short', 1178, 435, 97, 180)
        cheap = candidate('cheap', 780, 446, 136, 390)
        self.assertEqual(select_candidates([short, cheap])['recommended_id'], 'cheap')

    def test_close_alternatives_do_not_both_use_a_slot(self):
        result = select_candidates([
            candidate('original', 500, 15, 10, 65),
            candidate('longer', 600, 30, 10, 100),
            candidate('almost_same', 610, 31, 10, 101),
        ])
        self.assertEqual(result['recommended_id'], 'original')
        self.assertEqual([option['id'] for option in result['alternatives']], ['longer'])

    def test_relaxed_pace_uses_transfer_burden(self):
        snapshot = read(P010 / 'four_day_appointment/WORLD_FACTS.json')
        pace = next(r for r in snapshot['requirements'] if r['field'] == 'pace')
        snapshot['requirements'] = [pace]
        goal, = _goal_results(snapshot, [], {'peak_daily_city_transit_minutes': 27})
        self.assertEqual((goal['status'], goal['metric'], goal['observed']),
                         ('measured', 'peak_daily_city_transit_minutes', 27))

    def test_full_pace_prefers_more_active_days_and_shows_the_cheaper_option(self):
        pace = dict(id='pace',field='pace',strength='soft',value='充实',operator='prefer',source={})
        snapshot = {'requirements':[pace],'place_groundings':[]}
        full = candidate('full',6846,200,35,450,_goal_results(snapshot,[],{'full_visit_days':3}))
        light = candidate('light',6735,216,45,330,_goal_results(snapshot,[],{'full_visit_days':1}))
        full['quality_vector']['full_visit_days']=3
        light['quality_vector']['full_visit_days']=1
        selection = select_candidates([light,full])
        self.assertEqual(selection['recommended_id'],'full')
        alternative, = selection['alternatives']
        self.assertEqual(alternative['id'],'light')
        self.assertEqual(alternative['delta_from_recommended']['full_visit_days'],-2)

    def test_full_pace_keeps_long_detour_as_a_tradeoff(self):
        pace = dict(id='pace',field='pace',strength='soft',value='充实',operator='prefer',source={})
        snapshot = {'requirements':[pace],'place_groundings':[]}
        packed = candidate('packed',1970,335,40,300,_goal_results(snapshot,[],{'full_visit_days':2}))
        nearby = candidate('nearby',1753,165,42,240,_goal_results(snapshot,[],{'full_visit_days':1}))
        packed['quality_vector'].update(full_visit_days=2,other_city_transit_minutes=251)
        nearby['quality_vector'].update(full_visit_days=1,other_city_transit_minutes=112)
        cheaper = candidate('cheaper',1600,280,35,180,_goal_results(snapshot,[],{'full_visit_days':1}))
        cheaper['quality_vector'].update(full_visit_days=1,other_city_transit_minutes=210)
        selection = select_candidates([packed,nearby,cheaper])
        self.assertEqual(selection['recommended_id'],'nearby')
        alternative = selection['alternatives'][0]
        self.assertEqual(alternative['id'],'packed')
        self.assertIn('full_visit_days',alternative['improvements'])

    def test_tag_aliases_preserve_evidence_and_require_destination_for_local_cuisine(self):
        source = {'id': 'p1', 'field': 'cuisine', 'strength': 'soft', 'value': '当地特色菜',
                  'operator': 'prefer', 'scope': 'food', 'source': {}}
        snapshot = {'destination': '上海', 'requirements': [source], 'place_groundings': []}
        shanghai = {'id': 'restaurant:1', 'name': '店一', 'day': 1, 'tags': ['本帮菜']}
        italian = {**shanghai, 'id': 'restaurant:2', 'tags': ['西餐']}
        goal, = _goal_results(snapshot, [shanghai, italian], {})
        self.assertEqual(goal['status'], 'matched')
        self.assertEqual(goal['matched_places'], [shanghai])
        self.assertEqual(_goal_results(snapshot, [italian], {})[0]['status'], 'unmet')
        self.assertEqual(_goal_results({**snapshot, 'destination': '未映射城市'}, [italian], {})[0]['status'],
                         'insufficient_evidence')

        snapshot['requirements'] = [{**source, 'field': 'experience_theme', 'value': '历史文化景点',
                                     'scope': 'local_experience'}]
        monument = {'id': 'attraction:1', 'name': '南京总统府', 'day': 1, 'tags': ['历史古迹']}
        generic = {**monument, 'id': 'attraction:2', 'tags': ['人文景观']}
        goal, = _goal_results(snapshot, [monument, generic], {})
        self.assertEqual(goal['matched_places'], [monument])


if __name__ == '__main__':
    unittest.main()
