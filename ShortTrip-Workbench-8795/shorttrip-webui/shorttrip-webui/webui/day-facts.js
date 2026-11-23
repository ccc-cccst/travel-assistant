window.mountDayFacts = function (root, trip, activeDay, {itemId, initialDate, isCurrent, saveDate}) {
  root.innerHTML = `
    <div class="day-facts-controls">
      <label>旅行第一天 <input type="date" class="facts-date"></label>
      <label>查看哪天 <select class="facts-day"></select></label>
    </div>
    <h4>地点营业</h4><ul class="check-list facts-places"></ul>`;
  const date = root.querySelector(".facts-date"), selector = root.querySelector(".facts-day");
  const places = root.querySelector(".facts-places");
  const openingIsToday = info => info.date && info.todayDate === info.date;
  let placeRequests;

  function element(parent, tag, text) {
    const node = document.createElement(tag);
    node.textContent = text;
    parent.append(node);
    return node;
  }
  function source(parent, name, href) {
    if (!href?.startsWith("https://")) return;
    const link = element(parent, "a", name + " ↗");
    link.href = href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
  }
  function visitDay() {
    return trip.days.find(d => d.day === Number(selector.value));
  }
  function dayDate(day) {
    if (!date.value) return null;
    const first = new Date(date.value + "T00:00:00Z");
    first.setUTCDate(first.getUTCDate() + day - 1);
    return first.toISOString().slice(0,10);
  }
  function officialAlert(info, item) {
    if (info.usualClosure) return "按常规时段当天闭馆，节假日及专场请核对官网。";
    if (info.regularHoursUncertain) return "该星期的开放时段请向馆方核对。";
    if (!info.hours) return "";
    if (item.start && (item.start < info.hours[0] || item.end > info.hours[1])) return "计划时段超出常规开放时间。";
    if (item.start === info.lastEntry) return "原计划恰逢停止入场时刻，请核对预约及安检时间。";
    if (item.start > info.lastEntry) return "原计划晚于停止入场时刻。";
    return "";
  }
  function showPlace(out, item, info) {
    if (info.official) {
      const official = info.official;
      element(out, "p", [official.sourceLabel && official.sourceLabel !== "场馆官网" ? official.sourceLabel : official.name,
        official.hours?.join("–"),
        official.lastEntry && ("停止入场 " + official.lastEntry), official.address].filter(Boolean).join(" · "));
      const attention = info.date ? officialAlert(official,item) : "选择出游日期后可核对常规闭馆日。";
      if (attention) element(out,"p",attention);
      element(out, "p", official.notice);
      source(out,(official.sourceLabel || "场馆官网") + "（" + official.checkedOn + " 核对）",official.source);
    }
    if (info.notice) {
      element(out,"p",info.notice.text);
      source(out,info.notice.sourceLabel,info.notice.source);
    }
    if (["matched","site_match","alias_match"].includes(info.status)) {
      if (info.status === "site_match") element(out,"p","匹配到景区，地图点与原行程相差约 " + info.coordinateGapMeters + " 米，入口需核对。");
      if (info.status === "alias_match") element(out,"p","附近高德门店为「" + info.name + "」，请核对是否为原行程的分店。");
      if (info.address) element(out,"p","高德地点 · " + info.address);
      if (info.today) element(out,"p",(info.status === "matched" && openingIsToday(info) ? "出游日当天" : "高德今日参考") + " · " + info.today);
      if (info.week) element(out,"p","高德常规营业 · " + info.week);
      if (!info.today && !info.week) element(out,"p","营业时间待核实。");
    } else if (info.status !== "schedule_required" && !(info.notice && info.status === "unmatched")) {
      element(out,"p",{
        ambiguous:"找到多个相近地点，分店待核实。",
        unmatched:"高德未找到可确认的同名地点，请按原地址核对。",
      }[info.status] || "暂未取得高德营业信息，请稍后重试。");
    }
    source(out, "高德地点搜索", info.mapUrl);
  }

  function renderPlaces(checkItem) {
    placeRequests?.abort();
    placeRequests = new AbortController();
    const ongoing = placeRequests;
    places.replaceChildren();
    const day = visitDay();
    const dated = dayDate(day.day);
    const remaining = day.items.filter(item => ["attraction","restaurant"].includes(item.kind));
    if (!remaining.length) element(places,"li","当天没有景点或餐馆。");
    for (const item of remaining) {
      const row = element(places,"li",item.name + (item.hours ? " · 快照 " + item.hours : ""));
      if (!item.pos) continue;
      const btn = element(row,"button","查营业");
      btn.type = "button";
      btn.setAttribute("aria-label","查营业 " + item.name);
      const out = element(row,"div","");
      out.setAttribute("aria-live","polite");
      btn.onclick = async () => {
        btn.disabled = true;
        out.textContent = "正在查询…";
        const query = {city:trip.city, name:item.place || item.name, kind:item.kind, pos:item.pos.join(",")};
        if (dated) query.date = dated;
        try {
          const result = await API.dayFacts("place", query, AbortSignal.any([ongoing.signal,AbortSignal.timeout(6500)]));
          if (!isCurrent() || ongoing.signal.aborted || !root.isConnected) return;
          out.replaceChildren();
          showPlace(out,item,result);
        } catch {
          if (!ongoing.signal.aborted) out.textContent = "营业时间待核实，可重查。";
        } finally { btn.disabled = false; }
      };
      if (item.uid === checkItem) {
        places.prepend(row);
        btn.click();
      }
    }
  }

  for (const day of trip.days) element(selector,"option","第 " + day.day + " 天").value = day.day;
  selector.value = trip.days.some(day => day.day === activeDay) ? activeDay : trip.days[0].day;
  date.value = initialDate;
  date.readOnly = Boolean(trip.startDate);
  selector.onchange = () => renderPlaces();
  date.onchange = () => {saveDate(date.value); renderPlaces(itemId);};
  renderPlaces(itemId);
};

window.createTimelineWeather = function (root, mapPane, mapWeather, onMapChange = () => {}) {
  let key, controller, result, start, activeDay = 0;
  const mapTitle = mapWeather.querySelector("strong");
  const mapContext = mapWeather.querySelector(".map-weather-info > span");

  function dateFor(day) {
    const on = new Date(start + "T00:00:00Z");
    on.setUTCDate(on.getUTCDate() + day - 1);
    return on.toISOString().slice(0,10);
  }
  function themeFor(condition) {
    if (/雪|冰粒/.test(condition)) return "snow";
    if (/雨|雷/.test(condition)) return "rain";
    if (/雾|霾|阴|云/.test(condition)) return "cloud";
    if (/晴/.test(condition)) return "sun";
    return "neutral";
  }
  const degrees = value => Number.isInteger(value) ? value + "°C" : "";
  const dateText = iso => Number(iso.slice(5,7)) + "月" + Number(iso.slice(8,10)) + "日";

  function showMap() {
    const cast = start && activeDay ? result?.days?.find(d => d.date === dateFor(activeDay)) : null;
    const condition = cast?.day || cast?.night;
    mapWeather.hidden = !condition;
    if (!condition) {
      mapPane.removeAttribute("data-weather-theme");
      onMapChange();
      return;
    }
    const temp = degrees(cast.day ? cast.dayTemp : cast.nightTemp);
    mapPane.dataset.weatherTheme = themeFor(condition);
    mapTitle.textContent = temp ? condition + " " + temp : condition;
    mapContext.textContent = "第" + activeDay + "天 " + (cast.day ? "白天" : "夜间") + "预报";
    onMapChange();
  }

  function show() {
    for (const row of root.querySelectorAll("[data-weather-day]")) {
      const status = row.querySelector(".timeline-forecast");
      const retry = row.querySelector("[data-weather-retry]");
      const provider = row.querySelector(".timeline-weather-source");
      const periods = row.querySelector(".timeline-weather-periods");
      const date = row.querySelector(".timeline-weather-date");
      row.dataset.weatherTheme = "neutral";
      if (retry) retry.hidden = true;
      provider.hidden = true;
      periods.hidden = true;
      status.hidden = false;
      date.hidden = !start;
      if (!start) {
        status.textContent = "补出发日期后查看天气";
        continue;
      }
      const iso = dateFor(Number(row.dataset.weatherDay));
      date.textContent = dateText(iso);
      const cast = result?.days?.find(d => d.date === iso);
      if (!result) status.textContent = "正在查天气";
      else if (result.status === "unavailable") {
        status.textContent = "暂未查到天气";
        if (retry) retry.hidden = false;
      } else if (cast?.day || cast?.night) {
        row.dataset.weatherTheme = themeFor(cast.day || cast.night);
        for (const part of ["day","night"]) {
          row.querySelector('[data-weather-part="' + part + '"]').textContent = cast[part] || "待查";
          const temp = row.querySelector('[data-weather-temp="' + part + '"]');
          temp.textContent = degrees(cast[part + "Temp"]);
          temp.hidden = !temp.textContent;
        }
        status.hidden = true;
        periods.hidden = false;
        provider.hidden = false;
        provider.title = result.publishedAt ? "发布于 " + result.publishedAt : "高德天气";
      } else status.textContent = "暂无预报，临近出行再查";
    }
    showMap();
  }

  function render(t, newStart, refresh = false) {
    start = newStart;
    const next = t && start ? JSON.stringify([t.city,start,t.days.length]) : "";
    if (key === next && !refresh) return show();
    key = next;
    controller?.abort();
    result = null;
    show();
    if (!next) return;
    const run = new AbortController();
    controller = run;
    const options = {city:t.city, start, days:t.days.length};
    const signal = AbortSignal.any([run.signal,AbortSignal.timeout(6500)]);
    API.dayFacts("weather",options,signal).catch(() => ({status:"unavailable"})).then(data => {
      if (run.signal.aborted) return;
      result = data;
      show();
    });
  }

  function selectDay(day) {
    if (day === activeDay) return;
    activeDay = day;
    showMap();
  }
  return {render, selectDay};
};
