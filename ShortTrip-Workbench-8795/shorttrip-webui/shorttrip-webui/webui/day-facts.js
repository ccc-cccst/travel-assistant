window.mountDayFacts = function (root, trip, activeDay, {itemId, initialDate, isCurrent, saveDate}) {
  root.innerHTML = `
    <div class="day-facts-controls">
      <label>旅行第一天 <input type="date" class="facts-date"></label>
      <label>查看哪天 <select class="facts-day"></select></label>
    </div>
    <h4>地点营业</h4><ul class="facts-places"></ul>`;
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
    if (info.serviceWindows?.length && item.start && item.end && !info.serviceWindows.some(
      ([, start, end]) => start <= item.start && item.start < end && item.start <= item.end && item.end <= end
    )) return "行程时段超出餐厅公布的供餐时间。";
    if (!info.hours) return "";
    if (item.start && (item.start < info.hours[0] || item.end > info.hours[1])) return "计划时段超出常规开放时间。";
    if (!info.lastEntry) return "";
    if (item.start === info.lastEntry) return "原计划恰逢停止入场时刻，请核对预约及安检时间。";
    if (item.start > info.lastEntry) return "原计划晚于停止入场时刻。";
    return "";
  }
  function showPlace(out, item, info) {
    const links = document.createElement("div");
    links.className = "facts-source-links";
    if (info.official) {
      const official = info.official;
      const windows = official.serviceWindows?.map(([label, start, end]) => label + " " + start + "–" + end).join("，");
      element(out, "p", [official.sourceLabel !== "场馆官网" ? official.sourceLabel : official.name, windows || official.hours?.join("–"),
        official.lastEntry && ("停止入场 " + official.lastEntry), official.address].filter(Boolean).join(" · "));
      const attention = info.date ? officialAlert(official,item) : "选定出游日期后可核对当天的安排。";
      if (attention) element(out,"p",attention).className = "facts-warning";
      if (official.notice) element(out, "p", official.notice);
      source(links,(official.sourceLabel || "场馆官网") + "（" + official.checkedOn + " 核对）",official.source);
    }
    if (info.notice) {
      element(out,"p",info.notice.text);
      source(links,info.notice.sourceLabel,info.notice.source);
    }
    if (["matched","site_match","alias_match"].includes(info.status)) {
      if (info.status === "site_match") element(out,"p","匹配到景区，地图点与原行程相差约 " + info.coordinateGapMeters + " 米，入口需核对。");
      if (info.status === "alias_match") element(out,"p","附近高德门店为「" + info.name + "」，请核对是否为原行程的分店。");
      if (!info.official && (info.name || info.address)) element(out,"p",[info.name,info.address].filter(Boolean).join(" · "));
      if (!info.notice?.checkDateSchedule && !info.official?.serviceWindows?.length) {
        if (info.today && openingIsToday(info)) element(out,"p","高德当天参考 · " + info.today);
        if (info.week) element(out,"p","高德营业参考 · " + info.week);
        else if (!info.official && (!openingIsToday(info) || !info.today)) element(out,"p","出游日营业时间待核实。");
      }
    } else if (info.status === "hours_conflict") {
      element(out,"p",info.address + "有两条高德记录，营业时间不同。请向门店确认。").className = "facts-warning";
      const reports = element(out,"ul","");
      reports.className = "facts-hours-options";
      for (const report of info.hoursOptions) element(reports,"li",report.name + " · " + report.hours);
    } else if (info.status !== "schedule_required" && !(info.notice && info.status === "unmatched")) {
      element(out,"p",{
        ambiguous:"找到多个相近地点，请核对具体门店。",
        unmatched:"高德地点未匹配，营业仍待核实。",
      }[info.status] || "本次高德查询未完成，可重查。");
    }
    source(links, "高德地点搜索", info.mapUrl);
    if (links.childElementCount) out.append(links);
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
      const row = element(places,"li","");
      row.className = "facts-place";
      const heading = element(row,"div","");
      heading.className = "facts-place-head";
      const description = element(heading,"div","");
      element(description,"strong",item.name);
      if (item.hours) element(description,"small","行程参考 " + item.hours);
      if (!item.pos) {
        element(description,"small","缺少地点坐标，需自行核实。");
        continue;
      }
      const btn = element(heading,"button","查营业");
      btn.type = "button";
      btn.setAttribute("aria-label","查营业 " + item.name);
      const out = element(row,"div","");
      out.className = "facts-result";
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
          if (!ongoing.signal.aborted) out.textContent = "查询未完成，请重试。";
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
