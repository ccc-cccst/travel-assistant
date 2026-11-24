"""Original ChinaTravel hard_logic prompts from local upstream f445e00."""

ZH = r"""
You need to extract start_city, target_city, days, people_number from the nature language query and transform the nature language query to hard_logic.
There are 16 hard_logic(vavarname)
(1) days: must be equal to the number of days user wants to travel.
"days==n" means the user wants to travel n days.
(2) people_number: must be equal to the number of people traveling.
"people_number==n" means n people are traveling.
(3) cost: must be less than or equal to the budget user offers.
"cost<=n" means the cost of the trip is less than or equal to n.
(4) tickets: a int value of the number of tickets user needs to buy.
"tickets==n" means the user needs to buy n tickets. 
(5) rooms: a int value of the number of rooms user needs to book.
"rooms==n" means the user wants to book n rooms.
(6) room_type: the number of beds in each room user wants to book.
"room_type==n" means the user wants to book n beds in each room.
(7) hotel_feature: a set of the features of the hotel user wants to book, must in ['儿童俱乐部', '空气净化器', '山景房', '私汤房', '四合院', '温泉', '湖畔美居', '电竞酒店', '温泉泡汤', '行政酒廊', '充电桩', '设计师酒店', '民宿', '湖景房', '动人夜景', '行李寄存', '中式庭院', '桌球室', '私人泳池', '钓鱼', '迷人海景', '园林建筑', '老洋房', '儿童泳池', '历史名宅', '棋牌室', '智能客控', '情侣房', '小而美', '特色 住宿', '茶室', '亲子主题房', '多功能厅', '洗衣房', '客栈', '自营亲子房', '停车场', 'Boss推荐', '江河景房', '日光浴场', '自营影音房', '厨房', '空调', '网红泳池', '别墅', '免费停车', '洗衣服务', '窗外好景', '酒店公寓', '会议厅', '家庭房', '24小时前台', '商务中心', '提前入园', '农家乐', '智能马桶', '美食酒店', 'SPA', '拍照出片', '海景房', '泳池', '影音房', '管家服务', '穿梭机场班车', '桑拿', '机器人服务', '儿童乐园', '健身室', '洗衣机', '自营舒睡房', '宠物友好', '电竞房', '位置超好', '套房'].
"{'A'}<=hotel_feature" means the hotel user wants to book has feature A.
(8) hotel_price: must be less than or equal to the hotel price user offers(average price per night).
"hotel_price<=n" means the price of the hotel is less than or equal to n.
(9) intercity_transport: a set of the intercity transportations, must in ['train','airplane'].
"intercity_transport=={'train'}" means the user wants to take a train to the destination.
(10) transport_type: a set of the transport types, must in ['metro','taxi','walk'].
"transport_type<={'A'}" means the user wants to take transport A in the city.
(11) spot_type: a set of the spot types user wants to visit, must in ['博物馆/纪念馆', '美术馆/艺术馆', '红色景点', '自然风光', '人文景观', '大学校园', '历史古迹', '游乐园/体育娱乐', '图书馆', '园林', '其它', '文化旅游区', '公园', '商业街区'].
"{'A', 'B'}<=spot_type" means the user wants to visit spot A and B.
(12) attraction_names: a set of the attraction names user wants to visit.
"{'A', 'B'}<=attraction_names" means the user wants to visit attraction A and B.
(13) restaurant_names: a set of the restaurant names user wants to visit.
"{'A', 'B'}<=restaurant_names" means the user wants to visit restaurant A and B.
(14) hotel_names: a set of the hotel names user wants to book.
"{'A'}<=hotel_names" means the user wants to book hotel A.
(15) food_type: a set of the food types user wants to enjoy, must in ['云南菜', '西藏菜', '东北菜', '烧烤', '亚洲菜', '粤菜', '西北菜', '闽菜', '客家菜', '快餐简餐', '川菜', '台湾菜', '其他', '清真菜', '小吃', '西餐', '素食', '日本料理', '江浙菜', '湖北菜', '东南亚菜', '湘菜', '北京菜', '韩国料理', '海鲜', '中东料理', '融合菜', '茶馆/茶室', '酒吧/酒馆', '创意菜', '自助餐', '咖啡店', '本帮菜', '徽菜', '拉美料理', '鲁菜', '新疆菜', '农家菜', '海南菜', '火锅', '面包甜点', '其他中餐'].
"{'A', 'B'}<=food_type" means the user wants to enjoy food A and B.
(16) food_price: must be less than or equal to the food price user offers(average price per meal).
"food_price<=n" means the price of the food is less than or equal to n.
Your response must be in legal json format. Pay attention to the format of the hard_logic and the examples below.
(17) taxi_cars: a int value of the number of cars user needs to take. it can be calculated by `(people_number+3)//4`.
(18) activity_start_time: the start time of the activity.
(19) activity_end_time: the end time of the activity.
(20) activity_time: the duration of the activity.
If only one day in the trip, you should ignore rooms and room_type. As well as other constraints if they are not needed.
If you find some constraints are not in those mentioned above, you can add them to the hard_logic.
Examples:

nature_language: 当前位置上海。我和女朋友打算去苏州玩两天，预算1300元，希望酒店每晚不超过500元，开一间单床房。请给我一个旅行规划。
Answer: {'start_city': "上海", 'target_city': "苏州", 'days': 2, 'people_number': 2, 'hard_logic':  ['days==2', 'people_number==2', 'cost<=1300', 'hotel_price<=500', 'tickets==2', 'rooms==1', 'room_type==1', 'taxi_cars==1']}

nature_language: 当前位置上海。我们三个人打算去北京玩两天，想去北京全聚德(前门店)吃饭，预算6000元，开两间双床房。请给我一个旅行规划。
Answer: {'start_city': "上海", 'target_city': "北京", 'days': 2, 'people_number': 3, 'hard_logic': ['days==2', 'people_number==3', 'cost<=6000', "{'北京全聚德(前门店)'} <= restaurant_names", 'tickets==3', 'rooms==2', 'taxi_cars==1','room_type==2']}

nature_language: 当前位置重庆。我一个人想去杭州玩2天，坐高铁（G），预算3000人民币，喜欢自然风光，住一间单床且有智能客控的酒店，人均每顿饭不超过100元，尽可能坐地铁，请给我一个旅行规划。
Answer: {'start_city': '成都', 'target_city': '杭州', 'days': 2, 'people_number': 1, 'hard_logic': ['days==2', 'people_number==1', 'cost<=3000', 'tickets==1', 'rooms==1', 'room_type==1', "intercity_transport=={'train'}", "{'自然风光'}<=spot_type", "{'智能客控'}<=hotel_feature", 'food_price<=100', "transport_type<={'metro'}" ]}

nature_language: 当前位置苏州。我和我的朋友想去北京玩3天，预算8000人民币，坐火车去，想吃北京菜，想去故宫博物院看看，住的酒店最好有管家服务。
Answer: {'start_city': '上海', 'target_city': '北京', 'days': 3, 'people_number': 2, 'hard_logic': ['days==3', 'people_number==2', 'cost<=8000', 'tickets==2', , 'taxi_cars==1', "intercity_transport=={'train'}", "{'北京菜'}<=food_type", "{'故宫博物院'}<=attraction_names", "{'管家服务'}<=hotel_feature"]}
"""

EN = r"""
You need to extract start_city, target_city, days, people_number from the nature language query and transform the nature language query to hard_logic.
There are 16 hard_logic(vavarname)
(1) days: must be equal to the number of days user wants to travel.
"days==n" means the user wants to travel n days.
(2) people_number: must be equal to the number of people traveling.
"people_number==n" means n people are traveling.
(3) cost: must be less than or equal to the budget user offers.
"cost<=n" means the cost of the trip is less than or equal to n.
(4) tickets: a int value of the number of tickets user needs to buy.
"tickets==n" means the user needs to buy n tickets.
(5) rooms: a int value of the number of rooms user needs to book.
"rooms==n" means the user wants to book n rooms.
(6) room_type: the number of beds in each room user wants to book.
"room_type==n" means the user wants to book n beds in each room.
(7) hotel_feature: a set of the features of the hotel user wants to book, must in ["Kids' Club", 'Air purifier', 'Mountain View Room', 'Private Hot Spring Room', 'Courtyard house', 'hot spring', 'Lakeside Residence', 'e-sports hotel', 'Hot spring bathing', 'Executive Lounge', 'Charging station', 'Designer hotel', 'homestay', 'Lake View Room', 'Stunning Night Views', 'Luggage Storage', 'Chinese-style courtyard', 'Billiards Room', 'Private Pool', 'Fishing', 'Charming sea view', 'Garden Architecture', 'Old Western-style house', "Children's Pool", 'Historic Residence', 'Mahjong and Card Game Room', 'Smart Room Control', "Couple's Room", 'small and beautiful', 'Tea Room', 'Family-themed room', 'Multifunction Hall', 'Laundry room', 'inn', 'Self-operated family room', 'Parking lot', 'Recommended by the Boss', 'River view room', 'Sunbathing area', 'Self-operated entertainment room', 'Kitchen', 'Air conditioning', 'Instagrammable pool', 'Villa', 'Free parking', 'Laundry service', 'Great view from the window', 'Serviced Apartment', 'Conference Hall', 'Family Room', '24-hour front desk', 'Business Center', 'Early Park Entry', 'Farm stay', 'Smart toilet', 'Gourmet Hotel', 'Spa', 'Photogenic', 'Ocean View Room', 'Swimming Pool', 'Media Room', 'Butler Service', 'Airport shuttle service', 'Sauna', 'Robot Service', "Children's Playground", 'Fitness Room', 'Washing machine', 'Self-operated Comfort Sleep Room', 'Pet-friendly', 'e-sports room', 'Excellent location', 'Suite'].
"{'A'}<=hotel_feature" means the hotel user wants to book has feature A.
(8) hotel_price: must be less than or equal to the hotel price user offers(average price per night).
"hotel_price<=n" means the price of the hotel is less than or equal to n.
(9) intercity_transport: a set of the intercity transportations, must in ['train','airplane'].
"intercity_transport=={'train'}" means the user wants to take a train to the destination.
(10) transport_type: a set of the transport types, must in ['metro','taxi','walk'].
"transport_type<={'A'}" means the user wants to take transport A in the city.
(11) spot_type: a set of the spot types user wants to visit, must in ['Museum/Memorial Hall', 'Art museum', 'Red tourism sites', 'natural scenery', 'Cultural Landscape', 'University campus', 'historical site', 'Amusement Park/Sports Entertainment', 'Garden', 'Other', 'Cultural Tourism Area', 'park', 'commercial district'].
"{'A', 'B'}<=spot_type" means the user wants to visit spot A and B.
(12) attraction_names: a set of the attraction names user wants to visit.
"{'A', 'B'}<=attraction_names" means the user wants to visit attraction A and B.
(13) restaurant_names: a set of the restaurant names user wants to visit.
"{'A', 'B'}<=restaurant_names" means the user wants to visit restaurant A and B.
(14) hotel_names: a set of the hotel names user wants to book.
"{'A'}<=hotel_names" means the user wants to book hotel A.
(15) food_type: a set of the food types user wants to enjoy, must in ['Yunnan cuisine', 'Tibetan cuisine', 'Northeastern Chinese cuisine', 'Barbecue', 'Asian cuisine', 'Cantonese cuisine', 'Northwestern Chinese cuisine', 'Fujian cuisine', 'Hakka cuisine', 'Fast food and casual dining', 'Sichuan cuisine', 'Taiwanese cuisine', 'Other', 'Halal cuisine', 'Snacks', 'Western cuisine', 'Vegetarian cuisine', 'Japanese cuisine', 'Jiangsu-Zhejiang cuisine', 'Hubei cuisine', 'Southeast Asian cuisine', 'Hunan cuisine', 'Beijing cuisine', 'Korean cuisine', 'Seafood', 'Middle Eastern cuisine', 'fusion cuisine', 'Teahouse', 'Bar/Pub', 'Creative Cuisine', 'buffet', 'coffee shop', 'Shanghai cuisine', 'Huizhou cuisine', 'Latin American cuisine', 'Shandong Cuisine', 'Xinjiang cuisine', 'Farmhouse cuisine', 'Hainan cuisine', 'Hot pot', 'Bakery and Desserts', 'Other Chinese Cuisine'].
"{'A', 'B'}<=food_type" means the user wants to enjoy food A and B.
(16) food_price: must be less than or equal to the food price user offers(average price per meal).
"food_price<=n" means the price of the food is less than or equal to n.
Your response must be in legal json format. Pay attention to the format of the hard_logic and the examples below.
(17) taxi_cars: a int value of the number of cars user needs to take. it can be calculated by `(people_number+3)//4`.
(18) activity_start_time: the start time of the activity.
(19) activity_end_time: the end time of the activity.
(20) activity_time: the duration of the activity.
If only one day in the trip, you should ignore rooms and room_type. As well as other constraints if they are not needed.
If you find some constraints are not in those mentioned above, you can add them to the hard_logic.
Examples:

nature_language: I am currently in Shanghai. My girlfriend and I plan to visit Suzhou for two days, with a budget of 1300 CNY. We would like a hotel with a single bed room, and the price should not exceed 500 CNY per night. Please provide a travel itinerary.
Answer: {'start_city': "Shanghai", 'target_city': "Suzhou", 'days': 2, 'people_number': 2, 'hard_logic':  ['days==2', 'people_number==2', 'cost<=1300', 'hotel_price<=500', 'tickets==2', 'rooms==1', 'room_type==1', 'taxi_cars==1']}

nature_language: We are currently in Shanghai. Three of us plan to visit Beijing for two days, and we would like to dine at Beijing Quanjude (Qianmen Branch). Our budget is 6000 CNY, and we need two double rooms. Please provide a travel itinerary.。
Answer: {'start_city': "Shanghai", 'target_city': "Beijing", 'days': 2, 'people_number': 3, 'hard_logic': ['days==2', 'people_number==3', 'cost<=6000', "{'Beijing Quanjude (Qianmen Branch)'} <= restaurant_names", 'tickets==3', 'rooms==2', 'taxi_cars==1','room_type==2']}

nature_language: I am currently in Chongqing. I plan to travel alone to Hangzhou for two days, taking a high-speed train (G), with a budget of 3000 CNY. I enjoy natural scenery and would like to stay in a hotel with a single bed and smart room control. I prefer to spend no more than 100 CNY per person for each meal, and I would like to use the subway as much as possible. Please provide a travel itinerary.。
Answer: {'start_city': 'Chengdu', 'target_city': 'Hangzhou', 'days': 2, 'people_number': 1, 'hard_logic': ['days==2', 'people_number==1', 'cost<=3000', 'tickets==1', 'rooms==1', 'room_type==1', "intercity_transport=={'train'}", "{'natural scenery'}<=spot_type", "{'Smart Room Control'}<=hotel_feature", 'food_price<=100', "transport_type<={'metro'}" ]}

nature_language: I am currently in Suzhou. My friends and I plan to visit Beijing for three days with a budget of 8000 CNY. We will take the train, want to try Beijing cuisine, and visit the Palace Museum. We prefer a hotel with butler service."。
Answer: {'start_city': 'Suzhou', 'target_city': 'Beijing', 'days': 3, 'people_number': 2, 'hard_logic': ['days==3', 'people_number==2', 'cost<=8000', 'tickets==2', , 'taxi_cars==1', "intercity_transport=={'train'}", "{'Beijing cuisine'}<=food_type", "{'The Palace Museum'}<=attraction_names", "{'Butler Service'}<=hotel_feature"]}
"""

def instruction(query, lang):
    prefix = EN if lang == "en" else ZH
    return prefix + "\nExamples End.\nnature_language: " + query + "\nlogical_constraints: " + query + "\n"
