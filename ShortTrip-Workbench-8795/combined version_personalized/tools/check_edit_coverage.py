"""Replay synthetic multi-edit utterances against the intent validator."""

import argparse
import json
from pathlib import Path
import sys
from time import perf_counter
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chinatravel.personalization.edit_intent import interpret_edit
from chinatravel.personalization.tripspec import ModelReply


def activity(identifier, kind, name, start, end, day=2, locked=False):
    return dict(id=identifier, day=day, kind=kind, name=name, start=start,
                end=end, locked=locked, has_connection=True, arrival_note="")


ACTIVITIES = [
    activity("lunch", "lunch", "午餐甲", "12:00", "13:00"),
    activity("park", "attraction", "滨江公园", "13:30", "14:15"),
    activity("museum", "attraction", "城市博物馆", "15:00", "16:00", locked=True),
    activity("dinner", "dinner", "晚餐甲", "18:00", "19:00"),
    activity("rail", "train", "返程 G2", "20:30", "21:30", day=3, locked=True),
    activity("other-lunch", "lunch", "午餐乙", "12:00", "13:00", day=3),
]


class Facts:
    version = {"commit": "synthetic"}

    def place_with_suggestions(self, city, name, kind):
        names = {"测试餐馆": ["测试餐馆"], "测试分店": ["测试分店甲", "测试分店乙"],
                 "测试分店甲": ["测试分店甲"]}.get(name, [])
        return {"status": "found" if len(names) == 1 else "ambiguous",
                "records": [{"name": item, "planning_catalog": True, "routable_in_snapshot": True}
                            for item in names]}


def change(action, item, **fields):
    return dict(action=action, activity=item, **fields)


def batch(*operations, pace=None):
    return dict(action="batch", day=2, operations=list(operations), pace=pace)


def case(group, text, draft, state, *, actions=(), period=None, error=None, snapshot=None):
    return dict(group=group, text=text, draft=draft, state=state, actions=actions,
                period=period, error=error, snapshot=snapshot)


LUNCH = change("time", "lunch", start="12:10", end="13:10")
PARK = change("time", "park", start="13:30", end="14:00")
DINNER = change("meal_options", "dinner")
METRO = change("transport", "museum", mode="metro")
REMOVE = change("remove_visit", "park")

CASES = [
    case("明确动作", "第二天午餐改到12:10–13:10，晚饭换一家。", batch(LUNCH, DINNER),
         "proposal", actions=("time:lunch", "meal_options:dinner")),
    case("明确动作", "第二天午餐改到12:10–13:10，晚饭换一家。", batch(LUNCH), "clarify"),
    case("明确动作", "第二天午餐改到12:10–13:10，晚饭换一家。", LUNCH, "clarify"),
    case("明确动作", "第二天午餐改到12:10–13:10，晚饭换一家。",
         batch(change("time", "lunch", start="12:30", end="13:30"), DINNER), "clarify"),
    case("节奏", "第二天下午轻松一点，保留预约，晚饭换一家。", batch(DINNER, pace="lighter"),
         "proposal", actions=("meal_options:dinner",), period="afternoon"),
    case("节奏", "第二天晚饭换一家，晚上轻松一点。", DINNER,
         "proposal", actions=("meal_options:dinner",), period="evening"),
    case("节奏", "第二天早上别太赶，晚餐换一家。", batch(DINNER),
         "proposal", actions=("meal_options:dinner",), period="morning"),
    case("节奏", "第二天下午别太赶，午餐改到12:10至13:10，滨江公园13:30到14:00，晚餐换一家。",
         batch(LUNCH, PARK, DINNER), "proposal",
         actions=("time:lunch", "time:park", "meal_options:dinner"), period="afternoon"),
    case("地点与分店", "第二天午餐改到12:10到13:10，晚餐换测试分店。",
         batch(LUNCH, change("replace", "dinner", place="测试分店")), "clarify"),
    case("地点与分店", "第二天午餐改到12:10到13:10，晚餐换测试分店甲。",
         batch(LUNCH, change("replace", "dinner", place="测试分店甲")),
         "proposal", actions=("time:lunch", "replace:dinner")),
    case("地点与分店", "第二天午餐改到12:10到13:10，晚饭换一家。",
         batch(LUNCH, change("replace", "dinner", place="测试餐馆")), "clarify"),
    case("地点与分店", "第二天午餐改到12:10到13:10，晚饭换一家凌晨食堂。",
         batch(LUNCH, change("replace", "dinner", place="凌晨食堂")), "clarify"),
    case("预约与保护", "第二天午餐改到12:10到13:10，城市博物馆改到16点。",
         batch(LUNCH, change("time", "museum", start="16:00", end="17:00")), "clarify"),
    case("预约与保护", "第二天午餐改到12:10到13:10，去博物馆坐地铁。",
         batch(LUNCH, METRO), "proposal", actions=("time:lunch", "transport:museum")),
    case("预约与保护", "第二天午餐改到12:10到13:10，不要删掉滨江公园。", batch(LUNCH, REMOVE), "clarify"),
    case("预约与保护", "第二天午餐改到12:10到13:10，删掉滨江公园。", batch(LUNCH, REMOVE),
         "proposal", actions=("time:lunch", "remove_visit:park")),
    case("上下文", "第二天午餐改到12:10到13:10，第三天午餐换一家。", batch(LUNCH, DINNER), "clarify"),
    case("上下文", "第二天午餐改到12:10到13:10，晚饭换一家。",
         batch(LUNCH, change("meal_options", "other-lunch")), "clarify"),
    case("上下文", "第二天午餐改到12:10到13:10，晚饭换一家。", batch(LUNCH, LUNCH, DINNER), "clarify"),
    case("上下文", "第二天午饭换测试餐馆，晚餐保留。",
         batch(change("replace", "lunch", place="测试餐馆"), DINNER), "clarify"),
    case("异常返回", "第二天午餐改到12:10到13:10，晚饭换一家。", None, "unavailable", error="request_timeout"),
    case("异常返回", "第二天午餐改到12:10到13:10，晚饭换一家。", ["伪造方案"], "unavailable"),
    case("异常返回", "第二天午餐改到12:10到13:10，晚饭换一家。",
         batch(LUNCH, DINNER), "clarify", snapshot={"commit": "old"}),
    case("异常返回", "第二天午餐改到12:10到13:10，晚饭换一家。",
         batch(LUNCH, {"action": "unsupported"}), "clarify"),
]


def run():
    rows = []
    for index, fixture in enumerate(CASES, 1):
        def reply(*args, **kwargs):
            return ModelReply(draft=fixture["draft"], error=fixture["error"])

        result = interpret_edit({"text": fixture["text"], "day": 2, "city": "合成样例",
                                 "snapshot": fixture["snapshot"] or Facts.version,
                                 "activities": ACTIVITIES}, Facts(), provider=reply)
        proposal = result.get("proposal", {})
        actions = tuple(op["action"] + ":" + op["activity"] for op in proposal.get("operations", []))
        period = proposal.get("pace_period")
        passed = (result["state"], actions, period) == (fixture["state"], fixture["actions"], fixture["period"])
        rows.append(dict(id=index, cohort="development" if (index - 1) % 4 < 2 else "held_cases",
                         group=fixture["group"], text=fixture["text"], expected=fixture["state"],
                         actual=result["state"], expected_actions=fixture["actions"], actual_actions=actions,
                         expected_period=fixture["period"], actual_period=period, passed=passed,
                         message=result.get("message")))
    return dict(total=len(rows), passed=sum(row["passed"] for row in rows), rows=rows)


def live_synthetic():
    """Ask the configured model about authored synthetic activities only."""
    installation = Path(__file__).resolve().parents[2] / "ShortTrip/artifacts/data/6de12f4/install.json"
    info = json.loads(installation.read_text(encoding="utf-8-sig"))
    source = info.get("source", info)
    snapshot = dict(commit=source["commit"], profile=source["profile"],
                    source="installed_snapshot", live_lookup=False)
    rows = []
    for index in (1, 5, 6, 7, 13, 14, 15, 16):
        fixture = CASES[index - 1]
        payload = dict(text=fixture["text"], activities=ACTIVITIES, day=2, city="上海",
                       snapshot=snapshot, model_provider="deepseek",
                       metrics={"total_cost_cny": 1500, "city_transit_minutes": 120})
        start = perf_counter()
        request = Request("http://127.0.0.1:8781/itinerary-edit/intent",
                          data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        try:
            with build_opener(ProxyHandler({})).open(request, timeout=25) as response:
                result = json.load(response)
                code = response.status
        except HTTPError as error:
            code = error.code
            result = json.load(error)
        proposal = result.get("proposal", {})
        actions = tuple(op["action"] + ":" + op["activity"] for op in proposal.get("operations", []))
        period = proposal.get("pace_period")
        passed = (result.get("state") == fixture["state"] and sorted(actions) == sorted(fixture["actions"])
                  and period == fixture["period"])
        rows.append(dict(id=index, passed=passed, status=code, expected=fixture["state"],
                         expected_actions=fixture["actions"], elapsed=round(perf_counter()-start, 3), result=result))
    return dict(total=len(rows), passed=sum(row["passed"] for row in rows), rows=rows, source="synthetic_only")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--live-synthetic", action="store_true")
    opts = parser.parse_args()
    report = live_synthetic() if opts.live_synthetic else run()
    opts.out.parent.mkdir(parents=True, exist_ok=True)
    opts.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{report['passed']}/{report['total']}")
    raise SystemExit(report["passed"] != report["total"])
