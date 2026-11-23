"""An experimental return prompt that exposes feasible later departures."""

from chinatravel.agent.nesy_agent.pruning import clock, selection_reason, ticket_kind


def prompt_options(frame, query, outbound):
    departure_limit = clock(outbound.get("EndTime")) if query["days"] == 1 else None
    eligible = []
    for _, row in frame.iterrows():
        if selection_reason(query, {"go_transport": outbound, "back_transport": row}):
            continue
        leave = clock(row.get("BeginTime"))
        if leave is None or (departure_limit is not None and leave <= departure_limit):
            continue
        kind = ticket_kind(row)
        if kind is None:
            continue
        ticket_id = row["TrainID" if kind == "train" else "FlightID"]
        eligible.append((row, leave, ticket_id))
    if not eligible:
        return str(frame)
    latest = sorted(eligible, key=lambda e: (-e[1], e[0]["Cost"]))
    lowest = sorted(eligible, key=lambda e: (e[0]["Cost"], -e[1]))
    shown = {}
    for row, _, ticket_id in latest[:12] + lowest[:6]:
        shown[ticket_id] = row
    lines = ["ID | departs | arrives | from | to | fare/person"]
    for ticket_id, row in shown.items():
        lines.append(f'{ticket_id} | {row["BeginTime"]} | {row["EndTime"]} | '
                     f'{row["From"]} | {row["To"]} | {row["Cost"]}')
    return "\n".join(lines)
