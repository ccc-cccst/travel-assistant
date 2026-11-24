// Check the two published, date-sensitive metro connections with the least planned slack.
window.createTransferReview = function ({panel, clock, reviewDate, setReviewDate, dateChanged}) {
  const state = panel.querySelector("#transferState");
  const date = panel.querySelector("#transferStart");
  const rows = panel.querySelector("#transferRows");
  const retry = panel.querySelector("#transferRetry");
  let key, controller, legs = [], city, owner;

  function showText(parent, message) {
    const text = document.createElement("p");
    text.textContent = message;
    parent.append(text);
    return text;
  }
  function travelDate(day) {
    const start = new Date(date.value + "T00:00:00Z");
    start.setUTCDate(start.getUTCDate() + day - 1);
    return start.toISOString().slice(0, 10);
  }
  async function lookup(leg, departure, signal) {
    const r = await API.getRoute({city, from:leg.first.fromPos, to:leg.last.toPos, mode:"subway", departure, signal});
    if (r?.source !== "amap" || r.requestedDeparture !== departure || r.unavailable || !(r.durationSeconds > 0))
      throw Error(r?.reason?.includes("10021") ? "高德查询频率受限，请稍后重试。" : "未取得这个时段的地铁方案。");
    return Math.ceil(r.durationSeconds / 60);
  }

  async function check() {
    controller?.abort();
    const run = new AbortController();
    controller = run;
    rows.replaceChildren();
    retry.disabled = !date.value;
    if (!date.value) {
      state.textContent = "· 选择旅行第一天后核对";
      return;
    }
    retry.disabled = true;
    const selected = legs.slice(0, 2);
    const signal = AbortSignal.any([run.signal, AbortSignal.timeout(5000)]);
    state.textContent = "· 正在按行程日期核对";
    let tight = 0, unknown = 0;
    for (const [i, leg] of selected.entries()) {
      if (i) await new Promise(resolve => setTimeout(resolve, 1300));
      if (controller !== run) return;
      const row = document.createElement("div");
      rows.append(row);
      const line = showText(row, "");
      const heading = "第 " + leg.day + " 天 " + leg.first.from + " → " + leg.last.to + " · ";
      try {
        signal.throwIfAborted();
        const duration = await lookup(leg, travelDate(leg.day) + "T" + leg.first.start, signal);
        if (controller !== run) return;
        const slack = leg.deadline - leg.leave - duration;
        if (slack < 0) tight++;
        line.textContent = heading + (slack < 0 ? "按高德估时，这段接驳时间偏紧。" : "按高德估时，这段接驳有余量。");
      } catch (e) {
        if (controller !== run) return;
        unknown++;
        line.textContent = heading + "待核实。" + (e.name === "Error" ? e.message : "查询超时或暂不可用。");
      }
    }
    retry.disabled = false;
    state.textContent = `· 本次核对 ${selected.length} 段，${tight} 段时间偏紧，${unknown} 段待核实`;
  }
  date.onchange = () => {
    setReviewDate(owner, date.value);
    dateChanged();
    void check();
  };
  retry.onclick = check;

  return (trip, group) => {
    const id = trip?.archive && !group?.preview && API.canLookupRoute
      ? JSON.stringify([group?.id, trip.id, trip.revision?.id, trip.startDate]) : "";
    if (key === id) {
      if (id && !panel.hidden) {
        const value = reviewDate(trip, group);
        if (date.value !== value) {
          controller?.abort();
          controller = null;
          date.value = value;
          rows.replaceChildren();
          retry.disabled = !value;
          state.textContent = value ? "· 日期已更新，可核对接驳" : "· 选择旅行第一天后核对";
        }
      }
      return;
    }
    controller?.abort();
    controller = null;
    key = id;
    panel.hidden = true;
    panel.open = false;
    legs = [];
    if (!id) return;
    for (const day of trip.days) {
      for (const item of day.items) {
        const segments = item.leg?.segments;
        if (!item.lock || !segments?.some(s => s.mode === "metro")) continue;
        const first = segments[0], last = segments.at(-1);
        const leave = clock(first.start), start = clock(item.start), arrival = clock(last.end);
        if (!first.fromPos || !last.toPos || leave == null || start == null || arrival == null || start < leave) continue;
        const reserved = item.lock === "departure" ? {train:60, airplane:120}[item.type] : 0;
        if (reserved == null) continue;
        legs.push({day:day.day, first, last, leave, deadline:start-reserved,
                   plannedSlack:start-reserved-arrival});
      }
    }
    if (!legs.length) return;
    legs.sort((a,b) => a.plannedSlack-b.plannedSlack || a.day-b.day || a.leave-b.leave);
    city = trip.city;
    panel.hidden = false;
    owner = group;
    date.value = reviewDate(trip, group);
    date.readOnly = Boolean(trip.startDate);
    rows.replaceChildren();
    retry.disabled = !date.value;
    state.textContent = "";
  };
};
