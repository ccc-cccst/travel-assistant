"""把 ShortTrip / ChinaTravel 生成的行程 JSON 加上经纬度，导出给网页用。

用法（在仓库根目录）:
    python webui/export_plan.py                       # 导出 webui/samples/ 下的示例
    python webui/export_plan.py --plan a.json --query q.json --title "杭州3日游"
    python webui/export_plan.py --plan a.json --plan b.json   # 多个版本，网页里可切换

输出 webui/data/trips.js（window.TRIPS = [...]），index.html 直接双击即可打开，不需要起服务。
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "chinatravel" / "environment" / "database"
# 查坐标用的数据库目录，按顺序找到第一个有该城市数据的；后端会把 chinatravel-five-city 的数据库加进来
DB_ROOTS: list[Path] = [DB]
WEB = Path(__file__).resolve().parent

CITY = {
    "北京": "beijing", "上海": "shanghai", "南京": "nanjing", "苏州": "suzhou", "杭州": "hangzhou",
    "深圳": "shenzhen", "成都": "chengdu", "重庆": "chongqing", "广州": "guangzhou", "武汉": "wuhan",
    "西安": "xian", "厦门": "xiamen", "宁波": "ningbo", "长沙": "changsha", "青岛": "qingdao",
}


def _db_for(slug: str) -> Path:
    for root in DB_ROOTS:
        if (root / "attractions" / slug).exists():
            return root
    return DB


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


class CityIndex:
    """某个城市所有带坐标的地点：景点 / 餐厅 / 酒店 / poi（车站等）/ 地铁站。"""

    def __init__(self, city_cn: str, database: Path | None = None):
        self.city_cn = city_cn
        slug = CITY.get(city_cn, city_cn)
        self.places: dict[str, dict] = {}
        DB = database if database is not None else _db_for(slug)
        for row in _read_csv(DB / "attractions" / slug / "attractions.csv"):
            self._add(row["name"], row, "attraction", {
                "类型": row.get("type"), "开放": f"{row.get('opentime')}-{row.get('endtime')}",
                "门票": row.get("price"), "建议游玩(小时)": f"{row.get('recommendmintime')}-{row.get('recommendmaxtime')}"})
        rest = DB / "restaurants" / slug
        for p in sorted(rest.glob("*.csv")) if rest.exists() else []:
            for row in _read_csv(p):
                self._add(row["name"], row, "restaurant", {
                    "菜系": row.get("cuisine"), "人均": row.get("price"),
                    "营业": f"{row.get('opentime')}-{row.get('endtime')}", "推荐菜": row.get("recommendedfood")})
        for row in _read_csv(DB / "accommodations" / slug / "accommodations.csv"):
            self._add(row["name"], row, "hotel", {"房型": row.get("featurehoteltype"), "价格": row.get("price")})
        poi = DB / "poi" / slug / "poi.json"
        if poi.exists():
            for item in json.loads(poi.read_text(encoding="utf-8")):
                lat, lon = item["position"]
                self.places.setdefault(item["name"], {"lat": lat, "lon": lon, "kind": "poi", "info": {}})
        subways = DB / "transportation" / "subways.json"
        if subways.exists():
            lines = json.loads(subways.read_text(encoding="utf-8")).get(slug, [])
            for line in lines:
                for st in line["stations"]:
                    lon, lat = map(float, st["position"].split(","))
                    self.places.setdefault(st["name"] + "-地铁站", {"lat": lat, "lon": lon, "kind": "metro", "info": {"线路": line["name"]}})

    def _add(self, name, row, kind, info):
        try:
            lat, lon = float(row["lat"]), float(row["lon"])
        except (KeyError, TypeError, ValueError):
            return
        self.places.setdefault(name, {"lat": lat, "lon": lon, "kind": kind,
                                      "info": {k: v for k, v in info.items() if v not in (None, "", "nan")}})

    def find(self, name: str | None) -> dict | None:
        if not name:
            return None
        return self.places.get(name)


ACT_KIND = {"attraction": "attraction", "breakfast": "restaurant", "lunch": "restaurant", "dinner": "restaurant",
            "accommodation": "hotel", "train": "station", "airplane": "station"}


def _haversine(a, b):
    r = 6371.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def enrich(plan: dict, *, database: Path | None = None) -> dict:
    target = plan.get("target_city", "")
    idx = CityIndex(target, database)
    missing: set[str] = set()

    def locate(name):
        hit = idx.find(name)
        if hit is None and name:
            missing.add(name)
        return hit

    days = []
    total = 0.0
    for day in plan.get("itinerary", []):
        acts = []
        for a in day.get("activities", []):
            t = a.get("type")
            if t in ("train", "airplane"):
                # 只把目的城市这一头的车站画在地图上
                here = a.get("end") if idx.find(a.get("end")) else a.get("start")
                name = here
                title = f"{a.get('start')} → {a.get('end')}"
            else:
                name = a.get("position")
                title = name
            hit = locate(name) if t not in ("train", "airplane") or idx.find(name) else None
            segs = []
            for s in a.get("transports", []) or []:
                ps, pe = idx.find(s.get("start")), idx.find(s.get("end"))
                segs.append({**s, "_from": [ps["lat"], ps["lon"]] if ps else None,
                             "_to": [pe["lat"], pe["lon"]] if pe else None})
            cost = float(a.get("cost") or 0) + sum(float(s.get("cost") or 0) for s in a.get("transports", []) or [])
            total += cost
            acts.append({
                **a, "transports": segs, "_title": title, "_place": name,
                "_kind": ACT_KIND.get(t, "poi"),
                "_pos": [hit["lat"], hit["lon"]] if hit else None,
                "_info": hit["info"] if hit else {},
                "_cost": round(cost, 2),
            })
        days.append({"day": day.get("day"), "activities": acts})
    out = {k: v for k, v in plan.items() if k != "itinerary"}
    out["itinerary"] = days
    out["_total_cost"] = round(total, 2)
    out["_missing"] = sorted(missing)
    return out


def summarize(plan: dict) -> str:
    """给左侧 AI 助手的那段回复：按天列出主要景点、总花费。"""
    lines = [f"已为你生成 {plan.get('start_city','')}→{plan.get('target_city','')} 的 {len(plan['itinerary'])} 天行程，"
             f"{plan.get('people_number', 1)} 人，预计总花费约 ¥{plan['_total_cost']:.0f}。"]
    for d in plan["itinerary"]:
        spots = [a["_title"] for a in d["activities"] if a["type"] == "attraction"]
        food = [a["_title"] for a in d["activities"] if a["type"] in ("breakfast", "lunch", "dinner")]
        s = f"第{d['day']}天：" + ("、".join(spots) if spots else "以交通为主")
        if food:
            s += f"；用餐 {'、'.join(food)}"
        lines.append(s)
    return "\n".join(lines)


def load_trip(plan_path: Path, query_path: Path | None, title: str | None, version: str | None) -> dict:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    query = json.loads(query_path.read_text(encoding="utf-8")) if query_path and query_path.exists() else {}
    plan = enrich(plan)
    days = len(plan["itinerary"])
    return {
        "id": plan_path.stem,
        "title": title or f"{plan.get('target_city','')}{days}日游",
        "version": version or plan_path.stem,
        "query": query.get("nature_language") or query.get("nature_language_en") or "",
        "summary": summarize(plan),
        "plan": plan,
    }


def city_pois(city_cn: str, per_kind: int | None = 150, *, database: Path | None = None) -> dict:
    """地图筛选按钮（景点/酒店/美食/购物/机场/火车站）用的候选地点，按城市导出。
    评分/热度/图片数据库里没有，留空，由后端以后补（见 docs/webui-api.md 的 Poi）。"""
    idx = CityIndex(city_cn, database)
    out: dict[str, list] = {k: [] for k in ("attraction", "hotel", "restaurant", "shopping", "airport", "station")}
    for name, p in idx.places.items():
        kind = p["kind"]
        info = p["info"]
        if kind == "attraction" and info.get("类型") == "商业街区":
            kind = "shopping"
        elif kind == "poi":
            kind = "airport" if "机场" in name else ("station" if name.endswith("站") else None)
        if kind not in out:
            continue
        out[kind].append({"id": f"{kind}:{name}", "name": name, "kind": kind, "pos": [p["lat"], p["lon"]],
                          "info": info, "rating": None, "heat": None, "image": None})
    for k in out:
        out[k] = out[k][:per_kind]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", action="append", type=Path, help="行程 JSON，可写多次")
    ap.add_argument("--query", action="append", type=Path, help="对应的 query JSON（可选，顺序与 --plan 对应）")
    ap.add_argument("--title", action="append", help="标题（可选，顺序与 --plan 对应）")
    ap.add_argument("--out", type=Path, default=WEB / "data" / "trips.js")
    args = ap.parse_args()

    if not args.plan:
        sample = WEB / "samples"
        args.plan = [sample / "hangzhou_3d_plan.json", sample / "hangzhou_3d_plan_v2.json",
                     ROOT / "chinatravel/agent/nesy_agent/plan_for_check/day2.json"]
        args.query = [sample / "hangzhou_3d_query.json", sample / "hangzhou_3d_query_v2.json", None]
        args.title = ["杭州3日游：西溪湿地+西湖全景+运河夜游", "杭州3日游：西溪湿地+西湖全景+运河夜游", "广州2日游（plan_for_check 示例）"]

    trips = []
    for i, p in enumerate(args.plan):
        q = args.query[i] if args.query and i < len(args.query) else None
        t = args.title[i] if args.title and i < len(args.title) else None
        trip = load_trip(p, q, t, None)
        trips.append(trip)
        miss = trip["plan"]["_missing"]
        print(f"[ok] {p.name}: {len(trip['plan']['itinerary'])} 天, 总花费 ¥{trip['plan']['_total_cost']:.0f}"
              + (f"，找不到坐标: {miss}" if miss else ""))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    pois = {c: city_pois(c) for c in sorted({t["plan"].get("target_city", "") for t in trips} - {""})}
    args.out.write_text("window.TRIPS = " + json.dumps(trips, ensure_ascii=False, indent=1) + ";\n"
                        + "window.POIS = " + json.dumps(pois, ensure_ascii=False) + ";\n", encoding="utf-8")
    print(f"[ok] 写入 {args.out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
