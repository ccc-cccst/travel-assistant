"""User-selected preferences over observed itinerary features."""

from copy import deepcopy
import math

from chinatravel.soft_constraints.scoring import duration, score_plan as legacy_score


WEIGHTS = dict(cost=1., transport=1., walking=1., pace=1., interest=2., variety=.5, coherence=1., experience=2.)
CULTURE_TYPES = {"博物馆/纪念馆", "历史古迹", "艺术馆", "美术馆", "博物馆", "文化场馆", "展览馆"}
PRESETS = {
    "balanced": {"label": "均衡安排", "weights": WEIGHTS, "pace": "balanced"},
    "budget": {"label": "节省开销", "weights": {**WEIGHTS, "cost": 4.}, "pace": "balanced"},
    "relaxed": {"label": "轻松游览", "weights": {**WEIGHTS, "pace": 4., "transport": 3.}, "pace": "relaxed"},
    "culture": {"label": "文化探索", "weights": {**WEIGHTS, "interest": 5., "variety": 2.},
                "pace": "balanced", "interest_types": ["博物馆/纪念馆", "历史古迹"]},
    "family": {"label": "少走路", "weights": {**WEIGHTS, "walking": 5., "transport": 3., "pace": 2.},
               "pace": "relaxed"},
}
HARD_LIMITS = {"max_daily_walk_minutes", "max_daily_transport_minutes", "max_daily_attractions"}


def normalize_profile(value=None):
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("偏好设置必须是对象")
    allowed = {"preset", "weights", "pace", "interest_types", "restaurant_types", "hard_limits"}
    unknown = set(value) - allowed
    if unknown:
        raise ValueError("无法识别偏好字段 " + ", ".join(sorted(unknown)))
    preset = value.get("preset", "balanced")
    if preset not in PRESETS:
        raise ValueError("请选择有效的偏好方案")
    result = deepcopy(PRESETS[preset])
    result.update(preset=preset, restaurant_types=[], hard_limits={})
    result.setdefault("interest_types", [])
    for key in ("pace", "interest_types", "restaurant_types", "hard_limits"):
        if key in value:
            result[key] = deepcopy(value[key])
    if result["pace"] not in ("relaxed", "balanced", "full"):
        raise ValueError("节奏应为 relaxed、balanced 或 full")
    for key in ("interest_types", "restaurant_types"):
        if not isinstance(result[key], list) or any(not isinstance(v, str) or not v.strip() for v in result[key]):
            raise ValueError(key + " 应为非空名称组成的数组")
        result[key] = list(dict.fromkeys(v.strip() for v in result[key]))
    weights = value.get("weights", {})
    if not isinstance(weights, dict) or set(weights) - set(WEIGHTS):
        raise ValueError("权重字段无效")
    result["weights"].update(weights)
    limits = result["hard_limits"]
    if not isinstance(limits, dict) or set(limits) - HARD_LIMITS:
        raise ValueError("个性化硬限制字段无效")
    for key, number in {**result["weights"], **limits}.items():
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or number < 0:
            raise ValueError(key + " 必须是有限非负数")
    if sum(result["weights"].values()) <= 0:
        raise ValueError("至少一项偏好权重应大于零")
    return result


def evaluate_preferences(plan, profile, env, request=None):
    """Calculate features once per candidate, with hard limits kept explicit."""
    old = legacy_score(plan)
    city = plan["target_city"]
    attractions = env.attractions.data[city].set_index("name")
    restaurants = env.restaurants.data[city].set_index("name")
    request = request or {}
    wanted_names = set(request.get("preferred_attractions", ()))
    wanted_restaurants = set(request.get("preferred_restaurants", ()))
    dimensions = set(request.get("preference_dimensions", ()))
    special = {label: False for feature, label in (
        ("culture", "文化类景点"), ("first_day_sightseeing", "首日游览")) if feature in dimensions}
    days, costs, names, foods, kinds, cuisines, evidence = [], [], set(), set(), set(), set(), []
    for day in plan["itinerary"]:
        walking = transport = busy = sightseeing = intercity = 0.
        count = 0
        for activity in day["activities"]:
            costs.append(activity.get("cost"))
            for leg in activity.get("transports", []):
                costs.append(leg.get("cost"))
                minutes = duration(leg)
                if minutes is None:
                    return {"eligible": False, "violations": ["缺少可计算的交通时间"], "status": "unknown"}
                transport += minutes
                if leg["mode"] == "walk":
                    walking += minutes
            kind = activity["type"]
            if kind in ("attraction", "breakfast", "lunch", "dinner"):
                minutes = duration(activity)
                if minutes is None:
                    return {"eligible": False, "violations": ["缺少可计算的活动时间"], "status": "unknown"}
                busy += minutes
                if kind == "attraction":
                    sightseeing += minutes
            elif kind in ("train", "airplane"):
                minutes = duration(activity)
                if minutes is None:
                    return {"eligible": False, "violations": ["缺少可计算的城际交通时间"], "status": "unknown"}
                intercity += minutes
            name = activity.get("position")
            if kind == "attraction":
                count += 1
                names.add(name)
                if name in wanted_names:
                    evidence.append({"day": day["day"], "position": name, "feature": "点名景点"})
                if "首日游览" in special and day["day"] == 1:
                    special["首日游览"] = True
                    evidence.append({"day": day["day"], "position": name, "feature": "首日游览"})
                if name in attractions.index:
                    category = attractions.loc[name, "type"]
                    if hasattr(category, "iloc"):
                        category = category.iloc[0]
                    kinds.add(str(category))
                    if "文化类景点" in special and str(category) in CULTURE_TYPES:
                        special["文化类景点"] = True
                        evidence.append({"day": day["day"], "position": name, "feature": "文化类景点"})
                    if str(category) in profile["interest_types"]:
                        evidence.append({"day": day["day"], "position": name, "feature": str(category)})
            elif kind in ("breakfast", "lunch", "dinner") and name in restaurants.index:
                foods.add(name)
                if name in wanted_restaurants:
                    evidence.append({"day": day["day"], "position": name, "feature": "点名餐厅"})
                category = restaurants.loc[name, "cuisine"]
                if hasattr(category, "iloc"):
                    category = category.iloc[0]
                cuisines.add(str(category))
                if str(category) in profile["restaurant_types"]:
                    evidence.append({"day": day["day"], "position": name, "feature": str(category)})
        days.append({"day": day["day"], "walking_minutes": walking, "transport_minutes": transport,
                     "attraction_count": count, "sightseeing_minutes": sightseeing,
                     "intercity_minutes": intercity, "busy_minutes": busy + transport + intercity})
    if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) or v < 0 for v in costs):
        return {"eligible": False, "violations": ["费用数据不完整"], "status": "unknown"}
    total = sum(costs)
    count_days = max(1, len(days))
    wanted = set(profile["interest_types"])
    wanted_food = set(profile["restaurant_types"])
    matched = len(wanted & kinds) + len(wanted_food & cuisines) + len(wanted_names & names) + len(wanted_restaurants & foods)
    matched += sum(special.values())
    requested = len(wanted) + len(wanted_food) + len(wanted_names) + len(wanted_restaurants) + len(special)
    max_busy = {"relaxed": 360, "balanced": 480, "full": 600}[profile["pace"]]
    used_rules = {"station_wait", "airport_wait", "idle_gap", "no_attractions",
                  "too_little_sightseeing", "long_day_span", "daily_transit_distance"}
    coherence_evidence = [item for item in old["details"] if item["rule"] in used_rules]
    coherence_penalty = sum(item["penalty"] for item in coherence_evidence)
    # Saturating scales prevent a large raw CNY value from dominating minutes.
    components = {
        "cost": 100 / (1 + total / (500 * count_days * plan["people_number"])),
        "transport": 100 / (1 + sum(d["transport_minutes"] for d in days) / (90 * count_days)),
        "walking": 100 / (1 + sum(d["walking_minutes"] for d in days) / (45 * count_days)),
        "pace": sum(100 / (1 + max(0, d["busy_minutes"] - max_busy) / 120) for d in days) / count_days,
        "interest": 100 * matched / requested if requested else None,
        "variety": 100 * min(1, len(kinds) / max(1, min(3, len(names)))),
        "coherence": 100 / (1 + coherence_penalty / 10),
        "experience": 100 * min(1, sum(d["sightseeing_minutes"] for d in days) /
                               (count_days * {"relaxed": 120, "balanced": 150, "full": 180}[profile["pace"]])),
    }
    violations = []
    for key, feature in (("max_daily_walk_minutes", "walking_minutes"),
                         ("max_daily_transport_minutes", "transport_minutes"),
                         ("max_daily_attractions", "attraction_count")):
        limit = profile["hard_limits"].get(key)
        if limit is not None:
            for day in days:
                if day[feature] > limit + 1e-6:
                    violations.append({"day": day["day"], "feature": feature, "actual": day[feature], "limit": limit})
    active = {k: w for k, w in profile["weights"].items() if w > 0 and components[k] is not None}
    total_weight = sum(active.values())
    value = sum(components[k] * w for k, w in active.items()) / total_weight if total_weight else None
    return {
        "status": "known", "eligible": not violations, "violations": violations,
        "score": round(value, 4) if value is not None else None,
        "components": {k: round(v, 2) if v is not None else None for k, v in components.items()},
        "metrics": {"total_cost": round(total, 2),
                    "transport_minutes": sum(d["transport_minutes"] for d in days),
                    "walking_minutes": sum(d["walking_minutes"] for d in days),
                    "sightseeing_minutes": sum(d["sightseeing_minutes"] for d in days),
                    "intercity_minutes": sum(d["intercity_minutes"] for d in days),
                    "unique_attractions": len(names), "matched_preferences": matched,
                    "requested_preferences": requested, "days": days},
        "evidence": evidence, "missing_preferences": sorted((wanted - kinds) | (wanted_food - cuisines)
            | (wanted_names - names) | (wanted_restaurants - foods) | {label for label, hit in special.items() if not hit}),
        "legacy_penalties": old["categories"], "legacy_unknown": old["unknown"],
        "coherence_evidence": coherence_evidence, "legacy_coverage": old["coverage"],
        "profile": profile, "calibration": "engineering_proxy_v1",
    }
