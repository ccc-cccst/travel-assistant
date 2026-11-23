"""Same-origin workbench interface to the local personalized planner."""

from __future__ import annotations

from functools import lru_cache
from datetime import date
import json
import re
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

from personalized_archive import load_archive

IDENTIFIER = re.compile(r"[a-f0-9]{32}")
ATTEMPT = re.compile(r"attempt_[1-9][0-9]*")


def has_departure_date(spec):
    for item in spec.get("items", ()):
        if item.get("field") != "start_date":
            continue
        value = item.get("value")
        if not isinstance(value, str):
            continue
        try:
            if date.fromisoformat(value).isoformat() == value:
                return True
        except ValueError:
            continue
    return False


class Personalized:
    def __init__(self, port, planner_root, snapshots):
        self.url = f"http://127.0.0.1:{port}"
        self.results = planner_root / "result"
        self.snapshots = snapshots
        self.opener = build_opener(ProxyHandler({}))
        self.starts = {}
        self.start_lock = Lock()

    def request(self, path, *, query=None, payload=None):
        target = self.url + path + ("?" + urlencode(query) if query else "")
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        timeout = {"/tripspec/interpret": 35, "/itinerary-edit/intent": 22}.get(path, 8)
        try:
            with self.opener.open(Request(target, data=body, headers={"Content-Type": "application/json"}),
                                  timeout=timeout) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            with error:
                return error.code, json.load(error)
        except (URLError, TimeoutError, json.JSONDecodeError):
            return 503, {"message": "暂时无法连接个性化规划服务。可保留当前输入并重试。"}

    def start(self, body):
        token = body.get("submission_id", "")
        if not isinstance(token, str) or not IDENTIFIER.fullmatch(token):
            return 400, {"message": "提交编号无效，请返回需求确认后重试。"}
        payload = {key: body[key] for key in ("request_id", "request_revision", "try_budget_alternatives")
                   if key in body}
        with self.start_lock:
            if token in self.starts:
                existing, result = self.starts[token]
                if existing != payload:
                    return 409, {"message": "需求已变化，请重新确认后提交。"}
                return 202, result
            code, current = self.request("/tripspec/state", query={"id": payload.get("request_id", "")})
            if code != 200:
                return code, current
            if current.get("request_revision") != payload.get("request_revision"):
                return 409, {"message": "需求已变化，请重新确认后提交。"}
            if not has_departure_date(current):
                return 400, {"message": "请填写具体出发日期后再规划。"}
            code, result = self.request("/trip-planning", payload=payload)
            if code == 202:
                if len(self.starts) >= 256:
                    self.starts.pop(next(iter(self.starts)))
                self.starts[token] = payload, result
            return code, result

    @lru_cache(maxsize=32)
    def project(self, folder, final, job_id):
        return load_archive(folder, self.snapshots, final=final, job_id=job_id)

    def job(self, job_id):
        if not IDENTIFIER.fullmatch(job_id):
            return 400, {"message": "规划任务编号无效。"}
        code, status = self.request("/trip-planning/job", query={"id": job_id})
        if code != 200:
            return code, status
        folder = self.results / "manual" / job_id
        view = {key: status[key] for key in (
            "state", "stage", "message", "error_code", "stop_reason", "candidate_count",
            "first_visible_seconds", "request_id", "request_revision", "timings", "model", "model_provider", "recovered"
        ) if key in status}
        evidence = status.get("evidence") or {}
        undecided = evidence.get("unsupported_requirements") or evidence.get("conflicting_requirements") or []
        if undecided:
            view["requirements_to_review"] = [
                {"label": item.get("label") or item.get("field"), "value": item.get("value"),
                 "source": (item.get("source") or {}).get("text")}
                for item in undecided if isinstance(item, dict)
            ]
        recovery = status.get("recovery", {})
        if recovery:
            view["recovery"] = {
                "cause": recovery.get("cause"), "draft": recovery.get("draft"),
                "attempts": [{key: attempt[key] for key in ("id", "change", "message", "state") if key in attempt}
                             for attempt in recovery.get("attempts", [])],
            }
            for attempt in recovery.get("attempts", []):
                attempt_id = attempt["id"]
                if attempt.get("view") and ATTEMPT.fullmatch(attempt_id):
                    view["previews"] = view.get("previews", []) + [
                        {"id": attempt_id, "change": attempt["change"],
                         "group": self.project(folder / attempt_id, True, job_id)}]
        if status.get("recovered"):
            # The planning service has selected a recovery attempt. Its published candidate ID
            # identifies the corresponding local delivery, including after re-opening the run.
            recommended = status.get("result", {}).get("recommended_id")
            folder = next(
                (file.parent for file in sorted(folder.glob("attempt_*/PLAN_VIEW.json"))
                 if ATTEMPT.fullmatch(file.parent.name)
                 and json.loads(file.read_text(encoding="utf-8"))["recommended_id"] == recommended),
                None,
            )
        if folder is not None:
            if folder.name != job_id and ATTEMPT.fullmatch(folder.name):
                view["delivery"] = folder.name
            final = status["state"] == "complete" and bool(status.get("result"))
            if final or (folder / "WORKBENCH_FIRST.json").is_file():
                view["group"] = self.project(folder, final, job_id)
        return 200, view

    def dispatch(self, method, path, query, body):
        reads = {"options": "/options", "request": "/tripspec/state", "places": "/tripspec/places"}
        writes = {"interpret": "/tripspec/interpret", "resolve": "/tripspec/resolve",
                  "place-preference": "/tripspec/place-preference",
                  "adopt": "/trip-planning/recovery/accept"}
        if method == "GET" and path in reads:
            return self.request(reads[path], query=query)
        if method == "GET" and path == "job":
            return self.job(query.get("id", ""))
        if method == "POST" and isinstance(body, dict):
            if path == "start":
                return self.start(body)
            if path in writes:
                return self.request(writes[path], payload=body)
        return 404, {"message": "找不到个性化规划接口。"}
