"""ShortTrip 行程助手后端（只用标准库）。

    python webui/backend/server.py --five-city /path/to/chinatravel-five-city
    # 浏览器打开 http://127.0.0.1:8000

- 同一个端口同时提供网页（webui/）和 /api/*，接口说明见 docs/webui-api.md
- 规划：调用 chinatravel-five-city 的 ConstraintPlannerV2（plan_worker.py 子进程），异步任务 + 轮询
- 路线：高德 v5 路径规划（驾车/步行/公交，带路况分段），磁盘缓存；没有 key 时退回估算
- 对话：转发到 OpenAI 兼容服务（默认本地 llama-server），流式返回
- 数据：webui/data/store.json（行程、版本、收藏）；webui/data/route_cache/（路线缓存）
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WEB = Path(__file__).resolve().parents[1]
ROOT = WEB.parent
sys.path.insert(0, str(WEB))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import export_plan  # noqa: E402  坐标补全、POI 导出
from amap_route import MODES, RouteService, find_key, haversine  # noqa: E402
from day_facts import DayFacts  # noqa: E402
from itinerary_edits import ItineraryEdits  # noqa: E402
from personalized import Personalized  # noqa: E402
from personalized_archive import load_archive  # noqa: E402
from road_reference import RoadReference  # noqa: E402

CITIES = ["北京", "上海", "南京", "苏州", "杭州", "深圳", "成都", "重庆", "广州", "武汉", "西安", "厦门"]
CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


# =============================================================================
# 自然语言 → ChinaTravel query（规则版；有需要可以换成 LLM 抽取）
# =============================================================================
def _n(s: str) -> int | None:
    if s is None:
        return None
    if s.isdigit():
        return int(s)
    return CN_NUM.get(s)


def parse_query(text: str, base: dict | None = None) -> dict:
    q = dict(base or {})
    t = text.replace("，", ",")
    found = [(t.find(c), c) for c in CITIES if c in t]
    found.sort()
    start = None
    m = re.search(r"(?:当前位置|从|我在|人在)\s*(" + "|".join(CITIES) + r")", t) or re.search(r"(" + "|".join(CITIES) + r")\s*出发", t)
    if m:
        start = m.group(1)
    m2 = re.search(r"(?:去|到|玩|游|前往)\s*(" + "|".join(CITIES) + r")", t)
    target = m2.group(1) if m2 else None
    others = [c for _, c in found if c not in (start, target)]
    if not target and others:
        target = others[-1] if start else (others[1] if len(others) > 1 else others[0])
    if not start and target and len(found) > 1:
        start = next((c for _, c in found if c != target), None)
    if start:
        q["start_city"] = start
    if target:
        q["target_city"] = target
    m = re.search(r"(\d+|[一两二三四五六七八九十])\s*(?:天|日)", t)
    if m and _n(m.group(1)):
        q["days"] = _n(m.group(1))
    m = re.search(r"(\d+|[一两二三四五六七八九十])\s*(?:个人|人|位)", t)
    if m and _n(m.group(1)):
        q["people_number"] = _n(m.group(1))
    elif re.search(r"一个人|独自|自己去", t):
        q["people_number"] = 1
    elif re.search(r"我和(朋友|对象|女朋友|男朋友|老婆|老公|爱人|同事)|我们俩|两个人|情侣", t):
        q["people_number"] = 2
    q.setdefault("days", 2)
    q.setdefault("people_number", 1)
    q.setdefault("start_city", "上海" if q.get("target_city") != "上海" else "北京")
    q["nature_language"] = (base or {}).get("nature_language", "")
    q["nature_language"] = (q["nature_language"] + ("\n补充要求：" if q["nature_language"] else "") + text).strip()
    q["hard_logic_py"] = [f"result=(day_count(plan)=={q['days']})", f"result=(people_count(plan)=={q['people_number']})"]
    return q


# =============================================================================
# 存储
# =============================================================================
class Store:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))
        else:
            self.data = {"groups": [], "favorites": []}
            self._seed()
            self.save()

    def _seed(self):
        sample = WEB / "samples"
        seeds = [(sample / "hangzhou_3d_plan.json", sample / "hangzhou_3d_query.json", "杭州3日游：西溪湿地+西湖全景+运河夜游"),
                 (sample / "hangzhou_3d_plan_v2.json", sample / "hangzhou_3d_query_v2.json", "杭州3日游：西溪湿地+西湖全景+运河夜游")]
        for plan, query, title in seeds:
            if plan.exists():
                self.add_version(export_plan.load_trip(plan, query, title, None), group_title=title)

    def save(self):
        with self.lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)

    def add_version(self, trip: dict, group_id: str | None = None, group_title: str | None = None) -> dict:
        with self.lock:
            g = next((x for x in self.data["groups"] if x["id"] == group_id or (group_title and x["title"] == group_title)), None)
            if g is None:
                g = {"id": trip["id"], "title": trip["title"], "versions": []}
                self.data["groups"].append(g)
            g["versions"].append(trip)
            self.save()
            return g

    def find(self, trip_id: str):
        for g in self.data["groups"]:
            for i, v in enumerate(g["versions"]):
                if v.get("id") == trip_id:
                    return g, i
        return None, None


def trip_items(trip: dict):
    """兼容视图模型（days/items）和原始行程（plan/itinerary）两种存储形态：yield (day, name, kind, pos, start, end)"""
    if "days" in trip:
        for d in trip["days"]:
            for x in d["items"]:
                yield d["day"], x.get("name"), x.get("kind"), x.get("pos"), x.get("start"), x.get("end")
    else:
        for d in trip["plan"]["itinerary"]:
            for a in d["activities"]:
                yield d["day"], a.get("_title"), a.get("_kind"), a.get("_pos"), a.get("start_time"), a.get("end_time")


def trip_city(trip: dict) -> str:
    return trip.get("city") or trip.get("plan", {}).get("target_city", "")


# =============================================================================
# 规划任务
# =============================================================================
class Jobs:
    STEPS = ["理解需求", "检索交通与酒店", "DFS 搜索行程", "硬约束校验", "补全坐标"]

    def __init__(self, app: "App"):
        self.app = app
        self.jobs: dict[str, dict] = {}

    def start(self, kind: str, text: str, base_trip: dict | None = None, group_id: str | None = None) -> str:
        jid = uuid.uuid4().hex[:12]
        self.jobs[jid] = {"status": "queued", "progress": {"step": 0, "steps": self.STEPS, "message": "排队中"}}
        threading.Thread(target=self._run, args=(jid, kind, text, base_trip, group_id), daemon=True).start()
        return jid

    def _set(self, jid, step, msg, **kw):
        j = self.jobs[jid]
        j["status"] = kw.pop("status", "running")
        j["progress"] = {"step": step, "steps": self.STEPS, "message": msg}
        j.update(kw)

    def _run(self, jid, kind, text, base_trip, group_id):
        try:
            self._set(jid, 0, "理解需求")
            base_q = (base_trip or {}).get("queryParams")
            q = parse_query(text, base_q)
            if not q.get("target_city"):
                raise ValueError("没识别出目的地，请说明要去哪个城市（目前支持：" + "、".join(CITIES) + "）")
            self._set(jid, 1, "检索交通与酒店")
            app = self.app
            if not app.five_city:
                raise RuntimeError("后端没有配置 --five-city，无法规划")
            self._set(jid, 2, "DFS 搜索行程")
            p = subprocess.run([app.python, str(Path(__file__).with_name("plan_worker.py")), "--five-city", str(app.five_city),
                                "--max-evaluations", str(app.max_eval)],
                               input=json.dumps(q, ensure_ascii=False), capture_output=True, text=True, timeout=app.plan_timeout,
                               cwd=str(app.five_city))
            line = (p.stdout.strip().splitlines() or [""])[-1]
            if not line.startswith("{"):
                raise RuntimeError("规划进程异常：" + (p.stderr.strip().splitlines() or ["无输出"])[-1][:300])
            res = json.loads(line)
            if not res.get("ok"):
                raise RuntimeError("规划器没有找到可行行程：" + str(res.get("reason")))
            self._set(jid, 3, "硬约束校验")
            rep = res.get("report") or {}
            self._set(jid, 4, "补全坐标")
            plan = export_plan.enrich(res["plan"])
            tid = uuid.uuid4().hex[:10]
            title = (base_trip or {}).get("title") or f"{q['target_city']}{q['days']}日游"
            trip = {
                "id": tid, "title": title, "version": tid, "query": text, "queryParams": q,
                "summary": export_plan.summarize(plan), "plan": plan,
                "thinking": (f"解析需求：{q['start_city']} → {q['target_city']}，{q['days']} 天，{q['people_number']} 人。\n"
                             f"规划器 chinatravel-five-city ConstraintPlannerV2，用时 {res.get('seconds')} 秒；"
                             f"硬约束 {rep.get('hard_pass')}/{rep.get('hard_total')} 通过，常识校验{'通过' if rep.get('commonsense_ok') else '未通过：' + '；'.join(map(str, rep.get('commonsense_violations') or []))}。"
                             + ("\n注意：规则规划器目前只识别城市、天数、人数的修改，其他偏好会记录在需求里但不一定生效。" if kind == "revise" else "")),
            }
            g = app.store.add_version(trip, group_id=group_id if kind == "revise" else None)
            trip["groupId"] = g["id"]
            self._set(jid, len(self.STEPS), "完成", status="done", result=trip)
        except subprocess.TimeoutExpired:
            self._set(jid, 2, "超时", status="failed", error=f"规划超过 {self.app.plan_timeout} 秒")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._set(jid, self.jobs[jid]["progress"]["step"], "失败", status="failed", error=str(e))


# =============================================================================
# 应用
# =============================================================================
class App:
    def __init__(self, a):
        self.demo_routes = a.demo_routes
        self.five_city = a.five_city.resolve() if a.five_city else None
        if self.five_city:
            for db in (self.five_city / "ChinaTravel/chinatravel/environment/database", self.five_city / "data/xian11_merged_sandbox"):
                if db.exists() and db not in export_plan.DB_ROOTS:
                    export_plan.DB_ROOTS.append(db)
        self.python = a.python
        self.max_eval = a.max_evaluations
        self.plan_timeout = a.plan_timeout
        self.llm = a.llm.rstrip("/")
        self.model = a.model
        data = WEB / "data"
        data.mkdir(exist_ok=True)
        self.store = None if self.demo_routes else Store(data / "store.json")
        key = find_key(self.five_city, a.amap_key)
        self.routes = RouteService(key, data / "route_cache")
        self.day_facts = DayFacts(self.routes)
        self.road_reference = RoadReference()
        self.jobs = Jobs(self)
        self._pois = {}
        self.snapshot_roots = a.snapshot_roots
        self.archive = load_archive(a.personalized_archive, a.snapshot_roots) if a.personalized_archive else None
        example = a.planner_root / "result" / "workbench_examples" / "shanghai-qwen"
        self.example = load_archive(example, a.snapshot_roots) if example.is_dir() else None
        self.personalized = (Personalized(a.personalized_port, a.planner_root, a.snapshot_roots)
                             if a.personalized_port else None)
        self.itinerary_edits = (ItineraryEdits(self.personalized, a.planner_root, a.snapshot_roots)
                                if self.personalized else None)
        self.planner_port = a.personalized_port
        print(f"[backend] five-city: {self.five_city or '未配置（不能新建规划）'}")
        print(f"[backend] 高德路线服务: {'已配置' if key else '未配置'}")

    def record_feedback(self, body):
        if not isinstance(body, dict):
            return 400, {"message": "评价内容无效。"}
        answer, text = body.get("decision"), body.get("text", "")
        if answer not in ("use", "minor_edit", "replan", "need_evidence") or not isinstance(text, str) or len(text) > 1000:
            return 400, {"message": "请选择采用意向，补充说明最多 1000 字。"}
        job, candidate, revision = (body.get(k) for k in ("job", "candidate", "revision"))
        if not isinstance(job, str) or not isinstance(candidate, str):
            return 400, {"message": "请选择要评价的方案。"}
        if revision:
            code, detail = self.itinerary_edits.dispatch("GET", "state", {"id": revision}, None)
        else:
            code, detail = self.personalized.job(job)
        group = detail.get("group") or {}
        selected = next((v for v in group.get("versions", []) if v["id"] == candidate), None)
        if (code != 200 or group.get("jobId") != job or not selected
                or not selected["archive"]["validation"]["passed"]
                or (revision and not selected.get("revision", {}).get("adopted"))):
            return 409, {"message": "未找到可评价的已核验方案，请重新打开行程。"}
        evidence = selected["archive"]
        target = self.personalized.results / "workbench_feedback"
        target.mkdir(parents=True, exist_ok=True)
        identifier = uuid.uuid4().hex
        record = {"id": identifier, "at": datetime.now(timezone.utc).isoformat(), "job": job,
                  "candidate": candidate, "revision": revision or None, "request": evidence["requestId"],
                  "request_revision": evidence["requestRevision"], "snapshot": evidence["snapshotId"],
                  "decision": answer, "text": text.strip()}
        with (target / (identifier + ".json")).open("x", encoding="utf-8") as file:
            json.dump(record, file, ensure_ascii=False, indent=2)
        return 201, {"id": identifier}

    def route(self, origin, destination, mode, city, reference=False, refresh=False, departure=None):
        if mode not in MODES:
            raise ValueError("出行方式无效。")
        result = self.routes.route(origin, destination, mode, city, refresh=refresh, departure=departure)
        if departure is not None or result.get("source") == "amap" or not reference:
            return result
        road = self.road_reference.route(origin, destination, mode)
        return road or {"ok": True, "source": "amap_embed", "mode": mode, "unavailable": True}

    def pois(self, city: str, commit="", profile="") -> dict:
        if city not in export_plan.CITY:
            raise ValueError("未知的城市。")
        key = (city, commit, profile)
        if key not in self._pois:
            database = None
            if commit or profile:
                if not re.fullmatch(r"[a-f0-9]{7,40}", commit) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", profile):
                    raise ValueError("地点数据版本无效。")
                database = self.snapshot_roots / commit[:7] / "sandbox_profiles" / profile / "database"
                if not database.is_dir():
                    raise ValueError("本机缺少这份行程的地点数据。")
            self._pois[key] = export_plan.city_pois(city, per_kind=None if database else 400, database=database)
        return self._pois[key]


def _latlng(s: str):
    lat, lng = map(float, s.split(","))
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise ValueError("起终点坐标无效。")
    return [lat, lng]


class Handler(SimpleHTTPRequestHandler):
    app: App = None  # type: ignore[assignment]

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(WEB), **kw)

    def log_message(self, fmt, *args):
        if "/api/" in (args[0] if args else ""):
            sys.stderr.write("[api] " + (fmt % args) + "\n")

    # ------------------------------------------------------------------ utils
    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _route(self, method):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        parts = [urllib.parse.unquote(p) for p in u.path.strip("/").split("/")]
        try:
            return self._api(method, parts[1:], q)
        except ConnectionError:
            return
        except (ValueError, KeyError) as e:
            return self._json({"error": f"{type(e).__name__}: {e}"}, 400)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def do_GET(self):
        if self.path.startswith("/api/"):
            return self._route("GET")
        if self.path.split("?")[0] == "/config.js":  # 让同源页面自动进入 http 模式
            config = ('window.SHORTTRIP_ROUTE_LOOKUP = true;\n' if self.app.demo_routes
                      else 'window.SHORTTRIP_API = "";\n')
            if self.app.archive:
                config += 'window.SHORTTRIP_ARCHIVE = true;\n'
            if self.app.personalized:
                config += 'window.SHORTTRIP_PERSONALIZED = true;\n'
            body = config.encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        return super().do_GET()

    def do_POST(self):
        return self._route("POST")

    def do_PUT(self):
        return self._route("PUT")

    # ------------------------------------------------------------------- API
    def _api(self, m, p, q):
        app, store = self.app, self.app.store
        if m == "GET" and p == ["route"]:
            return self._json(app.route(_latlng(q["from"]), _latlng(q["to"]), q.get("mode", "car"),
                                        q.get("city", ""), reference=app.demo_routes or q.get("reference") == "1",
                                        refresh=q.get("refresh") == "1", departure=q.get("departure")))
        if m == "GET" and p == ["archived-trip"] and app.archive:
            return self._json(app.archive)
        if m == "GET" and p == ["archived-trips"]:
            return self._json([group for group in (app.archive, app.example) if group])
        if m == "GET" and p == ["day-facts", "place"]:
            return self._json(app.day_facts.place(q["city"], q["kind"], q["name"], _latlng(q["pos"]),
                                                 date.fromisoformat(q["date"]) if q.get("date") else None))
        if m == "GET" and p == ["day-facts", "weather"]:
            return self._json(app.day_facts.weather(q["city"], date.fromisoformat(q["start"]), int(q["days"])))
        if m == "GET" and p == ["workbench-status"]:
            return self._json({"kind": "shorttrip-workbench", "planner_port": app.planner_port,
                               "archive": app.archive is not None})
        if m == "POST" and p == ["personalized", "feedback"] and app.personalized:
            code, value = app.record_feedback(self._body())
            return self._json(value, code)
        if app.personalized and len(p) == 2 and p[0] == "personalized":
            body = self._body() if m == "POST" else None
            code, value = app.personalized.dispatch(m, p[1], q, body)
            return self._json(value, code)
        if app.itinerary_edits and len(p) == 2 and p[0] == "itinerary-edits":
            body = self._body() if m == "POST" else None
            code, value = app.itinerary_edits.dispatch(m, p[1], q, body)
            return self._json(value, code)
        if m == "GET" and p == ["pois"]:
            items = app.pois(q["city"], q.get("commit", ""), q.get("profile", "")).get(q.get("kind", "attraction"), [])
            if q.get("bbox"):
                s, w, n, e = map(float, q["bbox"].split(","))
                items = [x for x in items if s <= x["pos"][0] <= n and w <= x["pos"][1] <= e]
            return self._json(items)
        if app.demo_routes:
            return self._json({"error": "当前本地入口提供路线查询和示例行程。"}, 404)
        if m == "GET" and p == ["health"]:
            return self._json({"ok": True, "fiveCity": str(app.five_city or ""), "amap": bool(app.routes.key), "amapCalls": app.routes.calls})

        if m == "GET" and p == ["trips"]:
            return self._json(store.data["groups"])
        if m == "PUT" and len(p) == 2 and p[0] == "trips":
            trip = self._body()
            g, i = store.find(p[1])
            if g is None:
                return self._json({"error": "trip not found"}, 404)
            old = g["versions"][i]
            trip.setdefault("queryParams", old.get("queryParams"))
            g["versions"][i] = trip
            g["title"] = trip.get("title", g["title"])
            store.save()
            return self._json(trip)

        if m == "POST" and p == ["plan"]:
            body = self._body()
            return self._json({"jobId": app.jobs.start("create", body.get("text", ""))})
        if m == "POST" and len(p) == 3 and p[0] == "trips" and p[2] == "revise":
            g, i = store.find(p[1])
            if g is None:
                return self._json({"error": "trip not found"}, 404)
            base = dict(g["versions"][i])
            if not base.get("queryParams"):
                pl = base.get("plan") or {}
                base["queryParams"] = parse_query(base.get("query") or "", {
                    "start_city": base.get("startCity") or pl.get("start_city"), "target_city": base.get("city") or pl.get("target_city"),
                    "days": len(base.get("days") or pl.get("itinerary") or []) or 2, "people_number": base.get("people") or pl.get("people_number") or 1,
                    "nature_language": base.get("query", "")})
            return self._json({"jobId": app.jobs.start("revise", self._body().get("instruction", ""), base, g["id"])})
        if m == "GET" and len(p) == 2 and p[0] == "jobs":
            j = app.jobs.jobs.get(p[1])
            return self._json(j if j else {"status": "failed", "error": "job not found"}, 200 if j else 404)

        if m == "POST" and p == ["routes"]:
            body = self._body()
            out = [app.routes.route(l["from"], l["to"], l.get("mode", "car"), body.get("city", "")) for l in body.get("legs", [])]
            return self._json(out)

        if m == "POST" and p == ["alternatives"]:
            body = self._body()
            item = body["item"]
            kind = {"restaurant": "restaurant", "hotel": "hotel"}.get(item.get("kind"), "attraction")
            pool = [x for x in app.pois(body["city"]).get(kind, []) if x["name"] not in (item.get("place"), item.get("name"))]
            if item.get("pos"):
                pool.sort(key=lambda x: haversine(item["pos"], x["pos"]))
            return self._json(pool[:8])
        if m == "POST" and p == ["optimize"]:
            items = self._body()["items"]
            return self._json(optimize(items))

        if m == "GET" and p == ["favorites"]:
            return self._json(store.data.get("favorites", []))
        if m == "PUT" and p == ["favorites"]:
            store.data["favorites"] = self._body()
            store.save()
            return self._json(store.data["favorites"])
        if m == "GET" and p == ["inspirations"]:
            by: dict[str, list] = {}
            for x in app.pois(q["city"]).get("attraction", []):
                by.setdefault(x["info"].get("类型") or "其他", []).append(x["name"])
            return self._json([{"title": f"{q['city']}·{k}", "desc": f"{len(v)} 个地点", "image": None, "pois": v[:6]}
                               for k, v in sorted(by.items(), key=lambda kv: -len(kv[1]))[:8]])
        if m == "POST" and p == ["feedback"]:
            with open(WEB / "data" / "feedback.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({**self._body(), "at": time.strftime("%F %T")}, ensure_ascii=False) + "\n")
            return self._json({"ok": True})

        if m == "POST" and p == ["chat"]:
            return self._chat(self._body())
        return self._json({"error": "not found"}, 404)

    # ------------------------------------------------------------------ chat
    def _chat(self, body):
        app = self.app
        g, i = app.store.find(body.get("tripId") or "")
        sys_prompt = "你是旅行规划助手，回答简短具体。"
        if g is not None:
            trip = g["versions"][i]
            lines: dict[int, list] = {}
            for day, name, kind, pos, s, e in trip_items(trip):
                lines.setdefault(day, []).append(f"{s or ''} {name}")
            sys_prompt += f"\n用户当前行程（{trip_city(trip)}）：\n" + "\n".join(f"第{d}天：" + "；".join(v) for d, v in sorted(lines.items()))
            sys_prompt += "\n提到行程里的地点时用 [[地点原名]] 标出。"
        payload = {"model": app.model, "stream": True, "temperature": 0.3, "max_tokens": 800,
                   "messages": [{"role": "system", "content": sys_prompt}] + body.get("messages", [])}
        req = urllib.request.Request(app.llm + "/v1/chat/completions", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            resp = urllib.request.urlopen(req, timeout=120)
        except (urllib.error.URLError, OSError) as e:
            return self._json({"text": f"对话模型没有连上（{app.llm}）：{e}。启动 llama-server 或用 --llm 指定地址。"})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        with resp:
            for raw in resp:
                line = raw.decode("utf-8", "ignore").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    delta = json.loads(data)["choices"][0]["delta"].get("content") or ""
                except (KeyError, IndexError, json.JSONDecodeError):
                    continue
                if delta:
                    self.wfile.write(f"data: {json.dumps({'delta': delta}, ensure_ascii=False)}\n\n".encode())
                    self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")


def optimize(items: list[dict]) -> list[dict]:
    """顺路优化：首尾的车站/机场/酒店不动，中间按最近邻重排。"""
    fixed = lambda x: x.get("kind") in ("station", "airport", "hotel")  # noqa: E731
    head, tail, mid = [], [], list(items)
    while mid and fixed(mid[0]):
        head.append(mid.pop(0))
    while mid and fixed(mid[-1]):
        tail.insert(0, mid.pop())
    with_pos = [x for x in mid if x.get("pos")]
    no_pos = [x for x in mid if not x.get("pos")]
    cur = head[-1]["pos"] if head and head[-1].get("pos") else None
    out = []
    while with_pos:
        k = min(range(len(with_pos)), key=lambda j: haversine(cur, with_pos[j]["pos"]) if cur else 0)
        x = with_pos.pop(k)
        out.append(x)
        cur = x["pos"]
    for x in out + no_pos:
        x["leg"] = None
    return head + out + no_pos + tail


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    default_fc = os.environ.get("FIVE_CITY_ROOT") or str(ROOT.parent / "chinatravel-five-city")
    ap.add_argument("--five-city", type=Path, default=Path(default_fc) if Path(default_fc).exists() else None,
                    help="chinatravel-five-city 仓库路径（默认 $FIVE_CITY_ROOT 或同级目录）")
    ap.add_argument("--python", default=sys.executable, help="跑规划器用的 Python（需装好 five-city 的 requirements）")
    ap.add_argument("--amap-key", default=None, help="高德 Web 服务 key（默认读 AMAP_KEY / .secrets/amap.key）")
    ap.add_argument("--llm", default=os.environ.get("SHORTTRIP_LLM", "http://127.0.0.1:18080"))
    ap.add_argument("--model", default=os.environ.get("SHORTTRIP_MODEL", "qwen3.5-4b-q8"))
    ap.add_argument("--max-evaluations", type=int, default=60)
    ap.add_argument("--plan-timeout", type=int, default=600)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--demo-routes", action="store_true", help="使用随包行程，单独启用地图查路")
    workspace = ROOT.parent.parent
    ap.add_argument("--personalized-archive", type=Path, help="已核验的个性化规划归档目录")
    ap.add_argument("--snapshot-roots", type=Path, default=workspace / "ShortTrip" / "artifacts" / "data",
                    help="归档所用的旅行数据快照目录的上级目录")
    ap.add_argument("--personalized-port", type=int, help="本机 TripSpec 规划器端口")
    ap.add_argument("--planner-root", type=Path, default=workspace / "combined version_personalized",
                    help="本机 TripSpec 规划器所在目录")
    a = ap.parse_args()
    Handler.app = App(a)
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"[backend] http://{a.host}:{a.port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
