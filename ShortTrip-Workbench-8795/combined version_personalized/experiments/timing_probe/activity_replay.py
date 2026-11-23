"""Replay recorded activity decisions to assess shortcuts and exact-message caching."""

import argparse
import ast
import json
from pathlib import Path
import re

from live_report import HISTORY, jsonl


def choice(answer, options):
    match = re.search(r"Type:\s*(\w+)", answer)
    return match[1] if match and match[1] in options else options[0]


def activity_options(messages):
    menu = re.search(r"当前可选类型 POI_type_list：([^\n]*)", messages[-1]["content"])
    return ast.literal_eval(menu[1]) if menu else None


def replay(path):
    row = {"trace": path.name, "activity_requests": 0, "singleton_requests": 0,
           "singleton_seconds": 0., "singleton_changes": 0, "repeat_requests": 0,
           "repeat_seconds": 0., "repeat_changes": 0, "repeat_multi_choice_changes": 0}
    first = {}
    for call in jsonl(path):
        if call["stage"] != "activity_type" or "assistant" not in call:
            continue
        messages = call["messages"]
        options = activity_options(messages)
        if options is None:
            raise ValueError(f"Unrecognized activity prompt in {path}:{call['call']}")
        decision = choice(call["assistant"]["content"], options)
        row["activity_requests"] += 1
        if len(options) == 1:
            row["singleton_requests"] += 1
            row["singleton_seconds"] += call["elapsed_seconds"]
            row["singleton_changes"] += decision != options[0]
        key = json.dumps(messages, sort_keys=True, ensure_ascii=False)
        if key in first:
            row["repeat_requests"] += 1
            row["repeat_seconds"] += call["elapsed_seconds"]
            changed = first[key] != decision
            row["repeat_changes"] += changed
            row["repeat_multi_choice_changes"] += changed and len(options) > 1
        else:
            first[key] = decision
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    rows = [replay(path) for path in sorted((HISTORY / "traces").glob("*.jsonl"))]
    if not rows:
        raise ValueError("No activity traces found")
    rendered = json.dumps(rows, ensure_ascii=False, indent=2)
    with args.out.open("x", encoding="utf-8") as output:
        output.write(rendered + "\n")
    print(rendered)


if __name__ == "__main__":
    main()
