"""Run planner-owned edit trials and store adopted immutable versions."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from threading import Lock, Thread
import time
from uuid import uuid4

from export_plan import CityIndex
from personalized import IDENTIFIER
from personalized_archive import load_archive
from amap_route import haversine
from edit_worker import EditWorker

CANDIDATE_ID = re.compile(r"[a-f0-9]{24}")
CLOCK = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]|24:00")
PLACE_KINDS = {"attraction": "attraction", "accommodation": "hotel",
               "breakfast": "restaurant", "lunch": "restaurant", "dinner": "restaurant"}


def read(path):
    for attempt in range(6):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except PermissionError:
            # Windows may briefly refuse a read at the instant a worker replaces STATUS.json.
            if os.name != "nt" or attempt == 5:
                raise
            time.sleep(.025 * (attempt + 1))


def write(path, value):
    temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class ItineraryEdits:
    def __init__(self, planner, root, snapshots):
        self.planner = planner
        self.root = root.resolve()
        self.store = self.root / "result" / "workbench_edits"
        self.snapshots = snapshots.resolve()
        self._adopt_lock = Lock()
        executables = [folder / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
                       for folder in (self.root, self.root.parent / "ShortTrip")]
        self.python = next((str(p) for p in executables if p.is_file()), sys.executable)
        self.worker = EditWorker(self.python, self.root)

    def _folder(self, identifier):
        if isinstance(identifier, str) and IDENTIFIER.fullmatch(identifier):
            folder = self.store / identifier
            if (folder / "input.json").is_file():
                return folder
        raise ValueError("找不到这份修改记录。")

    def _base(self, value):
        job, candidate, parent = (value.get(key) for key in ("job", "candidate", "parent"))
        if not isinstance(job, str) or not IDENTIFIER.fullmatch(job):
            raise ValueError("请选择一个真实规划任务。")
        if not isinstance(candidate, str) or not CANDIDATE_ID.fullmatch(candidate):
            raise ValueError("请选择一份已核验方案。")
        if parent:
            folder = self._folder(parent)
            if not (folder / "ADOPTED.json").is_file():
                raise ValueError("请先采用该修改，再继续调整。")
            base = read(folder / "input.json")
            group = self._project(folder, base, adopted=True)
            if base["job"] != job or group["recommendedId"] != candidate:
                raise ValueError("当前版本和待修改候选不一致。")
            return folder / "run", group["versions"][0], base["original"]
        code, status = self.planner.job(job)
        if code != 200:
            raise ValueError(status.get("message", "无法读取原规划。"))
        if status["state"] != "complete" or not status.get("group"):
            raise ValueError("请等待最终核验完成，再修改行程。")
        selected = next((v for v in status["group"]["versions"] if v["id"] == candidate), None)
        if selected is None:
            raise ValueError("未找到已发布的候选方案。")
        source = self.planner.results / "manual" / job
        if status.get("delivery"):
            source /= status["delivery"]
        return source, selected, candidate

    def _current(self, entry):
        code, current = self.planner.request("/tripspec/state", query={"id": entry["request"]})
        return code == 200 and current["request_revision"] == entry["revision"]

    def _operation(self, value, selected):
        action = value.get("action")
        if action == "batch":
            return self._batch_operation(value, selected)
        if action == "hotel":
            return {"action": action}
        plan = selected["plan"]
        if action in ("optimize", "insert_visit"):
            day = value.get("day")
            if type(day) is not int or not any(entry["day"] == day for entry in plan["itinerary"]):
                raise ValueError("请选择行程中的一天。")
            if action == "insert_visit":
                place, period, duration = (value.get(k) for k in ("place", "period", "duration"))
                if (not isinstance(place, str) or not 0 < len(place.strip()) <= 120
                        or period not in ("morning", "afternoon", "evening")
                        or type(duration) is not int or not 15 <= duration <= 240):
                    raise ValueError("请选择具体景点、游览时段和 15–240 分钟的停留时间。")
                return {"action": action, "day": day, "place": place.strip(), "period": period, "duration": duration}
            return {"action": action, "day": day}
        activity_id = value.get("activity")
        for day in plan["itinerary"]:
            for slot, activity in enumerate(day["activities"], 1):
                if activity["_uid"] == activity_id:
                    return self._fields(value, day["day"], slot, activity)
        raise ValueError("找不到要修改的活动，行程可能已切换。")

    def _batch_operation(self, value, selected):
        raw = value.get("operations")
        pace = value.get("pace")
        period = value.get("pace_period")
        day = value.get("day")
        if (value.get("schema_version") != 1 or type(day) is not int
                or pace not in (None, "lighter") or period not in (None, "morning", "afternoon", "evening")
                or (period is not None and pace is None) or not isinstance(raw, list)
                or not (1 if pace else 2) <= len(raw) <= 3):
            raise ValueError("请选择同一天的两至三项关联调整，或一项调整加放松节奏。")
        operations = []
        for entry in raw:
            if not isinstance(entry, dict) or entry.get("action") not in (
                    "time", "replace", "transport", "meal_options", "remove_visit"):
                raise ValueError("这组修改包含暂不支持的操作。")
            operation = self._operation(entry, selected)
            if operation["day"] != day:
                raise ValueError("组合修改需要在同一天内试排。")
            operations.append(operation)
        if sum(op["action"] == "meal_options" for op in operations) > 1:
            raise ValueError("请为其中一家餐馆指定分店。")
        keys = {(op["slot"], op["action"]) for op in operations}
        if len(keys) != len(operations):
            raise ValueError("同一活动收到重复修改。")
        for index in {op["slot"] for op in operations}:
            actions = {op["action"] for op in operations if op["slot"] == index}
            if ("remove_visit" in actions and len(actions) > 1
                    or {"replace", "meal_options"}.issubset(actions)):
                raise ValueError("同一活动的修改互相冲突，请明确要保留哪一项。")
        return {"action": "batch", "day": day, "pace": pace, "pace_period": period, "operations": operations}

    @staticmethod
    def _fields(value, day, slot, activity):
        action = value["action"]
        op = {"action": action, "day": day, "slot": slot}
        if action == "remove_visit" and activity["type"] == "attraction" and not activity.get("_lock"):
            return op
        if (action == "advance_transfer" and activity.get("_lock") in ("departure", "reservation")
                and isinstance(value.get("start"), str) and CLOCK.fullmatch(value["start"]) and value["start"] != "24:00"):
            return {**op, "start": value["start"]}
        if action == "meal_options" and activity["type"] in ("breakfast", "lunch", "dinner") and not activity.get("_lock"):
            cuisine = value.get("cuisine")
            if cuisine is None:
                return op
            if isinstance(cuisine, str) and 0 < len(cuisine.strip()) <= 24:
                return {**op, "cuisine": cuisine.strip()}
        if action == "time" and all(isinstance(value.get(key), str) and CLOCK.fullmatch(value[key])
                                    for key in ("start", "end")):
            return {**op, "start": value["start"], "end": value["end"]}
        if action == "replace" and isinstance(value.get("place"), str) and 0 < len(value["place"]) <= 120:
            return {**op, "place": value["place"], **({"keep_connection_departure": True}
                    if value.get("keep_connection_departure") is True else {})}
        if action == "transport" and value.get("mode") in ("walk", "taxi", "metro"):
            return {**op, "mode": value["mode"]}
        raise ValueError("修改参数无效，请重新选择。")

    def _warm_for(self, selected):
        plan = selected["plan"]
        data = self.snapshots / selected["archive"]["snapshot"]["commit"][:7]
        self.worker.warm(data, plan.get("start_city"), plan.get("target_city"))

    def prewarm(self, value):
        _, selected, _ = self._base(value)
        self._warm_for(selected)
        return 202, {"state": "accepted"}

    def assist(self, value):
        _, selected, _ = self._base(value)
        text = value.get("text")
        if not isinstance(text, str) or not 0 < len(text.strip()) <= 600:
            raise ValueError("请输入修改要求或问题，最多 600 字。")
        archive, plan = selected["archive"], selected["plan"]
        revision = {"request": archive["requestId"], "revision": archive["requestRevision"]}
        if not self._current(revision):
            return 409, {"message": "需求版本已变化，请重新打开本次行程。"}
        activities = []
        for day in plan["itinerary"]:
            for item in day["activities"]:
                legs = item.get("transports") or []
                arrival = f"按规划快照预计 {legs[-1]['end_time']} 到达。" if legs else ""
                activities.append({"id": item["_uid"], "day": day["day"], "kind": item["type"],
                                   "name": item.get("_title") or item.get("position") or item["type"],
                                   "start": item["start_time"], "end": item["end_time"],
                                   "locked": bool(item.get("_lock")), "has_connection": bool(legs),
                                   "arrival_note": arrival})
        focus = value.get("focus")
        day = value.get("day")
        payload = {"text": text.strip(), "city": plan["target_city"], "activities": activities,
                   "day": day if type(day) is int and 1 <= day <= len(plan["itinerary"]) else None,
                   "focus": focus if any(item["id"] == focus for item in activities) else None,
                   "snapshot": archive["snapshot"], "metrics": archive["metrics"],
                   "model_provider": archive.get("modelProvider")}
        if archive.get("modelProvider") in (None, "deepseek"):
            Thread(target=self._warm_for, args=(selected,), daemon=True).start()
        code, result = self.planner.request("/itinerary-edit/intent", payload=payload)
        if code == 200 and (result.get("operations") or result.get("proposal")) and not self._current(revision):
            return 409, {"message": "理解请求期间需求已更新。请在当前行程上重新发送。"}
        return code, result

    def start(self, value):
        source, selected, original = self._base(value)
        info = selected["archive"]
        meta = {"job": value["job"], "candidate": selected["id"], "original": original,
                "parent": value.get("parent"), "request": info["requestId"], "revision": info["requestRevision"],
                "created": time.time()}
        if not self._current(meta):
            return 409, {"message": "需求版本已变化，或规划服务暂不可用。请重新打开任务后再修改。"}
        operation = self._operation(value, selected)
        meta["action"] = operation["action"]
        folder = self.store / uuid4().hex
        folder.mkdir(parents=True)
        write(folder / "input.json", meta)
        write(folder / "operation.json", operation)
        data = self.snapshots / info["snapshot"]["commit"][:7]
        hotel = operation["action"] == "hotel"
        # All model-independent recalculation runs in the planner's established environment.
        args = [self.python, "-B", "-X", "utf8", str(self.root / "personalize.py"),
                "hotel-preview" if hotel else "edit-preview", "--input", str(source),
                "--out", str(folder / "run"), "--data", str(data), "--candidate-id", selected["id"]]
        if not hotel:
            args += ["--edit-file", str(folder / "operation.json")]
        reusable = None if hotel else (data, source, selected["id"])
        Thread(target=self._run, args=(args, folder, 115 if hotel else 55, reusable), daemon=True).start()
        return 202, {"id": folder.name, "state": "starting"}

    def _run(self, args, folder, timeout, reusable):
        message = self.worker.preview(*reusable, folder, timeout) if reusable else None
        if message is None:
            message = self._run_isolated(args, folder, timeout)
        status = folder / "run" / "STATUS.json"
        if not status.is_file() or read(status).get("state") == "planning":
            write(folder / "FAILED.json", {"state": "worker_failed", "message": message or "修改复核未返回结果。"})

    def _run_isolated(self, args, folder, timeout):
        try:
            with (folder / "worker.log").open("w", encoding="utf-8") as output:
                subprocess.run(args, cwd=self.root, stdout=output, stderr=subprocess.STDOUT,
                               timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            return "修改进程退出，请回到原行程后重试。"
        except subprocess.TimeoutExpired:
            return "修改复核超过等待时限，请回到原行程后重试。"
        except OSError:
            return "本机无法启动修改复核进程。"

    @staticmethod
    def _status(folder):
        message = "正在加载修改所需的旅行数据…"
        for path in (folder / "FAILED.json", folder / "run" / "STATUS.json"):
            if path.is_file():
                result = read(path)
                if result["state"] != "planning":
                    return result
                message = result.get("message") or "正在重新计算接驳、时间与费用…"
                break
        meta = read(folder / "input.json")
        if time.time() - meta["created"] > (130 if meta["action"] == "hotel" else 70):
            return {"state": "worker_failed", "message": "修改复核已经中断，请重试。"}
        return {"state": "planning", "message": message}

    def _project(self, folder, meta, adopted=False):
        group = load_archive(folder / "run", self.snapshots, job_id=meta["job"])
        group["versions"][0]["revision"] = {"id": folder.name, "original": meta["original"],
                                             "parent": meta["parent"], "adopted": adopted}
        return group

    def state(self, folder):
        status = self._status(folder)
        value = {"id": folder.name, "state": status["state"], "message": status.get("message")}
        if status["state"] == "complete":
            if "meal_options" in status:
                return 200, {**value, "meal_options": status["meal_options"]}
            value["change"] = status.get("hotel_change") or status.get("edit")
            value["group"] = self._project(folder, read(folder / "input.json"), (folder / "ADOPTED.json").exists())
        return 200, value

    def adopt(self, folder):
        meta = read(folder / "input.json")
        if meta["action"] == "meal_options":
            return 409, {"message": "请先选择饭店并核验替换。"}
        marker = folder / "ADOPTED.json"
        if marker.is_file():
            return self.state(folder)
        if self._status(folder)["state"] != "complete":
            return 409, {"message": "当前修改尚未通过核验。"}
        self._project(folder, meta)
        if not self._current(meta):
            return 409, {"message": "需求版本已变化，或规划服务暂不可用，暂时无法采用。"}
        with self._adopt_lock:
            if not marker.is_file():
                write(marker, {"at": datetime.now(timezone.utc).isoformat()})
        return self.state(folder)

    def history(self, query):
        job, original = query.get("job"), query.get("original")
        versions = []
        for marker in self.store.glob("*/ADOPTED.json"):
            meta = read(marker.parent / "input.json")
            if (meta["job"], meta["original"]) == (job, original):
                status = self._status(marker.parent)
                if status["state"] == "complete":
                    versions.append({"id": marker.parent.name, **read(marker),
                                     "change": status.get("hotel_change") or status.get("edit")})
        return 200, sorted(versions, key=lambda v: v["at"], reverse=True)

    def places(self, value):
        _, selected, _ = self._base(value)
        data = selected["archive"]["snapshot"]
        database = self.snapshots / data["commit"][:7] / "sandbox_profiles" / data["profile"] / "database"
        index = CityIndex(selected["plan"]["target_city"], database)
        items = [a for day in selected["plan"]["itinerary"] for a in day["activities"]]
        origin = next((v for v in items if v["_uid"] == value.get("activity")
                       and v["type"] in PLACE_KINDS and not v.get("_lock")), None)
        if not origin:
            raise ValueError("请选择一项可更换的活动。")
        kind = PLACE_KINDS[origin["type"]]
        focus = origin["_pos"]
        radius = None
        if value.get("toward"):
            anchor = next((b for day in selected["plan"]["itinerary"]
                           for a, b in zip(day["activities"], day["activities"][1:])
                           if a["_uid"] == origin["_uid"] and b["_uid"] == value["toward"] and b.get("_lock")), None)
            if not anchor or not anchor.get("_pos") or not focus or kind not in ("restaurant", "attraction"):
                raise ValueError("请从预约或返程的紧邻前一站选择替换。")
            radius = haversine(focus, anchor["_pos"])
            focus = anchor["_pos"]
        occupied = ({item.get("position") for item in items if item["type"] == "attraction"}
                    if kind == "attraction" else {origin.get("position")})
        terms = value.get("q", "")
        if not isinstance(terms, str) or len(terms) > 120:
            raise ValueError("请输入有效的地点名称。")
        terms = terms.strip().casefold()
        bounds = value.get("bounds")
        if bounds is not None:
            if (not isinstance(bounds, list) or len(bounds) != 4
                    or any(type(v) not in (int, float) for v in bounds)
                    or not (-90 <= bounds[0] < bounds[2] <= 90 and -180 <= bounds[1] < bounds[3] <= 180)):
                raise ValueError("地图范围无效，请移动地图后重试。")
            south, west, north, east = bounds
        options = []
        original = index.places.get(origin.get("position"), {})
        original_alias = original.get("hotel_name_en")
        hotel_aliases = {(original_alias.casefold(), original["lat"], original["lon"])} if original_alias else set()
        for name, poi in index.places.items():
            if poi["kind"] != kind or name in occupied:
                continue
            alias = poi.get("hotel_name_en", "").casefold()
            if terms not in name.casefold() and terms not in alias:
                continue
            lat, lon = poi["lat"], poi["lon"]
            if bounds is not None and not (south <= lat <= north and west <= lon <= east):
                continue
            km = haversine(focus, [lat, lon]) if focus else None
            if radius is not None and km >= radius:
                continue
            if alias:
                identity = (alias, lat, lon)
                if identity in hotel_aliases:
                    continue
                hotel_aliases.add(identity)
            price_label = {"attraction": "门票", "hotel": "价格", "restaurant": "人均"}[kind]
            options.append({"name": name, "km": km, "price": poi["info"].get(price_label), "pos": [lat, lon]})
        return 200, sorted(options, key=lambda p: (p["km"] if p["km"] is not None else 0, p["name"]))[:12]

    def dispatch(self, method, path, query, body):
        try:
            if method == "GET" and path == "state":
                return self.state(self._folder(query.get("id")))
            if method == "GET" and path == "history":
                return self.history(query)
            if method == "POST" and isinstance(body, dict):
                if path == "assist":
                    return self.assist(body)
                if path == "prewarm":
                    return self.prewarm(body)
                if path == "preview":
                    return self.start(body)
                if path == "adopt":
                    return self.adopt(self._folder(body.get("id")))
                if path == "places":
                    return self.places(body)
        except ValueError as error:
            return 409, {"message": str(error)}
        return 404, {"message": "找不到行程修改接口。"}
