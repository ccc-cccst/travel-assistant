"""在 chinatravel-five-city 环境里跑一次规划（独立进程，避免和本仓库的 chinatravel 包冲突）。

用法：python plan_worker.py --five-city /path/to/chinatravel-five-city < query.json > result.json
输入 query：{start_city, target_city, days, people_number, nature_language, hard_logic_py?: [...]}
输出：{ok, plan, reason, report: {all_ok, hard_pass, hard_total, commonsense_violations}, seconds}
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import time
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--five-city", required=True, type=Path)
    ap.add_argument("--max-evaluations", type=int, default=60)
    ap.add_argument("--lang", default="zh")
    a = ap.parse_args()
    root = a.five_city.resolve()
    sys.path.insert(0, str(root / "src"))
    sys.path.insert(0, str(root / "ChinaTravel"))
    query = json.loads(sys.stdin.read())
    query.setdefault("uid", "webui")
    query.setdefault("hard_logic_py", [f"result=(day_count(plan)=={query['days']})",
                                       f"result=(people_count(plan)=={query['people_number']})"])
    t0 = time.time()
    log = io.StringIO()
    try:
        with contextlib.redirect_stdout(log):  # 规划器会打印日志，别混进 JSON
            from shorttrip.env import get_env
            from shorttrip.planner_v2 import ConstraintPlannerV2
            result = ConstraintPlannerV2(get_env(a.lang), max_evaluations=a.max_evaluations).plan(query)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "reason": f"{type(e).__name__}: {e}", "log": log.getvalue()[-2000:]}, ensure_ascii=False))
        return 0
    rep = result.report
    out = {
        "ok": result.plan is not None,
        "plan": result.plan,
        "reason": result.reason,
        "seconds": round(time.time() - t0, 2),
        "report": None if rep is None else {
            "all_ok": bool(rep.all_ok), "hard_pass": rep.hard_pass_count, "hard_total": len(rep.hard_results),
            "commonsense_ok": bool(rep.commonsense_ok), "commonsense_violations": list(rep.commonsense_violations or []),
        },
    }
    print(json.dumps(out, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
