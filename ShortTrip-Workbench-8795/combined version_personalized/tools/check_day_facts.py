"""Query 12 frozen archive locations and three city forecasts with the configured AMap service."""
import argparse
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from check_dated_transit import PLANNER, ROOT, RouteService, find_key, load_archive
from paired_transit import write
from day_facts import DayFacts

FIXTURES = {
    "0241a805a917423ca6302303cdabc5fc": ("外滩","上海博物馆","沪西老弄堂面馆(广东路店)","简厨·湖南菜(凌空SOHO店)"),
    "2a67a92984d3488b8bae2d15a4512659": ("西湖风景名胜区","雷峰塔","知竹(天竺路店)","鸡啤咯哒(下沙和达城店)"),
    "478845b168844b27b4d100f9060ddc97": ("成都大熊猫繁育研究基地","摩诃池","邱二哥锅魁(金丝街店)","酥小蒙牛肉饼店(天府广场店)"),
}


def frozen_locations():
    rows = []
    for job, names in FIXTURES.items():
        group = load_archive(PLANNER/"result/manual"/job, ROOT/"ShortTrip/artifacts/data")
        version = next(v for v in group["versions"] if v["id"] == group["recommendedId"])
        plan = version["plan"]
        items = {a.get("position"):a for day in plan["itinerary"] for a in day["activities"]}
        for name in names:
            item = items[name]
            rows.append({"city":plan["target_city"],"kind":item["_kind"],"name":name,"pos":item["_pos"],
                         "job":job,"candidate":version["id"],"snapshotHours":item.get("_info",{}).get("营业")
                         or item.get("_info",{}).get("开放")})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["freeze","query"])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--samples", type=Path)
    parser.add_argument("--date", type=date.fromisoformat)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("choose a new output file")
    if args.stage == "freeze":
        write(args.out, frozen_locations())
        return
    if not args.samples or not args.date:
        parser.error("query requires --samples and --date")
    from paired_transit import read
    samples = read(args.samples)
    key = find_key()
    if not key:
        parser.error("configure AMAP_KEY in the environment")
    with TemporaryDirectory() as temp:
        service = RouteService(key,Path(temp),min_interval=1.35)
        facts = DayFacts(service)
        observations = []
        for row in samples:
            started = perf_counter()
            result = facts.place(row["city"],row["kind"],row["name"],row["pos"],args.date)
            observations.append({"name":row["name"],"city":row["city"],"kind":row["kind"],
                                 "status":result["status"],"reason":result.get("reason"),
                                 "coordinateGapMeters":result.get("coordinateGapMeters"),
                                 "hasToday":bool(result.get("today")),"hasWeekly":bool(result.get("week")),
                                 "lastEntryFromOperator":(result.get("official") or {}).get("lastEntry"),
                                 "seconds":round(perf_counter()-started,3)})
        weather = []
        for city in sorted({row["city"] for row in samples}):
            result = facts.weather(city,args.date,3)
            weather.append({"city":city,"status":result["status"],"reason":result.get("reason"),
                            "dates":[{"date":d["date"],"hasDay":bool(d["day"]),"hasNight":bool(d["night"]),
                                      "dayRain":d["dayRain"],"nightRain":d["nightRain"]} for d in result["days"]]})
        write(args.out, {"calls":service.calls,"observations":observations,"weather":weather})


if __name__ == "__main__":
    main()
