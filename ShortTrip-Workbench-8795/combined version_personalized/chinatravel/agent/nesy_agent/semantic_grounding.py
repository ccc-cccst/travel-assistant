"""Small, catalog-checked semantic aliases for the supported benchmark tags."""
import re

TAG_ALIASES = {
    'spot_type': {
        '博物馆': '博物馆/纪念馆', '纪念馆': '博物馆/纪念馆', '美术馆': '美术馆/艺术馆', '艺术馆': '美术馆/艺术馆', '自然风景': '自然风光', '自然美景': '自然风光', '历史博物馆': '博物馆/纪念馆', '繁华的街区': '商业街区', '购物': '商业街区', '红色资源': '红色景点', '文化氛围': '文化旅游区', '历史景点': '历史古迹', '历史人文景点': '历史古迹',
        '历史人文': '历史古迹', '历史': '历史古迹', '自然景点': '自然风光',
        '自然景观': '自然风光', '自然风光': '自然风光', '大学校园': '大学校园',
        '大学里': '大学校园', '大学': '大学校园', '古典园林': '园林',
        '游乐园': '游乐园/体育娱乐', '购物街': '商业街区',
        'museum': 'Museum/Memorial Hall', 'museums': 'Museum/Memorial Hall',
    },
    'food_type': {'喝酒': '酒吧/酒馆', '快餐': '快餐简餐', '上海菜': '本帮菜', '上海本地菜': '本帮菜', '简餐': '快餐简餐', '自助': '自助餐'},
    'hotel_feature': {'江景房': '江河景房', '江河景色': '江河景房', '看电影': '影音房', '健身': '健身室', '电子游戏': '电竞房', '家庭入住': '家庭房', '拍照好看': '拍照出片', '智能控制房间设备': '智能客控', '开会的地方': '会议厅', '小孩玩的乐园': '儿童乐园', '打牌': '棋牌室', '游泳': '泳池', '湖景': '湖景房', '情侣入住': '情侣房', '家庭大床房': '家庭房', '亲子房': '亲子主题房'},
}

# Explicit geography-to-cuisine correspondences, intersected with the installed catalog.
CITY_CUISINES = {
    '上海': ('本帮菜',), '北京': ('北京菜',), '成都': ('川菜',), '重庆': ('川菜',),
    '武汉': ('湖北菜',), '广州': ('粤菜',), '深圳': ('粤菜', '客家菜'),
    '杭州': ('江浙菜',), '苏州': ('江浙菜',), '南京': ('江浙菜',),
    'Shanghai': ('Shanghai cuisine',), 'Beijing': ('Beijing cuisine',),
    'Chengdu': ('Sichuan cuisine',), 'Chongqing': ('Sichuan cuisine',),
    'Wuhan': ('Hubei cuisine',), 'Guangzhou': ('Cantonese cuisine',),
    'Shenzhen': ('Cantonese cuisine', 'Hakka cuisine'),
    'Hangzhou': ('Jiangsu-Zhejiang cuisine',), 'Suzhou': ('Jiangsu-Zhejiang cuisine',),
    'Nanjing': ('Jiangsu-Zhejiang cuisine',),
}
LOCAL_CUISINE = re.compile(r'(?:当地|本地)(?:的)?(?:特色)?(?:菜(?:系|肴)?|美食)|特色美食|\blocal (?:cuisine|dishes|specialties)\b', re.I)
HOTEL_CONTEXT = re.compile(r'酒店|住宿|民宿|客房|房间|hotel|accommodation|room', re.I)


def normalized(value):
    return re.sub(r'\s+', '', value).casefold()


def local_cuisine_values(query, catalog, source):
    matches = list(LOCAL_CUISINE.finditer(source))
    if not matches:
        return set()
    values = set()
    for match in matches:
        # An explicitly named cuisine region takes priority over destination context.
        mentions = [(abs(position.start() - match.start()), city)
                    for city in CITY_CUISINES
                    for position in re.finditer(re.escape(city), source)]
        city = min(mentions)[1] if mentions else query['target_city']
        values.update(CITY_CUISINES.get(city, ()))
    return values & catalog['food_type']


def category_values(query, catalog, field, value, source=None):
    if field == 'food_type' and LOCAL_CUISINE.fullmatch(value):
        return local_cuisine_values(query, catalog, source or value)
    return {TAG_ALIASES.get(field, {}).get(value, value)}


def category_supported(query, catalog, field, value, source):
    if any(normalized(part) in normalized(source) for part in value.split('/') if len(part) >= 2):
        return True
    if field == 'food_type' and value in local_cuisine_values(query, catalog, source):
        return True
    for alias, canonical in TAG_ALIASES.get(field, {}).items():
        if canonical != value or normalized(alias) not in normalized(source):
            continue
        if field != 'hotel_feature' or HOTEL_CONTEXT.search(source):
            return True
    return False


def grounding_instructions(query, catalog):
    categories = {field: sorted(catalog[field]) for field in ('spot_type', 'food_type', 'hotel_feature')}
    import json
    return ('\nAuthoritative destination: ' + query['target_city'] + '\nDestination tag catalog:\n' + json.dumps(categories, ensure_ascii=False)
            + '\nSupported tag aliases:\n' + json.dumps(TAG_ALIASES, ensure_ascii=False)
            + '\nLocal-cuisine candidates for this destination (use OR when multiple):\n'
            + json.dumps(sorted(set(CITY_CUISINES.get(query['target_city'], ())) & catalog['food_type']), ensure_ascii=False)
            + '\nUse restaurant cuisine tags, not words in restaurant names, for food preferences. '
            'A relative local-cuisine request uses the destination context. Preserve vague crowding, '
            'comfort, frequency and minimization goals verbatim in unmapped_requirements when the '
            'existing symbolic fields cannot express them. Do not invent symbols or numerical bounds.\n')
