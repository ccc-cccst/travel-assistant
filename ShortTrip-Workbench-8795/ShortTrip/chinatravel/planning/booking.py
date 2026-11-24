"""Verified public booking and visitor pages. Snapshot prices remain estimates."""

from datetime import date, timedelta
from urllib.parse import urlencode


SHANGHAI = {
    "上海美丽园大酒店": ("携程酒店预订", "https://hotels.ctrip.com/hotels/345052.html"),
    "刘海粟美术馆": ("美术馆参观服务", "https://lhs-arts.org/tour/serviceInfo.html"),
    "静安雕塑公园": ("上海市政府公园信息", "https://www.shanghai.gov.cn/jingan/index.html"),
    "上生新所": ("长宁区政府参观信息", "https://www.shcn.gov.cn/col6991/20250303/1279330.html"),
    "上海话剧艺术中心": ("上海市文旅局剧场名录", "https://whlyj.sh.gov.cn/jyjc/20250924/65721fbdbbf0423fa4fdec4a7c86e1c6.html"),
    "人民广场": ("上海市政府景点介绍", "https://english.shanghai.gov.cn/en-HeritageZones/20240409/4924daf618c549e587f556f5f544c3a4.html"),
    "愚园路历史风貌保护区": ("长宁区政府游览指南", "https://www.shcn.gov.cn/col6991/20240510/1259316.html"),
    "上海博物馆": ("上海博物馆人民广场馆参观", "https://www.shanghaimuseum.cn/mu/frontend/pg/service/visit-west"),
    "上海博物馆(东馆)": ("上海博物馆东馆参观", "https://www.shanghaimuseum.cn/mu/frontend/pg/service/visit-east"),
}


def booking_info(city, activity, day, people, departure_date=None):
    place = activity["position"]
    is_hotel = activity["type"] == "accommodation"
    scheduled = (date.fromisoformat(departure_date) + timedelta(days=day - 1)).isoformat() if departure_date else None
    verified = SHANGHAI.get(place) if city == "上海" else None
    entry, url = verified or (
        ("携程酒店搜索", "https://hotels.ctrip.com/") if is_hotel else
        ("上海文旅景区名录", "https://www.meet-in-shanghai.net/cn/a-grade-tourist-attraction/") if city == "上海" else
        ("在高德地图核对地点", "https://uri.amap.com/search?" + urlencode({"keyword": place, "city": city}))
    )
    # Only public entry points are integrated. There is no verified inventory
    # or date/party-specific response to promote to a live quote.
    price = activity.get("cost")
    status = "reference" if isinstance(price, (int, float)) else "pending"
    return {"entry": entry, "url": url, "venue_matched": verified is not None,
            "quote": {"status": status, "amount": price, "date": scheduled, "people": people}}

