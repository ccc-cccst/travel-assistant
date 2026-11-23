"""生成杭州 3 日游示例行程（ChinaTravel 输出格式，地点名都取自数据库）。

只是给网页演示用的假数据，不是模型生成的。两版：v1 原始版，v2 是“第一天晚上换成运河夜游”后的新版，
用来演示左侧 AI 助手的“保留新版本 / 回到上一个版本”。
    python webui/make_sample.py && python webui/export_plan.py
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_plan import CityIndex  # noqa: E402

OUT = Path(__file__).resolve().parent / "samples"
idx = CityIndex("杭州")


def hm(t):
    h, m = map(int, t.split(":"))
    return h * 60 + m


def fmt(m):
    return f"{m // 60:02d}:{m % 60:02d}"


def dist(a, b):
    pa, pb = idx.find(a), idx.find(b)
    r = 6371.0
    p1, p2 = math.radians(pa["lat"]), math.radians(pb["lat"])
    dp, dl = p2 - p1, math.radians(pb["lon"] - pa["lon"])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h)) * 1.3  # 直线距离 ×1.3 近似道路距离


def transport(frm, to, depart, people):
    d = round(dist(frm, to), 2)
    if d < 0.9:
        mins = max(3, round(d / 5 * 60))
        return [{"start": frm, "end": to, "mode": "walk", "start_time": fmt(depart), "end_time": fmt(depart + mins),
                 "cost": 0, "distance": d, "price": 0}], depart + mins
    mins = max(8, round(d / 25 * 60) + 5)
    price = round(13 + max(0.0, d - 3) * 2.5, 1)
    cars = math.ceil(people / 4)
    return [{"start": frm, "end": to, "mode": "taxi", "start_time": fmt(depart), "end_time": fmt(depart + mins),
             "cost": round(price * cars, 1), "distance": d, "price": price, "cars": cars}], depart + mins


TYPE_OF = {"attraction": "attraction", "lunch": "lunch", "dinner": "dinner", "breakfast": "breakfast", "hotel": "accommodation"}


def build(days_spec, people=2):
    itinerary = []
    hotel = "杭州西湖湖滨平海路亚朵酒店"
    for di, spec in enumerate(days_spec, 1):
        acts, here, clock = [], None, None
        for item in spec:
            kind = item[0]
            if kind == "train_in":
                _, tid, st, et = item
                acts.append({"start_time": st, "end_time": et, "start": "上海虹桥站", "end": "杭州东站", "price": 73.0,
                             "cost": 73.0 * people, "tickets": people, "transports": [], "TrainID": tid, "type": "train"})
                here, clock = "杭州东站", hm(et)
                continue
            if kind == "train_out":
                _, tid, st, et = item
                tr, _ = transport(here, "杭州东站", clock, people)
                acts.append({"start_time": st, "end_time": et, "start": "杭州东站", "end": "上海虹桥站", "price": 73.0,
                             "cost": 73.0 * people, "tickets": people, "transports": tr, "TrainID": tid, "type": "train"})
                continue
            _, name, st, et = item
            if here is None:  # 当天从酒店出发
                here, clock = hotel, hm(st) - 30
            tr, arrive = transport(here, name, clock, people)
            start = max(hm(st), arrive)
            end = start + (hm(et) - hm(st)) if et != "24:00" else 24 * 60
            info = idx.find(name)["info"]
            if kind == "attraction":
                price = float(info.get("门票") or 0)
                a = {"position": name, "type": "attraction", "price": price, "cost": price * people, "tickets": people}
            elif kind == "hotel":
                price = float(info.get("价格") or 0)
                a = {"position": name, "type": "accommodation", "price": price, "cost": price, "room_type": 2, "rooms": 1}
            else:
                price = float(info.get("人均") or 0)
                a = {"position": name, "type": TYPE_OF[kind], "price": price, "cost": price * people}
            a.update({"start_time": fmt(start), "end_time": fmt(end) if end < 1440 else "24:00", "transports": tr})
            acts.append(a)
            here, clock = name, end
        itinerary.append({"day": di, "activities": acts})
    return {"people_number": people, "start_city": "上海", "target_city": "杭州", "itinerary": itinerary}


DAY2 = [
    ("attraction", "白堤", "08:30", "09:20"),
    ("attraction", "平湖秋月", "09:30", "10:15"),
    ("lunch", "楼外楼(西湖景区孤山路店)", "11:00", "12:00"),
    ("attraction", "苏堤", "12:30", "13:30"),
    ("attraction", "三潭印月", "13:45", "15:15"),
    ("attraction", "雷峰塔", "15:40", "16:50"),
    ("attraction", "清河坊街", "17:20", "18:20"),
    ("dinner", "知味观(湖滨店)", "18:40", "19:40"),
    ("attraction", "断桥残雪", "20:00", "20:50"),
    ("hotel", "杭州西湖湖滨平海路亚朵酒店", "21:10", "24:00"),
]
DAY3 = [
    ("attraction", "杭州运河游船", "09:00", "11:00"),
    ("attraction", "香积寺", "11:20", "12:10"),
    ("lunch", "外婆家(湖滨银泰in77C区店)", "12:40", "13:40"),
    ("train_out", "G7518", "15:30", "16:28"),
]
V1 = [
    [("train_in", "G7501", "08:00", "08:56"),
     ("attraction", "西溪国家湿地公园", "09:50", "12:00"),
     ("lunch", "外婆家(西溪天堂店)", "12:20", "13:20"),
     ("attraction", "京杭大运河杭州景区", "14:00", "16:30"),
     ("attraction", "武林夜市", "17:30", "18:40"),
     ("dinner", "新白鹿餐厅(湖滨银泰in77C区店)", "19:00", "20:00"),
     ("hotel", "杭州西湖湖滨平海路亚朵酒店", "20:20", "24:00")],
    DAY2, DAY3,
]
V2 = [
    [("train_in", "G7501", "08:00", "08:56"),
     ("attraction", "西溪国家湿地公园", "09:50", "12:00"),
     ("lunch", "外婆家(西溪天堂店)", "12:20", "13:20"),
     ("attraction", "京杭大运河杭州景区", "14:00", "16:30"),
     ("dinner", "新白鹿餐厅(湖滨银泰in77C区店)", "17:20", "18:20"),
     ("attraction", "杭州运河夜游", "19:00", "20:30"),
     ("hotel", "杭州西湖湖滨平海路亚朵酒店", "21:00", "24:00")],
    DAY2,
    [("attraction", "灵隐寺", "08:30", "10:00"),
     ("attraction", "灵隐飞来峰景区", "10:00", "11:00"),
     ("lunch", "知味观·味庄(杨公堤店)", "11:40", "12:40"),
     ("train_out", "G7518", "15:30", "16:28")],
]

if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    q = "当前位置上海。我和朋友计划去杭州玩三天，坐高铁往返，想看西湖和西溪湿地，晚上逛逛运河一带。请给我一个旅行规划。"
    q2 = "第一天晚上别逛夜市了，换成运河夜游船；第三天的运河游船和第一天重复了，换个别的。"
    for fname, plan, nl in [("hangzhou_3d_plan.json", build(V1), q), ("hangzhou_3d_plan_v2.json", build(V2), q2)]:
        (OUT / fname).write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
        (OUT / fname.replace("plan", "query")).write_text(
            json.dumps({"start_city": "上海", "target_city": "杭州", "days": 3, "people_number": 2,
                        "nature_language": nl}, ensure_ascii=False, indent=1), encoding="utf-8")
        print("[ok]", fname)
