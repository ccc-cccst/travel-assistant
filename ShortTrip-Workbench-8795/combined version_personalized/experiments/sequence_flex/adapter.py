"""Adapter to the unchanged ChinaTravel catalog, route engine and validators."""
import copy
import math
import time
import sys
from pathlib import Path
from .core import Item, Policy, WINDOWS, MEALS, minute, clock, orders, realize, review


class Catalog:
    def __init__(self):
        from chinatravel.environment.tools.attractions.apis import Attractions
        from chinatravel.environment.tools.restaurants.apis import Restaurants
        from chinatravel.environment.tools.transportation.apis import Transportation
        self.attractions=Attractions(lang="zh").data
        self.restaurants=Restaurants(lang="zh").data
        self.transport=Transportation(lang="zh")
        self.routes={}
        self.route_queries=0
        self.route_misses=0

    def lookup(self, city, kind, name):
        data=self.attractions if kind=="attraction" else self.restaurants
        rows=data[city]
        matches=rows[rows["name"]==name]
        return matches.iloc[0].to_dict() if len(matches) else None

    def route(self, city, people, modes, start, end, depart, cost_weight=.08):
        if start==end:
            return []
        self.route_queries+=1
        candidates=[]
        for mode in modes:
            key=(city, people, mode, start, end, int(depart))
            if key not in self.routes:
                self.route_misses+=1
                try:
                    legs=copy.deepcopy(self.transport.goto(city,start,end,clock(depart),mode))
                    if not isinstance(legs,list) or not legs:
                        legs=None
                    else:
                        for leg in legs:
                            if minute(leg["end_time"])<minute(leg["start_time"]):
                                raise ValueError("Cross-day inner-city route")
                            leg["price"]=leg["cost"]
                            if leg["mode"]=="metro":
                                leg["tickets"]=people
                                leg["cost"]*=people
                            elif leg["mode"]=="taxi":
                                leg["cars"]=math.ceil(people/4)
                                leg["cost"]*=leg["cars"]
                except (ValueError, TypeError, KeyError, IndexError):
                    legs=None
                self.routes[key]=legs
            legs=self.routes[key]
            if legs is not None:
                length=sum(minute(t["end_time"])-minute(t["start_time"]) for t in legs)
                walk=sum(minute(t["end_time"])-minute(t["start_time"]) for t in legs if t["mode"]=="walk")
                # Mode choice is multi-cost, then old hard constraints recheck the plan.
                cost=sum(t["cost"] for t in legs)
                candidates.append((length+walk*.3+cost*cost_weight, legs))
        return copy.deepcopy(min(candidates,key=lambda p:p[0])[1]) if candidates else None


def allowed_modes(query, plan):
    # Frozen upstream decisions. Do not infer restrictions from hidden gold answers.
    modes=tuple(dict.fromkeys(t["mode"] for d in plan["itinerary"] for a in d["activities"]
                              for t in a.get("transports",[]) if t["mode"] in ("metro","taxi")))
    return modes or ("walk",)


def make_items(activities, catalog, city, day, appointments, policy):
    result=[]
    for a in activities:
        kind=a["type"]
        locked=next((x for x in appointments if x["day"]==day and x["name"]==a.get("position")),None)
        if kind in ("train","airplane"):
            depart=minute(a["start_time"])
            result.append(Item(a,depart,depart,0,True))
            continue
        if kind=="accommodation":
            result.append(Item(a,0,1380,0))
            continue
        row=catalog.lookup(city,kind,a["position"])
        if row is None:
            if kind=="breakfast" and a.get("price")==0:  # original hotel breakfast contract
                opens,closes=300,660
                minimum=30
            else:
                raise ValueError("missing_catalog:"+a["position"])
        else:
            opens,closes=minute(row["opentime"]),minute(row["endtime"])
            raw=row.get("recommendmintime")
            minimum=math.ceil(float(raw)*60) if raw is not None and math.isfinite(float(raw)) else policy.default_visit
        if kind in MEALS:
            lower,upper,duration=WINDOWS[kind]
            lower=max(lower,opens)
            upper=min(upper-1,closes-duration)
        else:
            duration=max(30,minimum,minute(a["end_time"])-minute(a["start_time"]))
            lower=max(360,opens)
            upper=min(1380,closes)-duration
        if locked:
            duration=locked.get("duration_minutes",duration)
            lower=upper=minute(locked["start_time"])
            if lower<opens or lower+duration>closes:
                raise ValueError("appointment_outside_opening")
        if lower>upper:
            raise ValueError("duration_does_not_fit:"+a["position"])
        result.append(Item(a,lower,upper,duration,bool(locked)))
    return result


def reschedule(plan, query, catalog, policy=Policy(), seconds=20):
    """Retain dates, activity set, ticket/hotel identity; search daily orders afresh."""
    sibling=str(Path(__file__).resolve().parents[1]/"c_only")
    if sibling not in sys.path:
        sys.path.append(sibling)
    from experiments.c_only.rules import ticket_interval
    started=time.perf_counter()
    city=plan["target_city"]
    modes=tuple(query.get("_sequence_modes") or allowed_modes(query,plan))
    route=lambda a,b,t:catalog.route(city,plan["people_number"],modes,a,b,t,policy.route_cost_weight)
    result=copy.deepcopy(plan)
    previous_hotel=None
    hotel_checkin=None
    windows=[]
    for di,day in enumerate(result["itinerary"]):
        if time.perf_counter()-started>seconds:
            return None,{"reason":"local_time_budget"}
        acts=day["activities"]
        retained=[]
        if di==0:
            if not acts or acts[0]["type"] not in ("train","airplane"):
                return None,{"reason":"missing_outbound"}
            outbound=copy.deepcopy(acts[0])
            retained=[outbound]
            interval=ticket_interval(outbound,day["day"])
            if interval["end_day"]!=day["day"]:
                return None,{"reason":"overnight_outbound_requires_day_mapping"}
            start=int(interval["end"])%1440
            position=outbound["end"]
            movable=acts[1:]
        else:
            if previous_hotel is None:
                return None,{"reason":"missing_overnight_anchor"}
            start=max(420,hotel_checkin+policy.sleep_minutes-1440)
            position=previous_hotel
            movable=acts
        try:
            items=make_items(movable,catalog,city,day["day"],query.get("appointments",[]),policy)
        except ValueError as exc:
            return None,{"reason":str(exc)}
        def estimate(a,b):
            r=route(a,b,540)
            return sum(minute(t["end_time"])-minute(t["start_time"]) for t in r) if r is not None else float("inf")
        feasible=[]
        for order in orders(items,position,estimate,policy):
            if time.perf_counter()-started>seconds:
                break
            candidate=realize(order,position,start,1440,route,policy)
            if candidate:
                temp={"itinerary":[{"day":day["day"],"activities":retained+candidate["activities"]}]}
                m=review(temp,policy)["metrics"]
                # Ordering objective only: keep visited identities, reduce travel and awkward meals.
                meal_deviation=sum(abs(minute(a["start_time"])-{"breakfast":480,"lunch":720,"dinner":1080}[a["type"]])
                                   for a in candidate["activities"] if a["type"] in MEALS)
                rank=m["city_transit_minutes"]+.3*m["walk_minutes"]+.05*meal_deviation
                feasible.append((rank,candidate))
        if not feasible:
            return None,{"reason":"no_feasible_order","day":day["day"]}
        chosen=min(feasible,key=lambda x:x[0])[1]
        day["activities"]=retained+chosen["activities"]
        windows.append({"day":day["day"],"items":chosen["windows"]})
        hotels=[a for a in day["activities"] if a["type"]=="accommodation"]
        if hotels:
            previous_hotel=hotels[-1]["position"]
            hotel_checkin=minute(hotels[-1]["start_time"])
    # A missing appointment must fail, including unknown names or omitted days.
    for p in query.get("appointments",[]):
        if not any(d["day"]==p["day"] and a.get("position")==p["name"] and a["start_time"]==p["start_time"]
                   for d in result["itinerary"] for a in d["activities"]):
            return None,{"reason":"missing_appointment"}
    return result,{"reason":"witness_found","windows":windows,"seconds":time.perf_counter()-started}

