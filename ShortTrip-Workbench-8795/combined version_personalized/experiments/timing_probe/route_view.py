"""User-facing ordered routes derived from the same verified timed itinerary."""
from chinatravel.soft_constraints.time_preferences import time_policy, timing_adjustments


def route_view(plan, appointments=(), *, policy='strict'):
    selected_policy=time_policy(policy)
    adjustments=timing_adjustments(plan) if selected_policy.name=='flexible' else {'issues':[]}
    days = []
    previous_position = None
    for day in plan.get("itinerary", ()):
        stops, transfers, fixed = [], [], []
        for act in day["activities"]:
            intercity = act["type"] in {"train", "airplane"}
            name = act.get("position") or (act.get("start", "") + " → " + act.get("end", ""))
            destination = act.get("start") if intercity else act.get("position")
            legs = act.get("transports", ())
            if previous_position and destination:
                total = 0
                for leg in legs:
                    first, last = leg["start_time"].split(":"), leg["end_time"].split(":")
                    elapsed = 60 * (int(last[0]) - int(first[0])) + int(last[1]) - int(first[1])
                    total += elapsed % 1440
                transfers.append({"from": previous_position, "to": destination,
                                  "estimated_minutes": total if legs or previous_position == destination else None,
                                  "modes": list(dict.fromkeys(t["mode"] for t in legs)),
                                  "source": "local_snapshot_estimate"})
            stops.append({"order": len(stops) + 1, "name": name, "type": act["type"]})
            if intercity:
                fixed.append({"kind": act["type"], "name": name, "departure": act["start_time"],
                              "service": act.get("TrainID") or act.get("FlightID")})
            for appointment in appointments:
                if appointment["day"] == day["day"] and appointment["name"] == act.get("position"):
                    fixed.append({"kind": "appointment", "name": act["position"],
                                  "start": appointment["start_time"]})
            previous_position = act.get("end") if intercity else act.get("position")
        days.append({"day": day["day"], "stops": stops, "transfers": transfers, "fixed_events": fixed,
                     "adjustments":[item for item in adjustments['issues'] if item['day']==day['day']]})
    return {"days": days, "time_policy":selected_policy.name,
            "transport_basis": "本地快照估算，含路线中的步行接驳"}


def route_markdown(view):
    lines = ["# 每日路线", "", view["transport_basis"], ""]
    for day in view["days"]:
        lines += [f"## 第 {day['day']} 天", "",
                  " → ".join(stop["name"] for stop in day["stops"]), ""]
        for transfer in day["transfers"]:
            minutes = transfer["estimated_minutes"]
            estimate = "待查询" if minutes is None else f"约 {minutes} 分钟"
            if minutes==0 and transfer['modes']:
                estimate='少于 1 分钟（分钟级估算）'
            modes = "、".join(transfer["modes"]) or "同地点"
            lines.append(f"- {transfer['from']} → {transfer['to']}，{modes}，{estimate}")
        if day["fixed_events"]:
            lines += ["", "需遵守的固定事项", ""]
            for event in day["fixed_events"]:
                clock = event.get("departure") or event.get("start")
                lines.append(f"- {clock} {event['name']} {event.get('service', '')}".rstrip())
        if day.get('adjustments'):
            lines += ['', '可自行调整的事项', '']
            for item in day['adjustments']:
                lines.append(f"- {item['place']}，{item['message']}")
        lines.append("")
    return "\n".join(lines)

