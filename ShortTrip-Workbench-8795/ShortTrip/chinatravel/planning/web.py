"""Local trip workbench and compatibility endpoint for the bounded planner."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import date, timedelta
import json
import math
from pathlib import Path
from threading import Lock, Thread
from time import monotonic
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from .preferences import PRESETS, evaluate_preferences
from .booking import booking_info
from .edits import EditConflict, activity_at, contains_locks, edit_visit, lock_key
from .saved_trips import SavedTrips
from .validation import validate_plan


STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/workbench.css": ("workbench.css", "text/css; charset=utf-8"),
    "/workbench.js": ("workbench.js", "text/javascript; charset=utf-8"),
    "/shanghai.webp": ("shanghai.webp", "image/webp"),
}


def option_view(candidate, prepared, env, departure_date=None, locks=None):
    plan = candidate["plan"]
    city = prepared["request"]["target_city"]
    points = env.poi.data.get(city, {})

    def position(activity):
        if activity.get("type") in ("train", "airplane"):
            return activity["end"] if city in activity["end"] or activity["end"] in points else activity["start"]
        return activity.get("position")

    locations, bookings = {}, {}
    for day in plan["itinerary"]:
        for index, activity in enumerate(day["activities"]):
            if activity["type"] in ("attraction", "accommodation"):
                bookings[f"{day['day']}:{index}"] = booking_info(
                    city, activity, day["day"], plan["people_number"], departure_date)
            name = position(activity)
            if name not in points:
                continue
            lat, lon = map(float, points[name])
            if math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180:
                locations[name] = [lat, lon]

    score = candidate["personalization"]
    if locks is None:
        locks = [{key: item[key] for key in ("day", "place", "start_time", "end_time")}
                 for item in prepared["request"].get("appointments", ()) if item.get("confirmed")]
    return {
        "plan": plan,
        "locations": locations,
        "bookings": bookings,
        "reservations": list(locks),
        "metrics": score["metrics"],
        "preferences": {"missing": score["missing_preferences"], "evidence": score["evidence"]},
    }


def serve(service, port=8766, seconds=15):
    runtime = Lock()
    state = Lock()
    confirmations, jobs, job_specs = {}, {}, {}
    trips = SavedTrips()

    def remember(records, key, value):
        records[key] = value
        while len(records) > 32:
            del records[next(iter(records))]

    def trip_view(document):
        return {key: document[key] for key in ("id", "revision", "title", "departure_date", "option", "updated")}

    def update_trip(query):
        action = query.get("action")
        if action not in ("save", "edit", "lock", "restore"):
            raise EditConflict("invalid_action", "请选择需要的行程操作。")
        if "trip_id" in query:
            if not isinstance(query["trip_id"], str):
                raise EditConflict("missing_trip", "找不到这份本机行程。")
            document = trips.get(query["trip_id"])
            if type(query.get("revision")) is not int or query["revision"] != document["revision"]:
                raise EditConflict("stale_revision", "行程已在其他窗口更新，请重新打开后再编辑。")
        else:
            job_id, index = query.get("job_id"), query.get("option_index")
            with state:
                job = jobs.get(job_id) if isinstance(job_id, str) else None
                spec = job_specs.get(job_id) if isinstance(job_id, str) else None
            if not job or job["status"] != "success" or not spec or type(index) is not int or not 0 <= index < len(job["options"]):
                raise EditConflict("missing_trip", "请等本轮规划完成，或重新打开已保存的行程。")
            option = job["options"][index]
            plan = option["plan"]
            title = f"{spec['start_city']} → {spec['target_city']} · {spec['days']} 天 · {spec['people_number']} 人"
            document = {"title": title, "request": spec, "option": option, "original": plan,
                        "restore_point": plan, "departure_date": None, "locks": list(option["reservations"])}
        plan, spec, locks = document["option"]["plan"], document["request"], document["locks"]
        departure = query.get("departure_date", document["departure_date"])
        if departure:
            try:
                departure = date.fromisoformat(departure).isoformat()
                date.fromisoformat(departure) + timedelta(days=spec["days"] - 1)
            except (ValueError, TypeError, OverflowError):
                raise EditConflict("invalid_date", "请填写有效的出发日期。") from None
        else:
            departure = None
        if action == "edit":
            plan = edit_visit(plan, spec, service.env, locks, query.get("day"), query.get("index"),
                              query.get("place"), query.get("start_time"), query.get("end_time"), seconds)
        elif action == "lock":
            day = query.get("day")
            item = lock_key(day, activity_at(plan, day, query.get("index")))
            if item not in locks:
                locks = [*locks, item]
                if not contains_locks(document["restore_point"], locks):
                    document["restore_point"] = plan
        elif action == "restore":
            plan = document["original"] if contains_locks(document["original"], locks) else document["restore_point"]
            check = validate_plan(plan, spec, service.env)
            if not contains_locks(plan, locks) or not check["passed"]:
                raise EditConflict("restore_conflict", "恢复版本与已确认预约或当前数据冲突。")
        score = evaluate_preferences(plan, spec["profile"], service.env, spec)
        document.update(locks=locks, departure_date=departure, option=option_view(
            {"plan": plan, "personalization": score}, {"request": spec}, service.env, departure, locks))
        return trip_view(trips.save(document))

    def run_job(job, prepared):
        started = job["started"]

        def update(event):
            partial = event.get("partial")
            option = option_view(partial, prepared, service.env) if partial else None
            with state:
                job["stage"] = event["stage"]
                job["events"].append({"stage": event["stage"], "seconds": round(monotonic() - started, 2)})
                if option is not None:
                    job["options"] = [option]
                    job["first_valid_seconds"] = event["first_valid_seconds"]
                if "candidate_count" in event:
                    job["candidate_count"] = event["candidate_count"]

        try:
            result = service.build(prepared, max_seconds=seconds, on_event=update)
            options = ([option_view(result, prepared, service.env)] +
                       [option_view(x, prepared, service.env) for x in result["alternatives"]]
                       if result["status"] == "success" else [])
            with state:
                if options:
                    job["options"] = options
                job.update(status=result["status"], stage="finished", issues=result["issues"],
                           metrics=result["metrics"], comparison_note=result.get("comparison_note"),
                           valid_count=result.get("valid_count", 0), finished=monotonic())
                job["events"].append({"stage": "finished", "seconds": round(job["finished"] - started, 2)})
        except Exception:
            with state:
                job.update(status="planning_error", stage="finished", finished=monotonic(),
                           issues=[{"message": "规划服务发生异常，请重试。"}])
        finally:
            runtime.release()

    class Handler(BaseHTTPRequestHandler):
        def send(self, value, status=200, content_type="application/json; charset=utf-8"):
            data = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            url = urlparse(self.path)
            if url.path in STATIC:
                name, content_type = STATIC[url.path]
                path = Path(__file__).with_name(name)
                if not path.is_file():
                    self.send({"error": "页面不存在"}, 404)
                    return
                self.send(path.read_bytes(), content_type=content_type)
            elif url.path == "/options":
                city = parse_qs(url.query).get("city", ["上海"])[0]
                if city not in service.env.support_cities:
                    self.send({"error": "请选择支持的目的地"}, 400)
                    return

                def values(table, column):
                    return sorted({str(x) for x in table[column].dropna().unique() if str(x).strip()})

                self.send({"cities": service.env.support_cities, "presets": PRESETS,
                           "interest_types": values(service.env.attractions.data[city], "type"),
                           "restaurant_types": values(service.env.restaurants.data[city], "cuisine"),
                           "attractions": values(service.env.attractions.data[city], "name")})
            elif url.path.startswith("/jobs/"):
                with state:
                    job = jobs.get(url.path.removeprefix("/jobs/"))
                    snapshot = None if job is None else {
                        **job, "events": list(job["events"]),
                        "elapsed_seconds": round((job.get("finished") or monotonic()) - job["started"], 2),
                    }
                if snapshot is None:
                    self.send({"status": "missing_job", "error": "本轮进度已失效"}, 404)
                else:
                    snapshot.pop("started")
                    snapshot.pop("finished", None)
                    self.send(snapshot)
            elif url.path == "/itineraries":
                self.send({"trips": trips.list()})
            elif url.path.startswith("/itineraries/"):
                try:
                    self.send(trip_view(trips.get(url.path.removeprefix("/itineraries/"))))
                except EditConflict as error:
                    self.send({"status": error.code, "error": str(error)}, 404)
            else:
                self.send({"error": "页面不存在"}, 404)

        def do_POST(self):
            if self.path not in {"/plan", "/interpret", "/jobs", "/itineraries"}:
                self.send({"error": "接口不存在"}, 404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 100_000:
                    raise ValueError("请求为空或过长")
                query = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(query, dict):
                    raise ValueError("需求应为 JSON 对象")
            except (ValueError, UnicodeError) as error:
                self.send({"status": "invalid_request", "issues": [{"message": str(error)}]}, 400)
                return
            if self.path == "/jobs":
                with state:
                    token = query.get("confirmation_id")
                    prepared = confirmations.get(token) if isinstance(token, str) else None
                if prepared is None:
                    self.send({"status": "missing_confirmation", "error": "需求确认已失效，请重新检查需求"}, 409)
                    return
            if not runtime.acquire(blocking=False):
                self.send({"status": "busy", "issues": [{"message": "规划服务正在处理另一条需求，请稍后重试"}]}, 409)
                return
            if self.path == "/jobs":
                job_id = uuid4().hex
                job = {"id": job_id, "status": "running", "stage": "queued", "started": monotonic(),
                       "budget_seconds": seconds, "issues": [], "events": [], "options": []}
                with state:
                    remember(jobs, job_id, job)
                    remember(job_specs, job_id, prepared["request"])
                try:
                    Thread(target=run_job, args=(job, prepared), daemon=True).start()
                except RuntimeError:
                    runtime.release()
                    with state:
                        jobs.pop(job_id)
                        job_specs.pop(job_id)
                    self.send({"status": "planning_error", "error": "无法启动规划，请重试"}, 503)
                    return
                self.send({"id": job_id, "budget_seconds": seconds}, 202)
                return
            try:
                if self.path == "/itineraries":
                    try:
                        self.send(update_trip(query))
                    except EditConflict as error:
                        self.send({"status": error.code, "error": str(error)}, 409)
                elif self.path == "/plan":
                    self.send(service.generate(query, max_seconds=seconds))
                else:
                    result = service.prepare(query, max_seconds=seconds)
                    if result["status"] == "ready":
                        token = uuid4().hex
                        with state:
                            remember(confirmations, token, result)
                        result = {**result, "confirmation_id": token}
                    self.send(result)
            finally:
                runtime.release()

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"ShortTrip http://127.0.0.1:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

