"""On-demand OSRM road reference for GCJ-02 endpoints on the demo map."""

from collections import OrderedDict
import json
import math
from threading import Lock
from datetime import datetime, timezone
from time import monotonic, sleep
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def gcj_offset(lat, lng):
    x, y = lng - 105, lat - 35
    pi, sin = math.pi, math.sin
    lat_delta = -100 + 2*x + 3*y + .2*y*y + .1*x*y + .2*math.sqrt(abs(x))
    lng_delta = 300 + x + 2*y + .1*x*x + .1*x*y + .1*math.sqrt(abs(x))
    common = (20*sin(6*x*pi) + 20*sin(2*x*pi)) * 2/3
    lat_delta += common + (20*sin(y*pi) + 40*sin(y*pi/3))*2/3 + (160*sin(y*pi/12) + 320*sin(y*pi/30))*2/3
    lng_delta += common + (20*sin(x*pi) + 40*sin(x*pi/3))*2/3 + (150*sin(x*pi/12) + 300*sin(x*pi/30))*2/3
    latitude = math.radians(lat)
    magic = 1 - .00669342162296594323*sin(latitude)**2
    radius = 6378245 / math.sqrt(magic)
    return (math.degrees(lat_delta*magic / (radius*(1-.00669342162296594323))),
            math.degrees(lng_delta / (radius*math.cos(latitude))))


def to_wgs84(point):
    lat, lng = point
    for _ in range(4):
        dlat, dlng = gcj_offset(lat, lng)
        lat, lng = point[0] - dlat, point[1] - dlng
    return lat, lng


def to_gcj02(point):
    lng, lat = point
    dlat, dlng = gcj_offset(lat, lng)
    return [round(lat + dlat, 6), round(lng + dlng, 6)]


class RoadReference:
    def __init__(self):
        self.cache = OrderedDict()
        self.lock = Lock()
        self.last_request = 0.0

    def route(self, origin, destination, mode):
        if mode not in {"walk", "car"} or origin == destination:
            return None
        if not all(18 <= lat <= 54 and 73 <= lng <= 135 for lat, lng in (origin, destination)):
            return None
        key = (mode, tuple(origin), tuple(destination))
        if not self.lock.acquire(timeout=9):
            return None
        try:
            cached = self.cache.get(key)
            if cached and monotonic() - cached[0] <= 3600:
                self.cache.move_to_end(key)
                return cached[1]
            sleep(max(0, 1 - (monotonic() - self.last_request)))
            self.last_request = monotonic()
            result = self.fetch(origin, destination, mode)
            if result:
                self.cache[key] = (monotonic(), result)
                self.cache.move_to_end(key)
                if len(self.cache) > 256:
                    self.cache.popitem(last=False)
            return result
        finally:
            self.lock.release()

    @staticmethod
    def fetch(origin, destination, mode):
        profile = "foot" if mode == "walk" else "car"
        coords = ";".join(f"{lng:.6f},{lat:.6f}" for lat, lng in map(to_wgs84, (origin, destination)))
        query = urlencode({"geometries": "geojson", "overview": "simplified", "steps": "false", "radiuses": "200;200"})
        url = f"https://routing.openstreetmap.de/routed-{profile}/route/v1/driving/{coords}?{query}"
        request = Request(url, headers={"User-Agent": "ShortTrip-WebUI/1.0 (interactive route reference)"})
        try:
            with urlopen(request, timeout=7) as response:
                result = json.loads(response.read(512_001))
            route = result["routes"][0]
            km, seconds = route["distance"] / 1000, route["duration"]
            path = [to_gcj02(point) for point in route["geometry"]["coordinates"]]
            if not math.isfinite(km) or not math.isfinite(seconds) or km <= 0 or seconds <= 0 or len(path) < 2:
                return None
            return {"ok": True, "source": "osrm", "mode": mode, "km": round(km, 3),
                    "retrievedAt": datetime.now(timezone.utc).isoformat(),
                    "minutes": math.ceil(seconds / 60), "path": path}
        except (OSError, ValueError, IndexError, KeyError, TypeError):
            return None
