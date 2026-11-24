from experiments.sequence_flex.core import Item, Policy, bounds, orders, realize, public_view, review
import unittest

def activity(name, kind="attraction", lo=480, hi=1200, duration=90):
    return Item({"position":name,"type":kind,"cost":0},lo,hi,duration)

def route(a,b,t):
    return [] if a==b else [{"start":a,"end":b,"start_time":f"{t//60:02d}:{t%60:02d}",
                            "end_time":f"{(t+10)//60:02d}:{(t+10)%60:02d}","mode":"walk","cost":0}]

def test_interval_retains_slack_not_single_clock():
    result=bounds([activity("A"),activity("B")],[10,10],480,1200)
    assert result==([490,590],[1010,1110])

def test_appointment_propagates_backwards():
    a=activity("A",duration=120)
    b=activity("预约",lo=900,hi=900)
    lo,hi=bounds([a,b],[10,30],480,1200)
    assert hi[0]==750 and lo[1]==hi[1]==900
    assert bounds([a,b],[10,30],800,1200) is None

def test_meal_gap_across_intervening_sight():
    items=[activity("早餐","breakfast",480,650,30),activity("公园",duration=30),
           activity("午餐","lunch",600,900,45)]
    lo,_=bounds(items,[0,10,10],480,1200,120)
    assert lo[2]>=lo[0]+150

def test_fixed_return_buffer_and_no_public_sight_clock():
    t=Item({"type":"train","start":"车站","end":"家","start_time":"18:00","end_time":"20:00","cost":0},1080,1080,0,True)
    result=realize([activity("公园"),t],"酒店",480,1440,route,Policy())
    assert result
    last=result["activities"][-1]
    from experiments.sequence_flex.core import minute
    assert minute(last["transports"][-1]["end_time"])<=1020
    view=public_view({"itinerary":[{"day":1,"activities":result["activities"]}]})
    assert "start_time" not in view["days"][0]["sequence"][0]
    assert "fixed_start" not in view["days"][0]["sequence"][0]
    assert view["days"][0]["sequence"][-1]["fixed_start"]=="18:00"

def test_cannot_shrink_visit_to_twenty_six_minutes():
    assert bounds([activity("博物馆",lo=1234,hi=1260-90)],[0],1234,1440) is None

def test_reorder_opportunity_and_meal_order():
    items=[activity("远"),activity("近"),activity("晚餐","dinner"),activity("午餐","lunch")]
    result=orders(items,"酒店",lambda a,b:1 if b=="近" else 10,Policy())
    assert result
    assert all([i.kind for i in o].index("lunch")<[i.kind for i in o].index("dinner") for o in result)

def test_uncertain_night_trip_is_not_quality_ready():
    p={"itinerary":[{"day":1,"activities":[{"type":"train","start_time":"00:03","end_time":"02:32","cost":1}]}]}
    r=review(p)
    assert "night_intercity_needs_confirmation" in r["issues"] and not r["delivery_ready"]

def test_public_appointment_retains_time():
    p={"itinerary":[{"day":2,"activities":[{"type":"attraction","position":"博物馆","start_time":"15:00","end_time":"16:30","cost":0}]}]}
    v=public_view(p,[{"day":2,"name":"博物馆","start_time":"15:00"}])
    assert v["days"][0]["sequence"][0]["fixed_start"]=="15:00"


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(unittest.FunctionTestCase(f) for n,f in globals().items() if n.startswith("test_"))

