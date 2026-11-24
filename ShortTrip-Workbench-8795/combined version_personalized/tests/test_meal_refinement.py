import unittest

from chinatravel.personalization.meal_refinement import _replacement, _try_meal
from chinatravel.planning.engine import clock


class LunchRoutes:
    def __init__(self, food):
        self.food = food

    def check(self):
        pass

    def distance(self, start, end):
        return 1

    def routes_to(self, start, end, depart, deadline, style):
        arrive = depart + (80 if start == '远处餐厅' else 10)
        if arrive > deadline:
            return []
        return [(0, arrive, [{'mode': 'walk', 'start_time': clock(depart), 'end_time': clock(arrive), 'cost': 0}])]

    def poi_activity(self, row, kind, start, end, route):
        return {'position': row['name'], 'price': row['price'], 'cost': row['price'], 'type': kind,
                'start_time': clock(start), 'end_time': clock(end), 'transports': route}


class MealRefinementTests(unittest.TestCase):
    def setUp(self):
        self.meal = {'position': '原餐厅', 'price': 100, 'cost': 100, 'type': 'lunch',
                     'start_time': '11:15', 'end_time': '12:15',
                     'transports': [{'mode': 'walk', 'start_time': '11:00', 'end_time': '11:15', 'cost': 0}]}
        self.train = {'type': 'train', 'start': '火车站', 'start_time': '14:00',
                      'transports': [{'mode': 'walk', 'start_time': '12:15', 'end_time': '12:45', 'cost': 0}]}

    def test_checks_the_outgoing_route_and_keeps_the_train_buffer(self):
        food = [{'name': name, 'price': price, 'cuisine': '中餐', 'opentime': '11:00', 'endtime': '21:00'}
                for name, price in [('远处餐厅', 10), ('附近餐厅', 75)]]
        option = _replacement(LunchRoutes(food), self.meal, self.train, '景点', 660, {'原餐厅'}, {}, 0)
        self.assertEqual(option[1]['position'], '附近餐厅')
        self.assertEqual(option[2][-1]['end_time'], '12:20')

    def test_recommendation_must_fit_the_existing_meal_start(self):
        restaurant = {'name': '附近餐厅', 'price': 75, 'opentime': '11:12', 'endtime': '21:00'}
        traveler = LunchRoutes([restaurant])
        self.assertEqual(list(_try_meal(traveler, restaurant, 'lunch', '景点', '火车站', 660,
                                        780, 60, fixed_start=670)), [])
        [(meal, onward)] = _try_meal(traveler, restaurant, 'lunch', '景点', '火车站', 660,
                                    780, 60, fixed_start=680)
        self.assertEqual(meal['start_time'], '11:20')
        self.assertEqual(onward[-1]['end_time'], '12:30')

    def test_preferred_cuisine_still_respects_the_remaining_group_budget(self):
        food = [{'name': '火锅店', 'price': 160, 'cuisine': '火锅', 'opentime': '11:00', 'endtime': '21:00'}]
        goals = {'goal': ('cuisine', {'火锅'})}
        option = _replacement(LunchRoutes(food), self.meal, self.train, '景点', 660, {'原餐厅'}, goals, 59)
        self.assertIsNone(option)
        option = _replacement(LunchRoutes(food), self.meal, self.train, '景点', 660, {'原餐厅'}, goals, 60)
        self.assertEqual(option[3], {'goal'})

    def test_a_dessert_or_snack_does_not_replace_lunch(self):
        food = [{'name': name, 'price': price, 'cuisine': cuisine, 'opentime': '11:00', 'endtime': '21:00'}
                for name, price, cuisine in [('冰淇淋店', 5, '面包甜点'), ('糖葫芦店', 6, '小吃'),
                                             ('面馆', 20, '快餐简餐')]]
        option = _replacement(LunchRoutes(food), self.meal, self.train, '景点', 660, {'原餐厅'}, {}, 0)
        self.assertEqual(option[1]['position'], '面馆')
