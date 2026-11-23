"""Opt-in return preflight and grouped ranking for the original C-group DFS."""

import json
import math
import re
import time

from chinatravel.agent.nesy_agent.pruning import clock, fixed_cost, number, selection_reason, ticket_kind
from experiment_agent import ticket
from rules import ticket_interval


def compatible_rooms(hotel, people, count, wanted_type):
    beds = number(hotel.get("numbed"))
    if not beds or (wanted_type is not None and wanted_type != beds):
        return None
    rooms = math.ceil(people / beds) if count is None else count
    if count is not None and (wanted_type is not None or beds == 2):
        return rooms
    return rooms if people <= beds * rooms < people + beds else None


def ticket_id(row):
    return row.get("TrainID" if ticket_kind(row) == "train" else "FlightID")


def compatible_returns(frame, query, go):
    candidates = []
    for position, (_, back) in enumerate(frame.iterrows()):
        try:
            if ticket_interval(ticket(back), query["days"])["end"] > query["days"] * 1440:
                continue
        except (ValueError, TypeError, KeyError):
            continue
        leave = clock(back.get("BeginTime"))
        arrival = clock(go.get("EndTime"))
        if query["days"] == 1 and leave is not None and arrival is not None and leave <= arrival:
            continue
        if not selection_reason(query, {"go_transport": go, "back_transport": back}):
            candidates.append(position)
    return candidates


def preflight(frame, eligible, query, go, hotels):
    """Reject only when the original branch cannot reach its DFS entry."""
    if not eligible:
        return "no_compatible_return"
    if query["days"] == 1:
        return None
    if not hotels:
        return "no_compatible_hotel_in_ranked_list"
    fares = [(number(frame.iloc[idx].get("Cost")), idx) for idx in eligible]
    if any(fare is None for fare, _ in fares):
        return None
    cheapest = frame.iloc[min(fares)[1]]
    for hotel, rooms in hotels:
        combo = {"go_transport": go, "back_transport": cheapest, "accommodation": hotel}
        if selection_reason(query, combo, rooms) is None:
            return None
    return "no_affordable_ranked_hotel_and_return"


def ticket_summary(row):
    return {"id": str(ticket_id(row)), "departs": row["BeginTime"], "arrives": row["EndTime"],
            "from": row["From"], "to": row["To"], "fare_per_person": number(row["Cost"])}


def displayed_returns(frame, eligible):
    def by_time(i):
        return -(clock(frame.iloc[i]["BeginTime"]) or 0)

    def by_price(i):
        return number(frame.iloc[i]["Cost"])

    latest = sorted(eligible, key=lambda i: (by_time(i), by_price(i)))[:8]
    cheapest = sorted(eligible, key=lambda i: (by_price(i), by_time(i)))[:4]
    return list(dict.fromkeys(latest + cheapest))


def decode_rankings(answer, groups):
    try:
        decoded = json.loads(answer.strip())
    except (ValueError, TypeError):
        return {}
    if not isinstance(decoded, dict):
        return {}
    results = {}
    for group, choices in groups.items():
        ids = decoded.get(group)
        if isinstance(ids, list):
            verified = list(dict.fromkeys(choices[i] for i in ids if isinstance(i, str) and i in choices))
            if verified:
                results[group] = verified
    return results


class ReturnExperiment:
    """Attach to one run; retain the outer enumeration, constraints and DFS."""

    def __init__(self, agent, screen, batch_size, check_budget, rescue=False):
        self.agent = agent
        self.screen = screen
        self.batch_size = batch_size
        self.check_budget = check_budget
        self.events = []
        self.screened = {}
        self.rankings = {}
        self.hotels = []
        self.outbounds = []
        self.return_rank_started = False
        self.outbound_pruned = False
        self.rescue = rescue
        self.outbound_format_failed = False
        self.hotel_rescued = False
        self.single = agent.ranking_intercity_transport_back
        rerank_go = agent.reranking_intercity_transport_go_with_constraints
        rerank_hotel = agent.reranking_hotel_with_constraints
        decide_rooms = agent.decide_rooms
        rank_hotel = agent.ranking_hotel

        def capture_go(ranking, frame, query):
            self.outbound_format_failed = bool(agent.llm_rec_format_error and query["days"] > 1)
            order = rerank_go(ranking, frame, query)
            self.outbounds = [frame.iloc[i] for i in order]
            return order

        def capture_rooms(query):
            self.room_choice = (None, None) if self.batch_size and query["days"] == 1 else decide_rooms(query)
            return self.room_choice

        def rank_hotel_if_needed(frame, query):
            return [] if self.batch_size and query["days"] == 1 else rank_hotel(frame, query)

        def capture_hotels(ranking, frame, query, room_number):
            order = rerank_hotel(ranking, frame, query, room_number)
            if query["days"] > 1:
                for i in order:
                    hotel = frame.iloc[i]
                    rooms = compatible_rooms(hotel, query["people_number"], *self.room_choice)
                    if rooms is not None:
                        self.hotels.append((hotel, rooms))
                if self.screen and self.batch_size and not self.hotels:
                    fallback = []
                    for i, (_, hotel) in enumerate(frame.iterrows()):
                        rooms = compatible_rooms(hotel, query["people_number"], *self.room_choice)
                        price = number(hotel.get("price"))
                        if rooms is None or price is None or selection_reason(query, {"accommodation": hotel}, rooms):
                            continue
                        fallback.append((price * rooms, i))
                        self.hotels.append((hotel, rooms))
                    if fallback:
                        self.hotel_rescued = True
                        self.events.append({"event": "hotel_recovery", "hotels": len(fallback)})
                        return [i for _, i in sorted(fallback)]
            return order

        agent.reranking_intercity_transport_go_with_constraints = capture_go
        agent.reranking_hotel_with_constraints = capture_hotels
        agent.ranking_hotel = rank_hotel_if_needed
        agent.decide_rooms = capture_rooms
        agent.ranking_intercity_transport_back = self.rank
        if self.screen and self.batch_size:
            agent.ranking_attractions = self.with_consistent_cost(agent.ranking_attractions)
            agent.ranking_restaurants = self.with_consistent_cost(agent.ranking_restaurants)

    def with_consistent_cost(self, rank):
        def ranking(plan, poi_plan, current_day, current_time, current_position, intercity_with_hotel_cost):
            go = poi_plan.get("go_transport")
            if go is not None and poi_plan.get("accommodation") is not None:
                # Only correct the original cost when the outbound leg replaces a hotel night.
                if self.agent.outbound_next(go)[0] > 0:
                    cost = fixed_cost(self.agent.query, poi_plan, self.agent.required_rooms)
                    if cost is not None and abs(cost - intercity_with_hotel_cost) > 0.01:
                        if not getattr(self.agent, f"{rank.__name__}_flag", False):
                            self.events.append({"event": "overnight_ranking_cost", "ranking": rank.__name__,
                                                "old_cost": intercity_with_hotel_cost, "cost": cost})
                        intercity_with_hotel_cost = cost
            return rank(plan, poi_plan, current_day, current_time, current_position, intercity_with_hotel_cost)

        return ranking

    @staticmethod
    def key(go):
        return (str(ticket_id(go)), go["BeginTime"], go["EndTime"], go["From"], go["To"], go["Cost"])

    def screen_go(self, frame, query, go):
        key = self.key(go)
        if key not in self.screened:
            self.check_budget()
            eligible = compatible_returns(frame, query, go)
            reason = preflight(frame, eligible, query, go, self.hotels) if self.screen else None
            self.screened[key] = eligible, reason
        return self.screened[key]

    def request_batch(self, frame, query, batch):
        payload, allowed = [], {}
        for go, eligible in batch:
            label = str(len(payload) + 1)
            shown = displayed_returns(frame, eligible)
            allowed[label] = {str(ticket_id(frame.iloc[idx])): idx for idx in shown}
            payload.append({"group": label, "outbound": ticket_summary(go),
                            "returns": [ticket_summary(frame.iloc[idx]) for idx in shown]})
        prompt = (
            "Rank suitable return services within each outbound group for this travel request. "
            "Consider useful sightseeing time, fare, terminal, transport preferences and the requested return deadline. "
            "Use only the real return IDs displayed within the corresponding group. "
            'Return only a JSON object mapping each group label to a ranked array of up to six IDs, e.g. {"1":["G10"]}.\n'
            f"Request: {query['nature_language']}\nDays: {query['days']}\n"
            + json.dumps(payload, ensure_ascii=False)
        )
        started = time.perf_counter()
        answer = self.agent.backbone_llm([{"role": "user", "content": prompt}], one_line=False,
                                         json_mode=True, max_tokens=384)
        self.agent.llm_inference_time_count += time.perf_counter() - started
        self.agent.llm_rec_count += 1
        decoded = decode_rankings(answer, allowed)
        self.events.append({"event": "batch", "groups": len(batch), "valid_groups": len(decoded)})
        for label, (go, eligible) in zip(allowed, batch):
            preferred = decoded.get(label)
            if preferred is None:
                self.events.append({"event": "batch_local_fallback", "go": self.key(go)[0]})
                preferred = list(allowed[label].values())
            # Keep the unshown fallbacks available to the original constraint reranker.
            self.rankings[self.key(go)] = list(dict.fromkeys(preferred + eligible))

    def rank(self, frame, query, go):
        eligible, reason = self.screen_go(frame, query, go)
        key = self.key(go)
        if reason:
            self.outbound_pruned = True
            self.events.append({"event": "pruned", "go": key[0], "reason": reason})
            return []
        if key in self.rankings:
            self.events.append({"event": "batch_reuse", "go": key[0]})
            return self.rankings[key]
        use_batch = self.batch_size and eligible and (
            query["days"] == 1 or self.return_rank_started or self.outbound_pruned or not self.screen
            or (self.rescue and self.outbound_format_failed)
        )
        self.return_rank_started = True
        if use_batch:
            batch = [(go, eligible)]
            after_current = False
            for later in self.outbounds:
                if not after_current:
                    after_current = self.key(later) == key
                    continue
                if len(batch) >= self.batch_size:
                    break
                options, reject = self.screen_go(frame, query, later)
                if options and not reject and self.key(later) not in self.rankings:
                    batch.append((later, options))
            self.request_batch(frame, query, batch)
            if key in self.rankings:
                return self.rankings[key]
        return self.single(frame, query, go)
