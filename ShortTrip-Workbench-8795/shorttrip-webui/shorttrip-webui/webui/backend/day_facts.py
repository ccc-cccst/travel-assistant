"""Read current POI and weather data without changing the planning snapshot."""
from datetime import date, datetime, timedelta, timezone
import re
import unicodedata
from urllib.parse import urlencode

from amap_route import CITYCODE, RouteError, haversine

AMAP_POI = "https://lbs.amap.com/api/webservice/guide/api-advanced/newpoisearch"
AMAP_WEATHER = "https://lbs.amap.com/api/webservice/guide/api/weatherinfo"
CHINA = timezone(timedelta(hours=8))
WEATHER_CODES = {
    "北京":"110000", "上海":"310000", "天津":"120000", "重庆":"500000", "南京":"320100",
    "武汉":"420100", "成都":"510100", "西安":"610100", "广州":"440100", "深圳":"440300",
    "杭州":"330100", "苏州":"320500", "厦门":"350200", "宁波":"330200", "长沙":"430100", "青岛":"370200",
}
POI_TYPES = {"restaurant":("05",), "hotel":("10",), "attraction":("08","11","14","16")}

# Operator-published normal hours, assigned to a named site. Special closures and reservations are checked on the linked page.
OFFICIAL = {
    ("上海","上海博物馆"): {
        "name":"上海博物馆（人民广场馆）",
        "archivePoint":[31.234149,121.482358], "address":"人民大道201号，从武胜路南门入馆",
        "source":"https://www.shanghaimuseum.cn/mu/frontend/pg/service/visit-west",
        "closed":[0], "hours":["09:00","17:00"], "lastEntry":"15:00",
        "notice":"特展期间购票入场，具体时段以馆方公告为准。", "checkedOn":"2026-11-22",
    },
    ("成都","成都博物馆"): {
        "name":"成都博物馆","source":"https://www.cdmuseum.com/zhinan/index.html",
        "closed":[0], "hours":["09:00","17:00"], "lastEntry":"16:30",
        "extendedDays":[4,5], "extendedHours":["09:00","20:30"],"extendedLastEntry":"20:00",
        "notice":"按馆方要求核对预约、限流及节假日公告。", "checkedOn":"2026-11-22",
    },
    ("成都","成都大熊猫繁育研究基地"): {
        "name":"成都大熊猫繁育研究基地","source":"https://www.panda.org.cn/cn/news/notice/2026-10-15/8896.html",
        "dateRange":["2026-11-01","2027-02-28"], "closed":[],
        "hours":["08:00","17:30"], "lastEntry":"16:30",
        "notice":"冬季上午票 08:00–12:00 入园，下午票 12:00–16:30 入园；请按票面时段核对。",
        "checkedOn":"2026-11-22",
    },
    ("杭州","中国丝绸博物馆"): {
        "name":"中国丝绸博物馆","source":"https://www.chinasilkmuseum.com/cgjyy/index_6.aspx",
        "closed":[], "hours":["09:00","17:00"], "lastEntry":"16:30", "exceptionDays":[0],
        "notice":"周一和节假日时段建议以馆方最新公告为准。", "checkedOn":"2026-11-22",
    },
    ("重庆","重庆动物园"): {
        "name":"重庆动物园","archivePoint":[29.509218,106.512622],
        "address":"九龙坡区西郊一村1号",
        "source":"https://cqjlp.gov.cn/bmjz/qzfbm_97119/qwhlyw_97727/zwgk_97124/gkml/jczwgk/ggwhfwly/ggfw/ggwljgml/202608/t20260807_15904141.html",
        "sourceLabel":"九龙坡区文旅名录", "closed":[], "hours":["08:00","17:30"], "lastEntry":"16:30",
        "notice":"高德和文旅名录的闭园时间有差异，出行前请核对园方当天公告。", "checkedOn":"2026-11-22",
    },
}
PLACE_NOTICES = {
    ("重庆","attraction","重庆两江游"): {
        "text":"请按船票核对具体班次和登船码头。洪崖洞、朝天门均有登船点，快照时段仅供参考。",
        "source":"https://www.cq.gov.cn/ywdt/jrcq/202604/t20260405_15591125.html",
        "sourceLabel":"重庆市政府乘船提示", "scheduleRequired":True,
    },
    ("重庆","restaurant","重庆国贸格兰维大酒店·山城映像中餐厅"): {
        "text":"餐厅位于渝中区青年路66号的国贸格兰维大酒店内，营业及午晚餐时段请向酒店核对。",
        "source":"https://www.glenviewitcplazachongqing.com/contact.html",
        "sourceLabel":"酒店联系页",
    },
}
RESTAURANT_ALIASES = {
    ("重庆","仪表厂火锅(解放碑店)"):("仪表厂火锅(总店)",),
}
# Large POIs explicitly identified by city and venue name. The map point may be a different entrance.
LARGE_SITES = {
    ("上海","外滩"):("外滩",),
    ("上海","上海博物馆"):("上海博物馆(人民广场馆)",),
    ("杭州","西湖风景名胜区"):("杭州西湖风景名胜区",),
    ("杭州","雷峰塔"):("雷峰塔景区",),
    ("成都","成都大熊猫繁育研究基地"):("成都大熊猫繁育研究基地",),
    ("成都","摩诃池"):("摩诃池公园",),
    ("重庆","重庆动物园"):("重庆动物园",),
}


def normalize(name):
    return re.sub(r"[^\w]", "", unicodedata.normalize("NFKC", name).casefold())


def info_text(fields, key):
    value = fields.get(key)
    return value.strip() if isinstance(value, str) and value.strip() not in {"","暂无","未知","--"} else None


def temperature(cast, key):
    value = str(cast.get(key, "")).strip()
    return int(value) if re.fullmatch(r"-?\d{1,2}", value) else None


def official_for(city, name, on=None, pos=None):
    row = OFFICIAL.get((city,name))
    if not row or ("archivePoint" in row and (not pos or haversine(pos,row["archivePoint"]) > .1)):
        return None
    if "dateRange" in row and (not on or not row["dateRange"][0] <= on.isoformat() <= row["dateRange"][1]):
        return None
    weekday = on.weekday() if on else None
    extended = weekday in row.get("extendedDays", ())
    uncertain = weekday in row.get("exceptionDays", ())
    hours = row["extendedHours"] if extended else row["hours"]
    entry = row["extendedLastEntry"] if extended else row["lastEntry"]
    return {**{k:row[k] for k in ("name","source","notice","checkedOn")}, "address":row.get("address"),
            "sourceLabel":row.get("sourceLabel","场馆官网"),
            "hours":None if uncertain else hours, "lastEntry":None if uncertain else entry,
            "usualClosure":weekday in row["closed"], "regularHoursUncertain":uncertain}


class DayFacts:
    def __init__(self, routes):
        self.routes = routes

    def place(self, city, kind, name, pos, on=None):
        if city not in CITYCODE or kind not in POI_TYPES or not name.strip() or len(name) > 110:
            raise ValueError("请选择行程中的地点。")
        official = official_for(city,name,on,pos)
        notice = PLACE_NOTICES.get((city,kind,name))
        map_params = {"keyword":name, "city":CITYCODE[city], "center":f"{pos[1]},{pos[0]}", "view":"list", "src":"ShortTrip"}
        base = {"source":"高德地点搜索", "sourceUrl":AMAP_POI, "official":official,
                "notice":notice, "mapUrl":"https://uri.amap.com/search?" + urlencode(map_params),
                "date":on.isoformat() if on else None, "retrievedAt":datetime.now(CHINA).isoformat(timespec="seconds")}
        if notice and notice.get("scheduleRequired"):
            return {**base, "status":"schedule_required"}
        if not self.routes.key:
            return {**base,"status":"unavailable","reason":"未配置高德"}
        site = LARGE_SITES.get((city,name), ()) if kind == "attraction" else ()
        if name in {"上海博物馆","重庆动物园"} and not official:
            site = ()
        alternatives = RESTAURANT_ALIASES.get((city,name), ()) if kind == "restaurant" else ()
        exact = {normalize(n) for n in (site or (name,))}
        aliases = {normalize(n) for n in alternatives}
        valid_names = exact | aliases
        try:
            data = self.routes._call("/v5/place/text", {"keywords":"|".join((name,*alternatives)),"region":CITYCODE[city],
                    "city_limit":"true","show_fields":"business","page_size":"12"}, timeout=6)
        except RouteError as exc:
            return {**base, "status":"unavailable", "reason":str(exc)}
        nearby = []
        for row in data.get("pois") or []:
            key = normalize(row["name"]) if isinstance(row.get("name"), str) else ""
            if (row.get("citycode") != CITYCODE[city] or key not in valid_names
                    or not str(row.get("typecode","")).startswith(POI_TYPES[kind])):
                continue
            try:
                lng, lat = map(float, row["location"].split(","))
                km = haversine(pos, (lat,lng))
            except (AttributeError, KeyError, ValueError, TypeError):
                continue
            alias = key in aliases
            limit = .10 if alias else 1.5 if site else .22 if kind == "restaurant" else .28
            if km <= limit:
                nearby.append((alias,km,row))
        candidates = [item for item in nearby if not item[0]] or nearby
        if len(candidates) != 1:
            return {**base, "status":"ambiguous" if candidates else "unmatched"}
        alias, distance, matched = candidates[0]
        business = matched.get("business")
        business = business if isinstance(business,dict) else {}
        map_params["keyword"] = matched["name"]
        return {**base, "status":"alias_match" if alias else "site_match" if distance > .28 else "matched",
                "mapUrl":"https://uri.amap.com/search?" + urlencode(map_params),
                "coordinateGapMeters":round(distance*1000), "name":matched["name"], "address":info_text(matched,"address"),
                "today":info_text(business,"opentime_today"), "week":info_text(business,"opentime_week"),
                "todayDate":datetime.now(CHINA).date().isoformat()}

    def weather(self, city, start, days):
        code = WEATHER_CODES.get(city)
        if not code or days not in range(1,15):
            raise ValueError("请选择旅行城市和日期。")
        today = datetime.now(CHINA).date()
        requested = [start + timedelta(days=i) for i in range(days)]
        base = {"source":"高德天气","sourceUrl":AMAP_WEATHER}
        if requested[-1] < today or start > today+timedelta(days=3):
            return {**base, "status":"outside_forecast", "days":[]}
        if not self.routes.key:
            return {**base,"status":"unavailable","reason":"未配置高德","days":[]}
        try:
            data = self.routes._call("/v3/weather/weatherInfo", {"city":code,"extensions":"all"}, timeout=6)
        except RouteError as exc:
            return {**base,"status":"unavailable","reason":str(exc),"days":[]}
        forecast = next((f for f in data.get("forecasts") or [] if f.get("adcode") == code), None)
        if not forecast:
            return {**base,"status":"unavailable","days":[]}
        casts = {cast.get("date"):cast for cast in forecast.get("casts") or []}
        rows = []
        for on in requested:
            cast = casts.get(on.isoformat(), {})
            day, night = info_text(cast,"dayweather"), info_text(cast,"nightweather")
            rows.append({"date":on.isoformat(),"day":day,"night":night,
                         "dayTemp":temperature(cast,"daytemp"), "nightTemp":temperature(cast,"nighttemp"),
                         "dayRain":bool(day and re.search("[雨雷]",day)), "nightRain":bool(night and re.search("[雨雷]",night))})
        return {**base,"status":"forecast","publishedAt":forecast.get("reporttime"),"days":rows}
