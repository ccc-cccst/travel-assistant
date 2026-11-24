(function () {
  "use strict";
  const esc = value => String(value ?? "").replace(/[&<>"']/g,
    ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
  const money = new Intl.NumberFormat("zh-CN",{maximumFractionDigits:2});
  let sources;

  function dateFor(start, day) {
    if (!start) return "";
    const date = new Date(start + "T00:00:00Z");
    date.setUTCDate(date.getUTCDate() + day - 1);
    return date.toISOString().slice(0,10);
  }
  function getSource(sources, t, item) {
    const found = sources[t.city]?.[item.place || item.name];
    if (!found || found.kind !== item.kind) return null;
    if (found.point && (!item.pos || API.haversine(item.pos, found.point) > 0.1)) return null;
    return found;
  }
  function collect(t, start, sources) {
    const out = [];
    let stay;
    for (const {day,items} of t.days) {
      for (const item of items) {
        const source = getSource(sources,t,item);
        const {kind, name, code} = item;
        const ticket = kind === "attraction";
        if (ticket && !(source || item.lock || item.price > 0)) continue;
        if (!["hotel","station","airport","attraction"].includes(kind)) continue;
        const rooms = item.raw?.rooms ?? null;
        const beds = item.raw?.room_type ?? null;
        const cost = Number.isFinite(item.cost) ? item.cost : null;
        if (kind === "hotel" && stay?.lastDay === day-1 && stay.name === name && stay.rooms === rooms && stay.beds === beds) {
          stay.lastDay = day;
          stay.end = dateFor(start,day+1);
          stay.cost = stay.cost == null || cost == null ? null : stay.cost + cost;
          continue;
        }
        const row = {kind, name, code, rooms, beds, day, lastDay:day, source,
          start:dateFor(start,day), end:kind === "hotel" ? dateFor(start,day+1) : "",
          time:item.start, visitEnd:item.end, people:t.people, cost,
          price:item.price, slot:ticket ? item.start : ""};
        if (kind === "hotel") stay = row;
        out.push(row);
      }
    }
    return out;
  }
  function summary(row) {
    const day = row.start || "第 " + row.day + " 天";
    const people = Number.isInteger(row.people) ? row.people + " 人" : "";
    if (row.kind === "hotel") return [row.end ? day + " 至 " + row.end : day,
      row.lastDay-row.day+1 + " 晚", people, row.rooms && row.rooms+" 间", row.beds && row.beds+" 床/间"].filter(Boolean);
    return [day, row.code, row.time, people].filter(Boolean);
  }
  function reference(row) {
    if (row.kind === "hotel" && row.cost != null) return "整段住宿参考 ¥" + money.format(row.cost);
    const unit = row.kind === "hotel" ? "间夜" : "人";
    return row.price == null ? "参考价待核实" : "快照参考 ¥" + money.format(row.price) + " / " + unit;
  }

  window.renderBookingReview = async function (root, t, start, scopeId, {current, icon, chooseDate}) {
    root.innerHTML = '<div class="empty">正在读取预订核对项…</div>';
    sources ||= fetch("data/booking-sources.json").then(r => {
      if (!r.ok) throw Error("预订来源加载失败");
      return r.json();
    }).catch(() => null);
    const registry = await sources;
    if (!registry) sources = null;
    if (!current()) return;
    const list = collect(t,start,registry || {});
    const scopeStorage = row => "st.booking:" + JSON.stringify([scopeId,t.city,row.name,row.kind,row.code,row.start,row.end,
      row.time,row.visitEnd,row.people,row.rooms,row.beds]);
    const booked = row => {
      try { return localStorage.getItem(scopeStorage(row)) === "yes"; }
      catch { return false; }
    };
    const html = list.map((row,i) => {
      const {kind,name,source} = row;
      const url = source?.url || (kind === "station" ? "https://www.12306.cn/" : "");
      const label = source?.label || "12306 核对";
      const inventory = kind === "hotel" ? "房型与余房待核实" : kind === "attraction" ? "票种、时段与名额待核实" : "班次与余量待核实";
      return `<article class="bk booking-review-row">
        <span class="mi ${kind}" aria-hidden="true">${icon(kind)}</span>
        <div class="bn"><strong>${esc(name)}</strong>
        <small>${summary(row).map(esc).join(" · ")}</small>
        <div class="booking-state"><span>${esc(reference(row))}</span>
          <span class="booking-pending">${esc(inventory)}</span>
          <span class="booking-confirmed" ${row.start && booked(row) ? "" : "hidden"}>用户已预订</span>
        </div>
        ${source?.notice ? `<small>${esc(source.notice)}</small>` : ""}
        <div class="booking-actions">
          ${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)} ↗</a>` : ""}
          ${row.start ? `<label><input type="checkbox" data-booking-mark="${i}" ${booked(row) ? "checked" : ""}> 我已预订</label>` : ""}
        </div>
        </div></article>`;
    });
    root.innerHTML = `<div class="sec-title">预订核对</div>
      ${!start ? '<p class="booking-guide">补出发日期后可按入住或参观日期核对。 <button type="button" data-booking-date>选日期</button></p>' : ""}
      ${registry ? "" : '<p class="booking-guide">官方来源暂未加载，刷新后可重查。</p>'}
      ${html.join("") || '<div class="empty">没有需要预订的项目</div>'}`;
    root.querySelector("[data-booking-date]")?.addEventListener("click",chooseDate);
    for (const mark of root.querySelectorAll("[data-booking-mark]")) {
      const row = list[Number(mark.dataset.bookingMark)];
      mark.onchange = () => {
        try {
          if (mark.checked) localStorage.setItem(scopeStorage(row),"yes");
          else localStorage.removeItem(scopeStorage(row));
          document.dispatchEvent(new CustomEvent("shorttrip-booking-changed", {detail:scopeStorage(row)}));
        } catch {}
        mark.closest("article").querySelector(".booking-confirmed").hidden = !mark.checked;
      };
    }
  };
})();
