"""高德 Web 服务 v5 路径规划：驾车 / 步行 / 公交（含地铁），返回给网页的统一结构。

- key 的查找顺序：参数 → 环境变量 AMAP_KEY → <five-city>/.secrets/amap.key → <five-city>/.env、本仓库 .env 里的 AMAP_KEY=
  （与 chinatravel-five-city 的 live_amap.py 约定一致）
- 坐标按 GCJ-02（ChinaTravel 数据库与高德瓦片一致）
- 未指定时段沿用既有缓存及离线估算。
- 指定公交接驳时刻时单独查询，缺少匹配路线或耗时返回待核实。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

# v5 公交需要 citycode
CITYCODE = {
    "北京": "010", "上海": "021", "天津": "022", "重庆": "023", "南京": "025", "武汉": "027", "成都": "028", "西安": "029",
    "广州": "020", "深圳": "0755", "杭州": "0571", "苏州": "0512", "厦门": "0592", "宁波": "0574", "长沙": "0731", "青岛": "0532",
}
TMC_COLOR = {"畅通": "#1db35b", "缓行": "#ffb400", "拥堵": "#e83b3b", "严重拥堵": "#a01a1a", "未知": "#3a77f2"}
MODES = ("car", "walk", "transit", "subway")


class RouteError(RuntimeError):
    pass


def find_key(five_city: Path | None = None, explicit: str | None = None) -> str:
    if explicit:
        return explicit.strip()
    if os.environ.get("AMAP_KEY"):
        return os.environ["AMAP_KEY"].strip()
    candidates = []
    if five_city:
        candidates += [five_city / ".secrets" / "amap.key", five_city / ".env"]
    candidates.append(Path(__file__).resolve().parents[2] / ".env")
    for p in candidates:
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if p.suffix == ".key":
            if text.strip():
                return text.strip()
            continue
        for line in text.splitlines():
            if line.strip().startswith("AMAP_KEY=") and line.split("=", 1)[1].strip():
                return line.split("=", 1)[1].strip()
    return ""


def haversine(a, b) -> float:
    r = math.radians
    dlat, dlon = r(b[0] - a[0]), r(b[1] - a[1])
    h = math.sin(dlat / 2) ** 2 + math.cos(r(a[0])) * math.cos(r(b[0])) * math.sin(dlon / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def _pl(s) -> list[list[float]]:
    """高德 "lng,lat;lng,lat" → [[lat,lng],...]"""
    if isinstance(s, dict):
        s = s.get("polyline")
    out = []
    for part in str(s or "").split(";"):
        if "," in part:
            lng, lat = part.split(",")[:2]
            out.append([round(float(lat), 6), round(float(lng), 6)])
    return out


def _num(v, scale=1.0, default=0.0) -> float:
    try:
        return float(v) / scale
    except (TypeError, ValueError):
        return default


def _concat(paths):
    out = []
    for p in paths:
        for pt in p:
            if not out or out[-1] != pt:
                out.append(pt)
    return out


def estimate(frm, to, mode: str, reason: str) -> dict:
    straight = haversine(frm, to)
    road = straight * 1.3
    base = {"ok": True, "source": "estimate", "reason": reason, "mode": mode, "straightKm": round(straight, 3),
            "path": [frm, to], "steps": [], "tmcs": [], "segments": []}
    if mode == "subway":
        return {**base, "path": [], "unavailable": True}
    if mode == "walk":
        return {**base, "km": round(road, 3), "minutes": max(1, round(road / 4.5 * 60)), "cost": 0}
    if mode == "car":
        return {**base, "km": round(road, 3), "minutes": round(road / 30 * 60) + 3, "cost": 13 if road < 3 else round(13 + (road - 3) * 2.5)}
    if road < 1.2:
        return {**base, "unavailable": True}
    return {**base, "km": round(road, 3), "minutes": round(road / 18 * 60) + 10, "cost": 2 if road < 10 else 4}


class RouteService:
    def __init__(self, key: str, cache_dir: Path, *, min_interval: float = 0.12):
        self.key = key
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.min_interval = min_interval
        self._last = 0.0
        self.calls = 0

    # ---------------------------------------------------------------- public
    def route(self, frm, to, mode: str, city: str = "", *, refresh: bool = False, departure=None) -> dict:
        """frm/to: [lat, lng]。返回见 docs/webui-api.md 的 Route。"""
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        frm = [round(float(frm[0]), 6), round(float(frm[1]), 6)]
        to = [round(float(to[0]), 6), round(float(to[1]), 6)]
        if departure is not None:
            if mode not in ("transit", "subway"):
                raise ValueError("计划出发时刻仅用于公交和地铁。")
            when = datetime.fromisoformat(departure)
            if when.isoformat(timespec="minutes") != departure or when.tzinfo is not None:
                raise ValueError("请使用当地日期和时刻 YYYY-MM-DDTHH:MM。")
            # This observation belongs to one departure. Keep it out of the undated route cache.
            unavailable = {"mode": mode, "requestedDeparture": departure, "unavailable": True}
            if not self.key:
                return {**unavailable, "source": "unavailable", "reason": "未配置高德 Web 服务 Key"}
            try:
                result = self._transit(frm, to, city, subway=mode == "subway", departure=when)
            except RouteError as exc:
                return {**unavailable, "source": "unavailable", "reason": str(exc)}
            if result.get("unavailable") or not result.get("durationSeconds"):
                return {**unavailable, "source": "amap", "reason": "no_timed_option",
                        "retrievedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
            return {**result, "ok": True, "mode": mode, "source": "amap", "requestedDeparture": departure,
                    "retrievedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        if not self.key:
            return estimate(frm, to, mode, "未配置高德 key（设置 AMAP_KEY 或 <five-city>/.secrets/amap.key）")
        ck = hashlib.sha1(json.dumps([mode, frm, to, CITYCODE.get(city, "")]).encode()).hexdigest()
        cache = self.cache_dir / f"{ck}.json"
        cache_seconds = 10 if refresh else 3600 if mode == "walk" else 60
        try:
            if time.time() - cache.stat().st_mtime <= cache_seconds:
                return json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        try:
            if mode == "car":
                res = self._driving(frm, to)
            elif mode == "walk":
                res = self._walking(frm, to)
            else:
                res = self._transit(frm, to, city, subway=mode == "subway")
        except RouteError as e:
            return estimate(frm, to, mode, f"高德路径规划失败：{e}")
        res.update({"ok": True, "source": "amap", "mode": mode, "straightKm": round(haversine(frm, to), 3),
                    "retrievedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
        cache.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
        return res

    # --------------------------------------------------------------- private
    def _call(self, path: str, params: dict, *, timeout=15) -> dict:
        with self.lock:  # 高德个人 key 有 QPS 限制，串行 + 最小间隔
            wait = self.min_interval - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            self.calls += 1
        url = "https://restapi.amap.com" + path + "?" + urllib.parse.urlencode({**params, "key": self.key, "output": "JSON"})
        req = urllib.request.Request(url, headers={"User-Agent": "ShortTripWebUI/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            raise RouteError(type(e).__name__) from None
        if str(data.get("status")) != "1":
            raise RouteError(f"{data.get('info')}({data.get('infocode')})")
        return data

    @staticmethod
    def _ll(p):
        return f"{p[1]:.6f},{p[0]:.6f}"

    def _driving(self, frm, to) -> dict:
        d = self._call("/v5/direction/driving", {"origin": self._ll(frm), "destination": self._ll(to),
                                                 "strategy": "32", "show_fields": "cost,tmcs,polyline"})
        route = d.get("route") or {}
        paths = route.get("paths") or []
        if not paths:
            raise RouteError("no_route")
        p = paths[0]
        steps, tmcs = [], []
        for s in p.get("steps") or []:
            sp = _pl(s.get("polyline"))
            steps.append({"instruction": s.get("instruction", ""), "road": s.get("road_name", ""),
                          "km": round(_num(s.get("step_distance"), 1000), 3),
                          "minutes": round(_num((s.get("cost") or {}).get("duration"), 60), 1), "path": sp})
            for t in s.get("tmcs") or []:
                status = t.get("tmc_status") or "未知"
                tmcs.append({"status": status, "color": TMC_COLOR.get(status, TMC_COLOR["未知"]), "path": _pl(t.get("tmc_polyline"))})
        cost = p.get("cost") or {}
        return {"km": round(_num(p.get("distance"), 1000), 3), "minutes": max(1, round(_num(cost.get("duration"), 60))),
                "cost": _num(route.get("taxi_cost"), default=0) or None, "tolls": _num(cost.get("tolls")),
                "lights": int(_num(cost.get("traffic_lights"))), "path": _concat(s["path"] for s in steps),
                "steps": steps, "tmcs": tmcs, "segments": []}

    def _walking(self, frm, to) -> dict:
        d = self._call("/v5/direction/walking", {"origin": self._ll(frm), "destination": self._ll(to), "show_fields": "cost,polyline"})
        paths = (d.get("route") or {}).get("paths") or []
        if not paths:
            raise RouteError("no_route")
        p = paths[0]
        steps = [{"instruction": s.get("instruction", ""), "road": s.get("road_name", ""),
                  "km": round(_num(s.get("step_distance"), 1000), 3),
                  "minutes": round(_num((s.get("cost") or {}).get("duration"), 60), 1), "path": _pl(s.get("polyline"))}
                 for s in p.get("steps") or []]
        return {"km": round(_num(p.get("distance"), 1000), 3), "minutes": max(1, round(_num((p.get("cost") or {}).get("duration"), 60))),
                "cost": 0, "path": _concat(s["path"] for s in steps), "steps": steps, "tmcs": [], "segments": []}

    def _transit(self, frm, to, city: str, *, subway: bool = False, departure=None) -> dict:
        code = CITYCODE.get(city, "")
        if not code:
            raise RouteError(f"未知城市 citycode：{city}")
        params = {"origin": self._ll(frm), "destination": self._ll(to),
                  "city1": code, "city2": code, "strategy": "7" if subway else "0",
                  "show_fields": "cost,polyline", "AlternativeRoute": "3" if subway else "1"}
        if departure is not None:
            params.update(date=departure.date().isoformat(), time=departure.strftime("%H-%M"))
        d = self._call("/v5/direction/transit/integrated", params)
        transits = (d.get("route") or {}).get("transits") or []
        for transit in transits:
            result = self._parse_transit(transit)
            if not subway or any(s["type"] == "subway" for s in result["segments"]):
                return result
        return {"unavailable": True, "km": None, "minutes": None, "cost": None, "path": [], "steps": [], "tmcs": [], "segments": []}

    @staticmethod
    def _parse_transit(t) -> dict:
        segs = []
        for s in t.get("segments") or []:
            w = s.get("walking") or {}
            if _num(w.get("distance")) > 0:
                wp = _concat(_pl(st.get("polyline")) for st in w.get("steps") or [])
                segs.append({"type": "walk", "name": "步行", "km": round(_num(w.get("distance"), 1000), 3),
                             "minutes": round(_num((w.get("cost") or {}).get("duration"), 60)), "path": wp,
                             "instruction": "；".join(st.get("instruction", "") for st in w.get("steps") or [] if st.get("instruction"))})
            for bl in ((s.get("bus") or {}).get("buslines") or [])[:1]:
                kind = "subway" if ("地铁" in str(bl.get("type")) or "subway" in str(bl.get("type")).lower()) else "bus"
                segs.append({"type": kind, "name": bl.get("name", ""), "from": (bl.get("departure_stop") or {}).get("name", ""),
                             "to": (bl.get("arrival_stop") or {}).get("name", ""), "stops": int(_num(bl.get("via_num"))) + 1,
                             "km": round(_num(bl.get("distance"), 1000), 3), "minutes": round(_num((bl.get("cost") or {}).get("duration"), 60)),
                             "path": _pl(bl.get("polyline"))})
            rw = s.get("railway") or {}
            if rw.get("name"):
                segs.append({"type": "railway", "name": rw.get("name"), "from": (rw.get("departure_stop") or {}).get("name", ""),
                             "to": (rw.get("arrival_stop") or {}).get("name", ""), "km": round(_num(rw.get("distance"), 1000), 3),
                             "minutes": round(_num(rw.get("time"), 60)), "path": []})
        cost = t.get("cost") or {}
        minutes = _num(cost.get("duration") or t.get("duration"), 60)
        return {"km": round(_num(t.get("distance"), 1000), 3), "minutes": round(minutes) if minutes > 0 else None,
                "durationSeconds": round(minutes * 60) if minutes > 0 else None,
                "cost": _num(cost.get("transit_fee"), default=0), "walkKm": round(_num(t.get("walking_distance"), 1000), 3),
                "path": _concat(s["path"] for s in segs), "steps": [], "tmcs": [], "segments": segs}
