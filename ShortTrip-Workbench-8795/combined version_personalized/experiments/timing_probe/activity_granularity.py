"""Coarser activity-type decisions for the historical C-group experiment."""

import json
import time


ACTIVITIES = ("attraction", "breakfast", "lunch", "dinner", "hotel", "back-intercity-transport")


def preference_order(text):
    try:
        value, _ = json.JSONDecoder().raw_decode(text[text.index("{"):])
        choices = value["priority"]
    except (ValueError, TypeError, KeyError, AttributeError):
        return ()
    if not isinstance(choices, list):
        return ()
    return tuple(dict.fromkeys(item for item in choices if isinstance(item, str) and item in ACTIVITIES))


class HalfDayActivityPolicy:
    """Use one model-provided ordering per day and half-day in a request."""

    def __init__(self):
        self.orders = {}
        self.events = []
        self.decisions = 0

    def decide(self, llm, candidates, query, day, clock, position, root):
        if len(candidates) == 1:
            self.decisions += 1
            return candidates[0]
        half = "morning" if int(clock.split(":", 1)[0]) < 12 else "afternoon"
        key = (day, half)
        if key not in self.orders:
            terminal = root.get("back_transport", {}) if day + 1 == query["days"] else root.get("accommodation", {})
            if not hasattr(terminal, "get"):
                terminal = {}
            facts = {
                "需求": query["nature_language"], "天数": query["days"], "第几天": day + 1,
                "时段": half, "首次看到的时刻": clock, "首次所在地点": position,
                "首次可选类型": list(candidates), "收尾锚点": terminal.get("From") or terminal.get("name"),
                "返程发车": terminal.get("BeginTime"),
            }
            content = (
                "为这半天给出一个活动类型优先顺序，供后续多个搜索节点使用。DFS 会按当前可用类型、"
                "真实交通、餐窗、营业和返程约束筛选，每个类型还有备选。考虑用户意图和半天的节奏，"
                "把到点要完成的用餐及返程留出余量。不要猜具体地点。"
                "只输出一个 JSON 对象，字段 priority 为一个包含以下类型的排序列表："
                + json.dumps(ACTIVITIES) + "\n真实上下文：" + json.dumps(facts, ensure_ascii=False)
            )
            started = time.perf_counter()
            raw = llm([{"role": "user", "content": content}], one_line=False)
            order = preference_order(raw)
            self.orders[key] = order
            self.events.append({"day": day + 1, "half_day": half, "priority": order,
                                "format_fallback": not order, "prompt_context": facts,
                                "seconds": time.perf_counter() - started})
        self.decisions += 1
        order = self.orders[key]
        return next((kind for kind in order if kind in candidates), candidates[0])


def experiment_agent(original, variant, on_instance):
    class Measured(original):
        def __init__(self, *args, **kwargs):
            self.policy = HalfDayActivityPolicy()
            super().__init__(*args, **kwargs)
            on_instance(self)

        def select_next_poi_type(self, candidates_type, plan, poi_plan, current_day, current_time, current_position):
            if variant == "baseline":
                return super().select_next_poi_type(candidates_type, plan, poi_plan, current_day, current_time, current_position)
            model = self.backbone_llm

            def prioritize(_messages, **_kwargs):
                # The C-group method applies its meal/return guards before calling
                # this adapter, so the list here is the actual remaining choices.
                choice = self.policy.decide(model, candidates_type, self.query, current_day,
                                            current_time, current_position, poi_plan)
                first = candidates_type.index(choice)
                candidates_type[:] = [choice, *candidates_type[:first], *candidates_type[first + 1:]]
                return "Type: " + choice

            self.backbone_llm = prioritize
            try:
                return super().select_next_poi_type(candidates_type, plan, poi_plan, current_day, current_time, current_position)
            finally:
                self.backbone_llm = model

    return Measured

