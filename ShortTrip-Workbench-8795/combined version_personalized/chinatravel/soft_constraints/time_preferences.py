"""Named, explicit timing profiles and independently reported user adjustments."""

from dataclasses import dataclass


@dataclass(frozen=True)
class TimePolicy:
    name: str
    breakfast: tuple
    lunch: tuple
    dinner: tuple
    attraction_start: int
    attraction_end: int
    maximum_wait: int | None

    @property
    def meal_windows(self):
        return {"breakfast": self.breakfast, "lunch": self.lunch, "dinner": self.dinner}


STRICT = TimePolicy("strict", (360, 540, 30), (660, 840, 60), (1020, 1200, 60), 480, 1260, 60)
FLEXIBLE = TimePolicy("flexible", (300, 660, 30), (600, 960, 45), (960, 1380, 45), 360, 1380, None)


def time_policy(name="strict"):
    if isinstance(name, TimePolicy):
        return name
    if name == "strict":
        return STRICT
    if name == "flexible":
        return FLEXIBLE
    raise ValueError("time_policy must be strict or flexible")


def minutes(value):
    hour, minute = map(int, value.split(":"))
    if hour < 0 or not 0 <= minute < 60:
        raise ValueError("Invalid time")
    return hour * 60 + minute


def timing_adjustments(plan):
    """Explain deviations from customary timing independently of feasibility."""
    issues = []
    for day in plan.get("itinerary", ()):
        previous_end = None
        for activity in day["activities"]:
            start = minutes(activity["start_time"])
            finish = minutes(activity["end_time"])
            kind = activity["type"]
            location = activity.get("position") or activity.get("end")
            if kind in STRICT.meal_windows:
                lo, hi, _ = STRICT.meal_windows[kind]
                deviation = max(0, lo - start, start - hi + 1)
                if deviation:
                    issues.append({"day": day["day"], "place": location, "kind": "meal_time",
                                   "minutes": deviation, "penalty": round(deviation / 60, 3),
                                   "message": "用餐时间偏离常用饭点，可调整用餐位置或顺序"})
            if kind == "attraction":
                length = finish - start
                if 0 < length < 30:
                    issues.append({"day": day["day"], "place": location, "kind": "short_visit",
                                   "minutes": length, "penalty": round((30 - length) / 15, 3),
                                   "message": "此处游览时间偏短，建议留出更多时间或减少一个地点"})
                if start < STRICT.attraction_start or finish > STRICT.attraction_end:
                    issues.append({"day": day["day"], "place": location, "kind": "early_or_late_visit",
                                   "minutes": max(0, STRICT.attraction_start - start, finish - STRICT.attraction_end),
                                   "penalty": 1.0, "message": "此处需要较早或较晚游览，可调整当天路线"})
            if previous_end is not None:
                legs = activity.get("transports", ())
                arrival = minutes(legs[-1]["end_time"]) if legs else previous_end
                waiting = start - arrival
                if waiting > 60:
                    issues.append({"day": day["day"], "place": location, "kind": "long_wait",
                                   "minutes": waiting, "penalty": 0.0,
                                   "message": "此处等候较长，可延后出发或替换前一项活动"})
            previous_end = finish
    return {"issues": issues, "penalty": round(sum(item["penalty"] for item in issues), 3)}

