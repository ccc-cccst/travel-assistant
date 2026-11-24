/* ShortTrip 行程助手：左 AI 助手 / 中行程详情 / 右地图。
 * 只通过 window.API（api.js）拿数据、存数据；数据结构见 docs/webui-api.md。 */
(function () {
  "use strict";
  if (location.origin === "http://127.0.0.1:8791") return;
  const API = window.API;
  const params = new URLSearchParams(location.search);
  const comparisonStudy = window.ComparisonStudy?.recognize(params);

  // ======================= 小工具 =======================
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const clone = (o) => JSON.parse(JSON.stringify(o));
  const LS = {
    get(k, d) { try { const v = localStorage.getItem("st.ui." + k); return v ? JSON.parse(v) : d; } catch { return d; } },
    set(k, v) { try { localStorage.setItem("st.ui." + k, JSON.stringify(v)); } catch { /* ignore */ } },
  };

  const P = {
    logo: '<path d="M4 7h11a3 3 0 0 1 0 6H9a3 3 0 0 0 0 6h11"/><path d="M17 4l3 3-3 3"/><path d="M7 16l-3 3 3 3"/>',
    menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
    calendar: '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M8 3v4M16 3v4M4 10h16M8 14h4"/>',
    clock: '<circle cx="12" cy="12" r="8"/><path d="M12 7v5l3 2"/>',
    history: '<path d="M4 6h11M4 12h7M4 18h7"/><circle cx="17" cy="16" r="4"/><path d="M17 14.5V16l1 1"/>',
    download: '<path d="M12 4v11M7 10l5 5 5-5M4 19h16"/>',
    share: '<path d="M14 5l6 6-6 6v-3.5c-5 0-8 1.5-10 5 .5-5.5 3.5-9.5 10-10.5z"/>',
    more: '<circle cx="5" cy="12" r="1.3" fill="currentColor"/><circle cx="12" cy="12" r="1.3" fill="currentColor"/><circle cx="19" cy="12" r="1.3" fill="currentColor"/>',
    sparkle: '<path d="M10 3l1.8 5.2L17 10l-5.2 1.8L10 17l-1.8-5.2L3 10l5.2-1.8z" fill="currentColor" stroke="none"/><path d="M18 14l.8 2.2L21 17l-2.2.8L18 20l-.8-2.2L15 17l2.2-.8z" fill="currentColor" stroke="none"/>',
    collapse: '<path d="M14 10l6-6M14 10V5M14 10h5M10 14l-6 6M10 14v5M10 14H5"/>',
    expand: '<path d="M20 4l-6 6M20 4v5M20 4h-5M4 20l6-6M4 20v-5M4 20h5"/>',
    close: '<path d="M6 6l12 12M18 6L6 18"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    plusCircle: '<circle cx="12" cy="12" r="10" fill="currentColor" stroke="none"/><path d="M12 7.5v9M7.5 12h9" stroke="#fff"/>',
    image: '<rect x="4" y="5" width="16" height="14" rx="2"/><path d="M4 16l4.5-4.5 4 4 2.5-2.5L20 18"/><circle cx="15.5" cy="9.5" r="1.3"/>',
    list: '<path d="M5 7h14M5 12h14M5 17h14"/>',
    arrowDown: '<path d="M12 5v14M6 13l6 6 6-6"/>',
    arrowUp: '<path d="M12 19V5M6 11l6-6 6 6"/>',
    chevronDown: '<path d="M6 9l6 6 6-6"/>',
    chevronRight: '<path d="M9 6l6 6-6 6"/>',
    check: '<path d="M5 12.5l4.5 4.5L19 7"/>',
    route: '<circle cx="6" cy="6" r="2"/><circle cx="18" cy="18" r="2"/><path d="M8 6h7a3 3 0 0 1 0 6H9a3 3 0 0 0 0 6h7"/>',
    walk: '<circle cx="13" cy="4.5" r="1.6" fill="currentColor"/><path d="M10 21l2.5-6.5L15 17v4M9 11.5l2.5-3.5 3 1.5 2 3M12.5 8l-1 6.5"/>',
    car: '<path d="M5 16V12l2-5h10l2 5v4"/><path d="M4 12h16v5H4z"/><circle cx="8" cy="17.5" r="1.4" fill="currentColor"/><circle cx="16" cy="17.5" r="1.4" fill="currentColor"/>',
    metro: '<rect x="6" y="3" width="12" height="14" rx="3"/><path d="M6 11h12M9 21l1.5-3M15 21l-1.5-3"/><circle cx="9" cy="14" r=".8" fill="currentColor"/><circle cx="15" cy="14" r=".8" fill="currentColor"/>',
    bus: '<rect x="5" y="3" width="14" height="15" rx="2"/><path d="M5 11h14M8 21v-3M16 21v-3"/>',
    train: '<rect x="6" y="3" width="12" height="14" rx="4"/><path d="M6 10h12M9 21l2-4M15 21l-2-4"/>',
    plane: '<path d="M3 13l7-1 4-8h2l-2 8 5 .5 2-2.5h1.5l-1 4 1 4H21l-2-2.5-5 .5 2 8h-2l-4-8-7-1z"/>',
    landmark: '<path d="M3 20l6-10 4 6 3-4 5 8z" fill="currentColor" stroke="none"/><circle cx="17" cy="6" r="2" fill="currentColor" stroke="none"/>',
    hotel: '<path d="M3 19V7M3 15h18v4M3 12h18v3"/><circle cx="7.5" cy="10" r="1.6" fill="currentColor"/><path d="M11 12V9h7a3 3 0 0 1 3 3"/>',
    food: '<path d="M7 3v8M5 3v5a2 2 0 0 0 4 0V3M7 11v10M17 3c-2 1-3 3-3 6s1 4 3 4v8"/>',
    shop: '<path d="M5 8h14l-1 12H6z"/><path d="M9 8V6a3 3 0 0 1 6 0v2"/>',
    heart: '<path d="M12 20s-7-4.5-7-10a4 4 0 0 1 7-2.5A4 4 0 0 1 19 10c0 5.5-7 10-7 10z"/>',
    edit: '<path d="M5 19h4l10-10-4-4L5 15z"/><path d="M13 7l4 4"/>',
    move: '<rect x="4" y="5" width="16" height="14" rx="2"/><path d="M9 12h7M13 9l3 3-3 3"/>',
    copy: '<rect x="8" y="8" width="11" height="12" rx="2"/><path d="M5 15V5a1 1 0 0 1 1-1h9"/>',
    trash: '<path d="M5 7h14M10 7V4h4v3M7 7l1 13h8l1-13M10 11v5M14 11v5"/>',
    drag: '<path d="M5 9h14M5 15h14"/>',
    refresh: '<path d="M19 12a7 7 0 1 1-2-4.9"/><path d="M19 4v4h-4"/>',
    fit: '<path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3"/><path d="M6.5 12s2-3.5 5.5-3.5 5.5 3.5 5.5 3.5-2 3.5-5.5 3.5S6.5 12 6.5 12z"/><circle cx="12" cy="12" r="1.4" fill="currentColor"/>',
    layers: '<path d="M12 4l9 5-9 5-9-5z"/><path d="M3 14l9 5 9-5"/>',
    locate: '<circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2" fill="currentColor"/><path d="M12 2v4M12 18v4M2 12h4M18 12h4"/>',
    zoomIn: '<path d="M12 5v14M5 12h14"/>',
    zoomOut: '<path d="M5 12h14"/>',
    send: '<path d="M4 12l16-8-6 16-3-6z"/><path d="M11 14l9-10"/>',
    fire: '<path d="M12 21c-4 0-6.5-2.6-6.5-6 0-3.5 3-5 3.5-9 2 1.5 3 3.5 3 5.5 1-1 1.5-2 1.5-3 2.5 2 4 4.5 4 6.8 0 3.4-2.5 5.7-5.5 5.7z" fill="currentColor" stroke="none"/>',
  };
  const ic = (n, cls = "") => `<svg class="i ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${P[n] || ""}</svg>`;
  const fillIcons = (root = document) => $$("[data-ic]", root).forEach((el) => { if (!el.dataset.icDone) { el.insertAdjacentHTML("afterbegin", ic(el.dataset.ic)); el.dataset.icDone = 1; } });

  const KIND = {
    attraction: { label: "景点", icon: "landmark", glyph: "🏛" },
    hotel: { label: "酒店", icon: "hotel", glyph: "🏨" },
    restaurant: { label: "美食", icon: "food", glyph: "🍜" },
    shopping: { label: "购物", icon: "shop", glyph: "🛍" },
    airport: { label: "机场", icon: "plane", glyph: "✈️" },
    station: { label: "火车站", icon: "train", glyph: "🚄" },
    fav: { label: "收藏", icon: "heart", glyph: "♥" },
  };
  const TYPE_CN = { attraction: "景点", breakfast: "早餐", lunch: "午餐", dinner: "晚餐", accommodation: "住宿", train: "火车", airplane: "飞机" };
  const MODE = { walk: ["walk", "步行"], taxi: ["car", "打车"], car: ["car", "驾车"], metro: ["metro", "地铁"], bus: ["bus", "公交"] };
  const PIN_COLOR = "#4a7dff";
  const DAY_COLORS = ["#2f6bff", "#13a8a8", "#fa8c16", "#8a5cff", "#eb2f96", "#52c41a"];
  const dayColor = (d) => DAY_COLORS[(d - 1) % DAY_COLORS.length];

  function toast(msg, ms = 1800) {
    const t = $("#toast"); t.textContent = msg; t.hidden = false;
    clearTimeout(toast.h); toast.h = setTimeout(() => (t.hidden = true), ms);
  }

  // ======================= 状态 =======================
  const state = {
    groups: [],
    g: 0, v: 0,                 // 当前行程组 / 版本；g = -1 表示“新建行程”空白页
    sec: "overview",            // 中栏当前位置：overview | day-N | pending | note（滚动联动）
    tab: "detail",
    view: LS.get("view", "card"),
    filters: new Set(LS.get("filters", [])),
    rateMin: LS.get("rateMin", 0),
    sel: new Set(),             // 多选的 item uid
    hl: null,                   // 高亮 item uid
    openLegs: new Set(),
    chat: LS.get("chat", {}),   // 页面里追加的对话，按行程组 id 存
    jobs: {},                   // 进行中的规划任务，按组 id（新建用 "_new"）
    favs: [],
    revision: null,
  };
  const group = () => state.groups[state.g];
  const baseTrip = () => group()?.versions[state.v] || null;
  const trip = () => state.revision || baseTrip();
  const reviewDates = new Map();
  const reviewDateKey = owner => "st.reviewDate:" + owner.id;
  function reviewDate(t, owner) {
    if (t.startDate) return t.startDate;
    if (reviewDates.has(owner.id)) return reviewDates.get(owner.id);
    try { return sessionStorage.getItem(reviewDateKey(owner)) || ""; }
    catch { return ""; }
  }
  function setReviewDate(owner, date) {
    reviewDates.set(owner.id, date);
    try {
      if (date) sessionStorage.setItem(reviewDateKey(owner), date);
      else sessionStorage.removeItem(reviewDateKey(owner));
    } catch { /* Session storage is optional. */ }
  }
  const acceptedEdits = new WeakMap();
  const saveTimers = new WeakMap();

  // 所有 item（含待安排）的位置索引
  function locate(uid) {
    const t = trip(); if (!t) return null;
    for (let di = 0; di < t.days.length; di++) {
      const i = t.days[di].items.findIndex((x) => x.uid === uid);
      if (i >= 0) return { list: t.days[di].items, di, i, item: t.days[di].items[i] };
    }
    const i = t.pending.findIndex((x) => x.uid === uid);
    return i >= 0 ? { list: t.pending, di: -1, i, item: t.pending[i] } : null;
  }

  // 编辑后保存：标记手动修改 → 重绘 → 防抖调后端
  function commit(msg, keepEdited = false) {
    const t = trip();
    const owner = group(), version = state.v;
    const before = acceptedEdits.get(t);
    if (t.archive) {
      if (before) owner.versions[version] = before;
      resetView(state.sec);
      return;
    }
    if (before) {
      const oldIssues = new Map(scheduleIssues(before).map(issue => [issue.key, issue.minutes]));
      const added = scheduleIssues(t).find(issue => issue.minutes > (oldIssues.get(issue.key) ?? 0));
      const locked = changedFixedVisit(before, t);
      if (added || locked) {
        owner.versions[version] = before;
        acceptedEdits.set(before, clone(before));
        state.sec = "overview"; state.sel.clear();
        renderTop(); renderChips(); renderPlan(); drawRoute(true);
        toast((locked ? `「${locked.name}」已锁定。请先解除锁定` : added.text) + "。已保留原安排。", 4400);
        return;
      }
      invalidateChangedLegs(before, t);
    }
    if (!keepEdited) t.edited = true;
    const snapshot = clone(t);
    acceptedEdits.set(t, snapshot);
    renderPlan(); drawRoute(false); renderChips();
    if (msg) toast(msg);
    clearTimeout(saveTimers.get(t));
    saveTimers.set(t, setTimeout(async () => {
      try {
        const saved = await API.saveTrip(snapshot);
        if (saved && saved !== snapshot && saved.id === t.id && owner.versions[version] === t &&
            acceptedEdits.get(t) === snapshot) {
          owner.versions[version] = saved;
          acceptedEdits.set(saved, clone(saved));
          if (group() === owner && state.v === version) { renderPlan(); drawRoute(false); }
        }
      } catch (e) { toast("保存失败：" + e.message, 3000); }
    }, 600));
  }

  const clock = value => typeof value === "string" && /^(?:[01]\d|2[0-3]):[0-5]\d$|^24:00$/.test(value)
    ? +value.slice(0, 2) * 60 + +value.slice(3) : null;
  const fixedLabel = {reservation: "预约已锁定", departure: "返程已锁定"};

  function changedFixedVisit(before, after) {
    const current = new Map(after.days.flatMap(day => day.items.map(item => [item.uid, {day:day.day,item}])));
    for (const day of before.days) for (const item of day.items) {
      if (!Object.hasOwn(fixedLabel, item.lock)) continue;
      const next = current.get(item.uid);
      if (!next || next.day !== day.day || next.item.start !== item.start || next.item.end !== item.end ||
          next.item.place !== item.place) return item;
    }
    return null;
  }

  function scheduleIssues(t) {
    const issues = [];
    for (const day of t.days) {
      let previous;
      for (const item of day.items) {
        const start = clock(item.start), end = clock(item.end);
        if (start == null || end == null) continue;
        if (previous && start < clock(previous.end)) {
          issues.push({key: previous.uid + ":" + item.uid, day: day.day, minutes: clock(previous.end) - start,
            text: `第${day.day}天「${item.name}」${item.start} 开始，早于上一站「${previous.name}」${previous.end} 结束`});
        }
        previous = item;
      }
    }
    return issues;
  }

  function invalidateChangedLegs(before, after) {
    const predecessors = new Map(before.days.flatMap(day =>
      day.items.map((item, i) => [item.uid, day.items[i - 1]?.uid])));
    for (const day of after.days) for (const [i, item] of day.items.entries()) {
      if (predecessors.get(item.uid) === day.items[i - 1]?.uid) continue;
      delete item.leg; delete item.legChoice; delete item.savedRoute;
      if (item.raw) item.raw.transports = [];
    }
  }

  function tripFacts(t) {
    const facts = {travel: 0, walk: 0, sight: 0, unknown: 0, missingTime: 0, issues: scheduleIssues(t).map(issue => issue.text)};
    for (const day of t.days) for (const [i, item] of day.items.entries()) {
      const start = clock(item.start), end = clock(item.end);
      if (start == null || end == null) facts.missingTime++;
      if (item.kind === "attraction" && start != null && end != null && end >= start) facts.sight += end - start;
      if (!i) continue;
      const previous = day.items[i - 1], option = legInfo(previous, item)?.chosen;
      const fullPath = hasPath(option?.path) || (option?.segments?.length && option.segments.every(s => hasPath(s.path)));
      if (!fullPath || !Number.isFinite(option.minutes)) { facts.unknown++; continue; }
      facts.travel += option.minutes;
      const walking = hasPath(option.path)
        ? option.mode === "walk" ? option.km : 0
        : (option.segments || []).filter(s => s.mode === "walk" && hasPath(s.path)).reduce((sum, s) => sum + (s.km || 0), 0);
      facts.walk += walking || 0;
      const last = clock(previous.end);
      if (start != null && last != null && start >= last && start - last < option.minutes) {
        facts.issues.push(`第${day.day}天「${previous.name} → ${item.name}」按道路估算还差约 ${option.minutes - (start - last)} 分钟`);
      }
    }
    return facts;
  }

  function renderReviewBar(t) {
    $("#tripReviewBar").hidden = !t;
    $("#mobilePlanFeedback").hidden = !group()?.canEdit;
    if (!t) return;
    $("#reviewBtn").textContent = "出发前核对";
    if (t.archive) {
      $("#reviewCount").textContent = t.archive.referencePreview
        ? "已按快照试排 · 班次与价格为参考" : "已按旅行快照核验 · 价格为参考";
      $("#compareBtn").textContent = "比较方案";
      $("#compareBtn").hidden = group().versions.length < 2 || Boolean(t.revision);
      return;
    }
    $("#compareBtn").textContent = "比较版本";
    const facts = tripFacts(t);
    $("#reviewCount").textContent = [
      facts.issues.length ? `${facts.issues.length} 处时间需核对` : "",
      facts.unknown ? `${facts.unknown} 段线路待核实` : "",
      facts.missingTime ? `${facts.missingTime} 项时刻待定` : ""
    ].filter(Boolean).join(" · ");
    $("#compareBtn").hidden = group().versions.length < 2;
  }

  const verificationLabels = {
    current_ticket_and_room_availability: "出行日的余票和余房",
    date_specific_prices_and_opening: "出行日的票价、房价与开放时间",
    inside_attraction_walking: "景区内部的步行时间",
    actual_room_type_and_occupancy: "实际房型与入住人数",
    calendar_start_date: "出发日的具体日期",
    weekend_date: "周末的具体日期和当天班次",
    specific_experience_departure: "夜游的当天船班或预约时段",
  };
  function openArchiveReview(day, itemId) {
    const t = trip(), owner = group(), pending = t.archive.pending;
    const keyItems = t.days.flatMap(day => day.items.filter(item => item.lock || item.code).map(item =>
      `<li>第 ${day.day} 天 · ${esc(item.start || "")} ${esc(item.name)}${item.code ? " · " + esc(item.code) : ""}</li>`));
    openModal(`<div class="fact-review-head"><h3>出发前核对</h3>
        <button type="button" class="ib" data-close aria-label="关闭出发前核对">${ic("close")}</button></div>
        <dl class="fact-meaning"><dt>已核验</dt><dd>时间、接驳和硬约束已按旅行快照检查。</dd>
          <dt>参考价</dt><dd>按行程人数和住宿安排估算。实际票价和房价需核对。</dd>
          <dt>待核实</dt><dd>请在出发前确认下方列出的事项。预订记录可在「预订管理」查看。</dd></dl>
        ${t.archive.referencePreview ? "<p>这份行程可使用历史铁路班次和票价。厦门住宿可能按历史展示价估算每间每晚费用，实际房型、入住人数和报价请向酒店核对。</p>" : ""}
        ${pending.length ? `<details class="compare-metrics" ${itemId ? "" : "open"}><summary>仍需核实 · ${pending.length} 项</summary>
          <ul class="check-list">${pending.map(key => `<li>${esc(verificationLabels[key] || key)}</li>`).join("")}</ul></details>` : ""}
        <section id="dayFacts" class="day-facts" aria-label="地点营业"></section>
        ${keyItems.length ? `<details class="compare-metrics"><summary>预约与城际交通时刻</summary><ul class="check-list">${keyItems.join("")}</ul></details>` : ""}
        <div class="btns"><button data-close>返回行程</button></div>`);
    mountDayFacts($("#dayFacts"), t, day, {
      itemId, initialDate: reviewDate(t, owner),
      isCurrent: () => !$("#modal").hidden && trip() === t,
      saveDate: date => {
        setReviewDate(owner, date);
        renderTop();
        renderPlan();
      },
    });
  }
  $("#reviewBtn").onclick = () => {
    if (trip()?.archive) {
      return openArchiveReview(activeDay());
    }
    const facts = tripFacts(trip());
    openModal(`<h3>出发前核对</h3>
      ${facts.issues.length ? `<ul class="check-list">${facts.issues.map(s => `<li>${esc(s)}</li>`).join("")}</ul>` : "<p>已知时刻与道路参考未发现衔接冲突。</p>"}
      ${facts.unknown ? `<p>还有 ${facts.unknown} 段线路待核实，可点时间线的「高德」。</p>` : ""}
      ${facts.missingTime ? `<p>${facts.missingTime} 项活动的时刻待定。</p>` : ""}
      <p>班次、门票和营业时间请按出行日期确认。</p><div class="btns"><button class="pri" data-close>返回行程</button></div>`);
  };

  $("#compareBtn").onclick = () => {
    const versions = group().versions, right = state.v;
    if (trip()?.archive) return compareArchived(versions, right);
    const options = versions.map((_, i) => `<option value="${i}" ${i === right ? "disabled" : ""}>第 ${i + 1} 版</option>`).join("");
    openModal(`<h3>比较行程版本</h3><label class="compare-select">参照版本 <select id="compareVersion">${options}</select></label>
      <div id="versionMetrics"></div><p class="compare-note">费用取自原规划。接驳和步行只汇总有路线参考的路段。</p>
      <div class="btns"><button class="pri" data-close>返回行程</button></div>`);
    const select = $("#compareVersion");
    select.value = String(right === 0 ? 1 : right - 1);
    const numbers = new Intl.NumberFormat("zh-CN", {maximumFractionDigits: 1});
    const cost = t => !t.edited && Number.isFinite(t.totalCost) ? `约 ¥${numbers.format(t.totalCost)}` : "待核实";
    const road = (value, unknown, unit) => unknown
      ? `${value ? numbers.format(value) + " " + unit + " · " : ""}${unknown} 段待核实` : `${numbers.format(value)} ${unit}`;
    function renderMetrics() {
      const a = versions[+select.value], b = versions[right];
      const x = tripFacts(a), y = tripFacts(b);
      const rows = [
        ["日期与人数", `${a.days.length} 天 · ${a.people} 人`, `${b.days.length} 天 · ${b.people} 人`],
        ["全员预计费用", cost(a), cost(b)],
        ["已知道路接驳", road(x.travel, x.unknown, "分钟"), road(y.travel, y.unknown, "分钟")],
        ["已知接驳步行", road(x.walk, x.unknown, "公里"), road(y.walk, y.unknown, "公里")],
        ["已排景点游览", `${x.sight} 分钟`, `${y.sight} 分钟`],
        ["需核对的时间", `${x.issues.length} 处`, `${y.issues.length} 处`]
      ];
      $("#versionMetrics").innerHTML = `<table class="version-table"><thead><tr><th scope="col">指标</th><th scope="col">第 ${+select.value + 1} 版</th><th scope="col">当前第 ${right + 1} 版</th></tr></thead>
        <tbody>${rows.map(([label, first, second]) => `<tr><th scope="row">${label}</th><td>${esc(first)}</td><td>${esc(second)}</td></tr>`).join("")}</tbody></table>`;
    }
    select.onchange = renderMetrics;
    renderMetrics();
  };

  const archiveMetricRows = PlanDifferences.rows;
  const goalStatus = {matched:"已满足", unmet:"未满足", insufficient_evidence:"待核实", measured:"已纳入比较"};
  const goalReason = {ambiguous_reference:"需要确认具体地点", matching_places_not_scheduled:"本方案没有排入对应体验"};
  const readableGoal = goal => (goalStatus[goal.status] || "待核实") +
    (goalReason[goal.reason] ? " · " + goalReason[goal.reason] : "");
  const archiveLabel = v => v.revision ? "已采用版本" : v.archive.recommended ? "首推" : v.archive.label;
  function renderArchiveOptions(t) {
    const options = $("#archiveOptions");
    const versions = t?.archive ? group().versions : [];
    options.hidden = !versions.length;
    const choices = versions.map((v,i) => {
      const cost = v.archive.metrics?.total_cost_cny;
      const suffix = Number.isFinite(cost) ? " · 约 ¥" + Math.round(cost).toLocaleString("zh-CN") : "";
      return `<option value="${i}" ${state.v === i && !t.revision ? "selected" : ""}>${esc(archiveLabel(v) + suffix)}</option>`;
    });
    options.innerHTML = (versions.length > 1 ? `<label class="archive-choice">当前方案
      <select id="archiveChoice">${t.revision ? '<option value="" selected disabled>切换至原方案…</option>' : ""}${choices.join("")}</select>
      </label>` : "") + (t?.revision ? '<span class="current-revision">已采用版本</span>' :
      versions.length === 1 ? '<span class="compare-note">当前只有一份通过核验并公开的方案。</span>' : "");
  }
  $("#archiveOptions").onchange = event => {
    if (event.target.id === "archiveChoice" && event.target.value !== "") selectVersion(Number(event.target.value));
  };
  function compareArchived(versions, current, study, preferred, labelFor = archiveLabel) {
    const a = versions[current], other = preferred ?? (current === 0 ? 1 : 0);
    const options = versions.map((v, i) => i === current ? "" :
      `<option value="${i}">${esc(labelFor(v))}</option>`).join("");
    const tableFirst = study?.display === "table";
    openModal(`<h3>比较行程方案</h3><label class="compare-select">对比方案
      <select id="archiveCompare" ${study ? "disabled" : ""}>${options}</select></label>
      <div id="archiveTakeaways" aria-live="polite" ${tableFirst ? "hidden" : ""}></div>
      <details class="compare-metrics" ${tableFirst ? "open" : ""}><summary>查看完整数据</summary><div id="archiveDelta"></div>
      <p class="compare-note">步行仅统计站点之间的接驳。金额为同一旅行快照下的整团参考价。</p></details>
      <div id="archiveDayDelta"></div>
      ${study ? "" : '<div class="btns"><button data-close>关闭</button><button id="openCompared" class="pri">查看对比方案</button></div>'}`);
    const select = $("#archiveCompare");
    select.value = String(other);
    const dayRoute = day => {
      let transit = 0, walk = 0;
      for (const item of day.items) {
        for (const segment of item.leg?.segments || []) {
          transit += segment.minutes;
          if (segment.mode === "walk") walk += segment.minutes;
        }
      }
      return {transit, walk};
    };
    function render() {
      const b = versions[+select.value];
      const comparison = PlanDifferences.compare(a, b);
      if (!comparison) {
        $("#archiveTakeaways").hidden = false;
        $("#archiveTakeaways").textContent = "两份方案的需求、出发日期或数据版本不同，费用和时长请分别查看。";
        $("#archiveDelta").replaceChildren(); $("#archiveDayDelta").replaceChildren();
        return;
      }
      const rows = comparison.changes.map(({label, before:x, after:y, format:fmt, change:delta}) => {
        return [label, Number.isFinite(x) ? fmt(x) : "待核实", Number.isFinite(y) ? fmt(y) : "待核实",
                delta == null ? "—" : delta === 0 ? "相同" : (delta > 0 ? "+" : "−") + fmt(Math.abs(delta))];
      });
      const goalKey = goal => goal.requirement_id || goalValue(goal.value);
      const labels = new Map([...a.archive.goals, ...b.archive.goals].map(goal => [goalKey(goal), goalValue(goal.value)]));
      for (const [key, label] of labels) {
        const from = a.archive.goals.find(g => goalKey(g) === key);
        const to = b.archive.goals.find(g => goalKey(g) === key);
        rows.push([label, from ? readableGoal(from) : "—", to ? readableGoal(to) : "—", ""]);
        if (from?.status === "unmet" && to?.status === "matched") {
          const places = (to.matched_places || []).slice(0, 2).map(place => place.name).filter(Boolean);
          comparison.benefits.unshift({line:"满足「" + label + "」偏好" + (places.length ? "，安排了" + places.join("、") : ""),
            priority:"在意" + label});
        }
        if (from?.status === "matched" && to?.status === "unmet") comparison.tradeoffs.unshift({line:"未满足「" + label + "」偏好"});
      }
      const list = entries => entries.length ? `<ul>${entries.map(({line}) => `<li>${esc(line)}</li>`).join("")}</ul>` : "";
      const priorities = [...new Set(comparison.benefits.slice(0, 2).map(entry => entry.priority))];
      if (!tableFirst) $("#archiveTakeaways").innerHTML = (window.AtlasView?.comparisonHero(comparison, a, b) || "") + `<div class="decision-summary">
        <p class="decision-context">${esc(labelFor(b))} 相比 ${esc(labelFor(a))}</p>
        ${comparison.benefits.length ? `<div><strong>优势</strong>${list(comparison.benefits)}</div>` : ""}
        ${comparison.tradeoffs.length ? `<div><strong>代价</strong>${list(comparison.tradeoffs)}</div>` : ""}
        ${priorities.length ? `<p>适合${esc(priorities.join("、"))}的旅行者。</p>` : ""}
        ${!comparison.benefits.length && !comparison.tradeoffs.length ? "<p>已记录的费用、接驳、游览和偏好指标没有差异。可以查看每天的具体安排。</p>" : ""}
        ${comparison.changes.some(row => row.change === null) ? '<p class="compare-note">部分指标资料不足，详情中标为待核实。</p>' : ""}</div>`;
      $("#archiveDelta").innerHTML = `<table class="version-table"><thead><tr><th scope="col">指标</th>
        <th scope="col">${esc(labelFor(a))}</th><th scope="col">${esc(labelFor(b))}</th><th scope="col">差值</th></tr></thead>
        <tbody>${rows.map(([label, x, y, delta]) => `<tr><th scope="row">${esc(label)}</th><td>${esc(x)}</td><td>${esc(y)}</td><td>${esc(delta)}</td></tr>`).join("")}</tbody></table>`;
      const changes = b.days.flatMap(day => {
        const previous = a.days.find(entry => entry.day === day.day);
        if (!previous) return [];
        const stops = day.items.filter(item => item.kind === "attraction").map(item => item.name);
        const originalStops = previous.items.filter(item => item.kind === "attraction").map(item => item.name);
        const added = stops.filter(name => !originalStops.includes(name));
        const removed = originalStops.filter(name => !stops.includes(name));
        const before = dayRoute(previous), after = dayRoute(day);
        if (before.walk === after.walk && before.transit === after.transit && !added.length && !removed.length) return [];
        return [{day: day.day, before, after, added, removed}];
      });
      const pending = [...new Set([...a.archive.pending, ...b.archive.pending])];
      $("#archiveDayDelta").innerHTML = `
        ${comparison.costChanges.length ? `<details class="compare-metrics"><summary>费用分项变化</summary><ul>
          ${comparison.costChanges.map(({label, change}) => `<li>${esc(label + (change > 0 ? " +" : " −") + comparison.money(Math.abs(change)))}</li>`).join("")}</ul></details>` : ""}
        ${changes.length ? `<h4>每天有什么变化</h4><ul class="check-list">${changes.map(day => `<li>
          ${study ? `<strong>第 ${day.day} 天</strong>` :
            `<button class="link-btn" type="button" data-compare-day="${day.day}">查看第 ${day.day} 天</button>`}
          ${day.before.transit !== day.after.transit ? ` · 接驳 ${day.before.transit} → ${day.after.transit} 分钟` : ""}
          ${day.before.walk !== day.after.walk ? ` · 接驳步行 ${day.before.walk} → ${day.after.walk} 分钟` : ""}
          ${day.added.length ? `<div>新增 ${esc(day.added.join("、"))}</div>` : ""}
          ${day.removed.length ? `<div>移出 ${esc(day.removed.join("、"))}</div>` : ""}</li>`).join("")}</ul>` : ""}
        ${pending.length ? `<details class="compare-note"><summary>出发前需核实</summary>
          <ul>${pending.map(key => `<li>${esc(verificationLabels[key] || key)}</li>`).join("")}</ul></details>` : ""}`;
      window.AtlasView?.finishComparison();
    }
    $("#archiveDayDelta").onclick = event => {
      const button = event.target.closest("[data-compare-day]");
      if (!button) return;
      closeModal();
      selectVersion(+select.value, "day-" + button.dataset.compareDay);
    };
    select.onchange = render;
    if (!study) $("#openCompared").onclick = () => { closeModal(); selectVersion(+select.value); };
    render();
  }

  // ======================= 顶栏 =======================
  function renderTop() {
    const t = trip();
    renderTransferReview(t, group());
    const archive = Boolean(t?.archive);
    $("#layout").classList.toggle("archive-view", archive);
    $("#sampleMode").textContent = group()?.preview ? "待确认预览" : group()?.jobId ? "个性化规划" : archive ? "规划归档" : "示例行程";
    $("#sampleMode").hidden = !archive && (API.mode !== "mock" || online.isActive());
    $("#surveyBtn").textContent = group()?.canEdit ? "评价方案" : "😆 满意度调研";
    $("#askForm").hidden = archive ? !group()?.canEdit : online.isActive();
    $("#askInput").placeholder = archive ? "例如：第三天午餐换一家本帮菜" : "说说你的旅行需求";
    $("#historyBtn").hidden = archive;
    $("#moreBtn").hidden = archive && !group()?.canEdit;
    $("#addDay").hidden = archive;
    $("#title").title = archive ? "" : "点击修改标题";
    $("#title").textContent = t ? t.title : "新建行程";
    const ds = archive ? reviewDate(t, group()) : t?.startDate;
    $("#dateText").textContent = ds ? `${+ds.slice(5, 7)}月${+ds.slice(8, 10)}日出发` : archive ? "补出发日期" : "设置出发日期";
    $("#dateBtn").classList.toggle("set", !!ds);
    $("#dateBtn").hidden = !t;
    $("#dateBtn").disabled = Boolean(archive && t.startDate);
    if (window.AtlasView) {
      AtlasView.render(t, group(), state.v, {
        select: selectVersion,
        compare: (reference, index, labelFor) => compareArchived(group().versions, reference, null, index, labelFor),
        labelFor: archiveLabel,
      });
    } else renderArchiveOptions(t);
  }

  $("#title").addEventListener("click", () => {
    if (!trip() || trip().archive) return;
    const h = $("#title"); h.contentEditable = "true"; h.focus();
    document.getSelection().selectAllChildren(h);
  });
  $("#title").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); e.target.blur(); } });
  $("#title").addEventListener("blur", () => {
    const h = $("#title"); h.contentEditable = "false";
    const v = h.textContent.trim();
    if (trip() && v && v !== trip().title) { trip().title = v; group().title = v; commit("已修改标题"); renderDrawer(); }
    else renderTop();
  });

  $("#dateBtn").addEventListener("click", (e) => {
    const t = trip();
    if (t.archive) {
      openArchiveReview(activeDay());
      return $("#dayFacts .facts-date").focus();
    }
    openPop(e.currentTarget, `<div class="ph">出发日期</div><input type="date" id="dateInput" value="${t.startDate || ""}">
      ${t.startDate ? `<button class="mi-row danger" data-act="clearDate">${ic("trash")}清除日期</button>` : ""}`);
    const inp = $("#dateInput"); inp.focus();
    inp.addEventListener("change", () => { t.startDate = inp.value || null; closePop(); renderTop(); commit("已设置出发日期"); });
  });

  // 满意度调研
  $("#surveyTipX").addEventListener("click", (e) => { e.stopPropagation(); $("#surveyTip").hidden = true; LS.set("tipClosed", true); });
  $("#surveyBtn").addEventListener("click", () => {
    $("#surveyTip").hidden = true; LS.set("tipClosed", true);
    if (group()?.canEdit) return openPlanFeedback();
    let score = 0;
    openModal(`<h3>满意度调研</h3><div class="scores">${["😣", "🙁", "😐", "🙂", "😆"].map((e, i) => `<button data-s="${i + 1}">${e}</button>`).join("")}</div>
      <textarea id="fbText" placeholder="说说你的使用体验（可选）"></textarea>
      <div class="btns"><button data-close>取消</button><button class="pri" id="fbSend">提交</button></div>`);
    $("#modalBox").addEventListener("click", (e) => {
      const b = e.target.closest("[data-s]");
      if (b) { score = +b.dataset.s; $$(".scores button").forEach((x) => x.classList.toggle("on", x === b)); }
    });
    $("#fbSend").addEventListener("click", async () => {
      if (!score) return toast("先选一个表情吧");
      await API.submitFeedback({ score, text: $("#fbText").value, tripId: trip() && trip().id });
      closeModal(); toast("感谢反馈！");
    });
  });

  function openPlanFeedback() {
    const t = trip();
    const target = {job:group().jobId, candidate:t.id, revision:t.revision?.id || null};
    openModal(`<h3>这个行程你会采用吗？</h3>
      <p>${esc(archiveLabel(t))} · ${esc(t.title)}</p>
      <form id="planFeedback"><fieldset>
      ${[["use","直接采用"],["minor_edit","小改后采用"],["replan","需要重排"],["need_evidence","资料不足"]].map(([key, label]) =>
        `<label><input type="radio" name="decision" value="${key}" required> ${label}</label>`).join("")}
      </fieldset>
      <label for="planFeedbackText">你想保留或修改什么？（可选）</label>
      <textarea id="planFeedbackText" name="text" rows="3" maxlength="1000"></textarea>
      <p id="planFeedbackStatus" role="status"></p>
      <div class="btns"><button type="button" data-close>取消</button><button type="submit" class="pri">保存评价</button></div></form>`);
    const form = $("#planFeedback");
    form.onsubmit = async event => {
      event.preventDefault();
      const submit = form.querySelector("[type=submit]");
      const data = new FormData(form);
      submit.disabled = true;
      try {
        await API.personalized("feedback", {...target, decision:data.get("decision"), text:data.get("text")});
        if ($("#planFeedback") === form && !$("#modal").hidden) { closeModal(); toast("评价已保存"); }
      } catch (error) {
        if ($("#planFeedback") === form) $("#planFeedbackStatus").textContent = error.message;
      } finally { submit.disabled = false; }
    };
  }
  $("#mobilePlanFeedback").onclick = openPlanFeedback;

  $("#historyBtn").addEventListener("click", (e) => {
    const g = group(); if (!g) return;
    openPop(e.currentTarget, `<div class="ph">历史版本</div>` + g.versions.map((v, i) =>
      `<button class="mi-row" data-ver="${i}">${i === state.v ? ic("check") : '<svg class="i"></svg>'}第 ${i + 1} 版<span class="r">${esc((v.query || "").slice(0, 12))}${(v.query || "").length > 12 ? "…" : ""}</span></button>`).join(""));
  });

  $("#downloadBtn").addEventListener("click", (e) => {
    if (!trip()) return;
    openPop(e.currentTarget, `<button class="mi-row" data-act="dlView">${ic("download")}下载行程（页面数据 JSON）</button>
      <button class="mi-row" data-act="dlRaw">${ic("download")}下载 ChinaTravel 格式 JSON</button>
      <button class="mi-row" data-act="print">${ic("image")}打印 / 存为 PDF</button>`);
  });

  $("#shareBtn").addEventListener("click", () => {
    const t = trip(); if (!t) return;
    const link = new URL(location.pathname, location.origin);
    for (const key of ["job","request","trip","candidate","revision","day","preview"]) {
      const current = new URLSearchParams(location.search).get(key);
      if (current) link.searchParams.set(key,current);
    }
    openModal(`<h3>分享当前行程</h3><p>下载逐日行程，文件会保留现在的地点顺序和时间，可发给同行者。</p>
      <label for="shareLocalLink">本机行程链接</label>
      <input id="shareLocalLink" readonly value="${esc(link.href)}">
      <div class="btns"><button data-close>关闭</button><button id="downloadShare" class="pri">下载行程 JSON</button></div>`);
    $("#shareLocalLink").onclick = event => event.currentTarget.select();
    $("#downloadShare").onclick = () => download(`${t.id}.itinerary.json`, {
      title: t.title, city: t.city, startDate: t.startDate, days: t.days.map(day => ({
        day: day.day, title: day.title, items: day.items.map(item => ({
          name: item.name, place: item.place, start: item.start, end: item.end,
          kind: item.kind, hours: item.hours, code: item.code, lock: item.lock
        }))
      }))
    });
  });

  $("#moreBtn").addEventListener("click", (e) => {
    if (group()?.canEdit) {
      openPop(e.currentTarget, `<button class="mi-row" data-act="archiveHistory">${ic("history")}修改记录</button>`);
      return;
    }
    openPop(e.currentTarget, `<button class="mi-row" data-act="dupVersion">${ic("copy")}复制为新版本</button>
      <button class="mi-row" data-act="resetLocal">${ic("refresh")}撤销本地所有修改</button>
      <div class="sep"></div><div class="ph">数据接口：${API.mode === "http" ? "后端 " + esc(params.get("api")) : "本地示例（mock）"}</div>`);
  });

  function download(name, obj) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([JSON.stringify(obj, null, 1)], { type: "application/json" }));
    a.download = name; a.click();
  }
  // 页面数据 → ChinaTravel 行程 JSON（顺序/删除/替换会体现；时间未重算）
  function toChinaTravel(t) {
    const strip = (a) => Object.fromEntries(Object.entries(a).filter(([k]) => !k.startsWith("_")));
    return {
      ...(t.raw ? Object.fromEntries(Object.entries(t.raw).filter(([k]) => !k.startsWith("_") && k !== "itinerary")) : {}),
      people_number: t.people, start_city: t.startCity, target_city: t.city,
      itinerary: t.days.map((d) => ({ day: d.day, activities: d.items.map((x) => strip({ ...x.raw, position: x.place, transports: (x.raw.transports || []).map(strip) })) })),
    };
  }

  // 抽屉里的旧任务在展开时补读真实行程资料。
  const drawerRuns = new Map();
  const drawerModels = {deepseek:"DeepSeek",qwen:"本地 Qwen",program:"表单规划"};
  function drawerDetails(t, run) {
    const date = t?.startDate || run?.startDate;
    const people = t?.people || run?.people;
    const provider = t?.archive?.modelProvider || run?.provider;
    const cost = t?.archive?.metrics?.total_cost_cny ?? t?.totalCost;
    const stops = [...new Set(t?.days.flatMap(d => d.items.filter(x => x.kind === "attraction").map(x => x.name)) || [])].slice(0,2);
    const meta = [
      date ? date.replaceAll("-", "/") + " 出发" : "",
      people ? people + " 人" : "",
      Number.isFinite(cost) ? "约 ¥" + Math.round(cost).toLocaleString("zh-CN") : "",
      drawerModels[provider],
    ].filter(Boolean);
    return {meta:meta.join(" · "), highlights:stops.join(" / ")};
  }
  function drawerCard(title, detail, badge = "") {
    return `<span class="drawer-trip-name">${esc(title)}</span><small class="drawer-trip-meta">${esc(detail.meta)}</small>
      <small class="drawer-trip-stops">${esc(detail.highlights)}</small>${badge ? `<small class="drawer-trip-ref">${esc(badge)}</small>` : ""}`;
  }
  async function readDrawerRun(run) {
    const key = [run.job,run.candidate,run.revision,run.preview].join("/");
    if (!drawerRuns.has(key)) {
      const read = run.revision ? API.itineraryEdit("state", null, {id:run.revision}, AbortSignal.timeout(4500)) :
        API.personalized("job", null, {id:run.job}, AbortSignal.timeout(4500));
      const pending = read.then(value => {
        const raw = (run.preview && value.previews?.find(item => item.id === run.preview)?.group) || value.group;
        const version = raw?.versions.find(item => item.id === run.candidate) ||
          raw?.versions.find(item => item.id === raw.recommendedId) || raw?.versions[0];
        if (!version) drawerRuns.delete(key);
        return version ? API.fromChinaTravel(version) : null;
      }).catch(() => { drawerRuns.delete(key); return null; });
      drawerRuns.set(key,pending);
    }
    return drawerRuns.get(key);
  }
  async function addDrawerDetails(entries) {
    for (const {run, row} of entries) {
      if (!row.isConnected || $("#drawer").hidden) return;
      const loaded = await readDrawerRun(run);
      if (!row.isConnected || !loaded) continue;
      const detail = drawerDetails(loaded,run);
      row.querySelector(".drawer-trip-meta").textContent = detail.meta;
      row.querySelector(".drawer-trip-stops").textContent = detail.highlights;
    }
  }
  function renderDrawer() {
    const list = $("#drawerList"), current = group();
    const recent = API.hasPersonalized ? LS.get("planningJobs", []) : [];
    const imported = browserHistory?.links() || [];
    const url = new URLSearchParams(location.search);
    if (current?.jobId && !recent.some(run => run.job === current.jobId)) {
      recent.unshift({job:current.jobId, title:current.title,
        ...Object.fromEntries(["request","candidate","revision","day","preview"].map(key => [key,url.get(key)]))});
    }
    const entries = [...recent, ...imported];
    const titleCount = new Map();
    for (const run of entries) titleCount.set(run.title,(titleCount.get(run.title) || 0) + 1);
    const rows = entries.map((run, index) => {
      const isCurrent = current?.jobId === run.job && (run.candidate || baseTrip()?.id) === baseTrip()?.id &&
        (run.revision || "") === (trip()?.revision?.id || "");
      const link = new URLSearchParams({job:run.job});
      for (const key of ["request","candidate","revision","day","preview"]) if (run[key]) link.set(key,run[key]);
      if (isCurrent && !run.candidate) {
        for (const key of ["candidate","revision","day","preview"]) if (url.has(key)) link.set(key,url.get(key));
      }
      const badge = [index >= recent.length ? "从旧版迁入" : "", run.revision ? "已修改" : "",
        titleCount.get(run.title) > 1 ? "任务 " + run.job.slice(-6).toUpperCase() : ""].filter(Boolean).join(" · ");
      return `<a class="drawer-trip recent-plan" data-drawer-run="${index}" href="?${esc(link)}" ${isCurrent ? 'aria-current="page"' : ""}>
        ${drawerCard(run.title,drawerDetails(isCurrent ? trip() : null,run),badge || (isCurrent ? "当前行程" : ""))}</a>`;
    });
    const examples = state.groups.map((g,i) => g.jobId ? "" : `<button class="drawer-trip ${i === state.g ? "on" : ""}" data-g="${i}">
      ${drawerCard(g.title, drawerDetails(g.versions.find(v => v.archive?.recommended) || g.versions.at(-1)),
        g.archive ? "已核验归档" : g.versions.length + " 个示例版本")}</button>`).join("");
    const examplesOpen = list.querySelector("details")?.open ?? (!recent.length || !current?.jobId);
    list.innerHTML = (rows.length ? `<h2>最近规划</h2>${rows.join("")}` : "") +
      (examples ? `<details class="drawer-examples" ${examplesOpen ? "open" : ""}><summary>示例与归档</summary>${examples}</details>` : "");
    if (!$("#drawer").hidden) {
      void addDrawerDetails($$("[data-drawer-run]",list).map(row => ({row,run:entries[Number(row.dataset.drawerRun)]})));
    }
  }
  const showDrawer = (on) => { $("#drawer").hidden = !on; $("#mask").hidden = !on; };
  const historyPanel = $("#legacyHistory");
  historyPanel.hidden = location.origin !== "http://127.0.0.1:8795" || API.mode !== "mock";
  const browserHistory = historyPanel.hidden ? null : createBrowserHistory({
    panel:historyPanel, open:openModal, close:closeModal, download, escape:esc, toast,
    async findJob(id) {
      try { return (await API.personalized("job", null, {id}, AbortSignal.timeout(4000))).group; }
      catch { return null; }
    },
    render(favorites) {
      state.favs = favorites;
      renderDrawer();
      if (trip()) renderPlan();
      if (planningPlaces) showPlanningPlaces(planningPlaces,true);
      else drawRecs();
    },
  });
  $("#menuBtn").addEventListener("click", () => { showDrawer(true); renderDrawer(); });
  $("#drawerX").addEventListener("click", () => showDrawer(false));
  $("#mask").addEventListener("click", () => showDrawer(false));
  $("#drawerList").addEventListener("click", (e) => { const b = e.target.closest("[data-g]"); if (b) { selectGroup(+b.dataset.g); showDrawer(false); } });
  $("#newTripBtn").addEventListener("click", () => {
    if (API.hasPersonalized) return startPersonalized();
    selectGroup(-1); showDrawer(false); $("#askInput").focus();
  });
  $("#startTrip").onclick = startPersonalized;

  // ======================= 弹层 / 弹窗 =======================
  let popCtx = null;
  function openPop(anchor, html, ctx = null, cls = "") {
    const p = $("#pop"); p.classList.remove("pop-leg","pop-visit"); if (cls) p.classList.add(cls); p.innerHTML = html; p.hidden = false; popCtx = ctx;
    const r = anchor.getBoundingClientRect();
    const w = p.offsetWidth, h = p.offsetHeight;
    let left = Math.min(r.left, innerWidth - w - 8), top = r.bottom + 6;
    const up = top + h > innerHeight - 8;
    if (up) top = Math.max(8, r.top - h - 6);
    left = Math.max(8, left);
    p.style.left = left + "px"; p.style.top = top + "px";
    p.classList.toggle("up", up);
    p.style.setProperty("--arrow-x", Math.max(16, Math.min(w - 30, r.left + Math.min(r.width / 2, 60) - left)) + "px");
    $$(".card .more.on").forEach((b) => b.classList.remove("on"));
  }
  function closePop() { $("#pop").hidden = true; $("#pop").classList.remove("pop-leg","pop-visit"); popCtx = null; $$(".card .more.on").forEach((b) => b.classList.remove("on")); $$(".leg.open").forEach((b) => b.classList.remove("open")); }
  document.addEventListener("mousedown", (e) => { if (!$("#pop").hidden && !e.target.closest("#pop")) closePop(); });
  document.addEventListener("keydown", event => {
    if (event.key !== "Escape" || $("#pop").hidden) return;
    const trigger = popCtx;
    closePop();
    if (trigger instanceof HTMLElement) trigger.focus();
  });
  function openModal(html) {
    $("#modalBox").innerHTML = html; $("#modal").hidden = false;
    window.AtlasView?.openModal();
  }
  function closeModal() {
    if (!comparisonStudy) {
      $("#modal").hidden = true;
      window.AtlasView?.closeModal();
    }
  }
  $("#modal").addEventListener("click", (e) => { if (e.target.id === "modal" || e.target.closest("[data-close]")) closeModal(); });

  // ======================= AI 助手 =======================
  function inlineMd(s, t) {
    let h = esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
    return h.replace(/\[\[(.+?)\]\]/g, (_, name) => {
      const hit = t && findByName(t, name);
      return `<span class="poi-link" data-name="${name}">${name}</span>${hit ? `<span class="in-plan" title="已在行程中">${ic("check")}</span>` : ""}`;
    });
  }
  // 极简 markdown：# 大标题、## 小标题、- 列表、**粗体**、[[地名]]；没有 [[ ]] 的地名也会自动识别
  function md(text, t) {
    text = autoLink(text, t);
    const out = []; let ul = false;
    String(text).split("\n").forEach((line) => {
      const s = line.trimEnd();
      if (/^\s*[-*•]\s+/.test(s)) { if (!ul) { out.push("<ul>"); ul = true; } out.push(`<li>${inlineMd(s.replace(/^\s*[-*•]\s+/, ""), t)}</li>`); return; }
      if (ul) { out.push("</ul>"); ul = false; }
      if (/^#\s/.test(s)) out.push(`<h2>${inlineMd(s.slice(2), t)}</h2>`);
      else if (/^#{2,3}\s/.test(s)) out.push(`<h3>${inlineMd(s.replace(/^#+\s/, ""), t)}</h3>`);
      else if (s.trim()) out.push(`<p>${inlineMd(s, t)}</p>`);
    });
    if (ul) out.push("</ul>");
    return out.join("");
  }
  function autoLink(text, t) {
    if (!t || /\[\[/.test(text)) return text;
    const names = new Set();
    t.days.forEach((d) => d.items.forEach((x) => { if (x.pos && x.name.length >= 2 && !/→/.test(x.name)) names.add(x.name); }));
    [...names].sort((a, b) => b.length - a.length).forEach((n) => {
      const i = text.indexOf(n);
      if (i >= 0 && text.slice(Math.max(0, i - 2), i) !== "[[") text = text.slice(0, i) + `[[${n}]]` + text.slice(i + n.length);
    });
    return text;
  }
  function findByName(t, name) {
    for (const d of t.days) for (const x of d.items) if (x.name === name || x.place === name) return x;
    return t.pending.find((x) => x.name === name) || null;
  }

  function goalValue(value) {
    if (Array.isArray(value)) return value.map(goalValue).join("、");
    if (value && typeof value === "object") return value.name || Object.values(value).map(goalValue).join(" ");
    return String(value ?? "");
  }

  function renderArchivedGuide(t) {
    const {metrics, goals, alternative, first, recommended, requirements} = t.archive;
    const delta = alternative?.delta_from_recommended;
    const tradeoffs = delta && archiveMetricRows.flatMap(([label, key, format]) => {
      const n = delta[key];
      return Number.isFinite(n) && n !== 0 ? [label + (n < 0 ? "少 " : "多 ") + format(Math.abs(n))] : [];
    });
    const items = t.days.flatMap(day => day.items.map(item => ({...item, day:day.day})));
    const goalRows = goals.map(goal => {
      const matches = (goal.matched_places || []).flatMap(place => {
        const matches = items.filter(x => x.day === place.day && x.poiId === place.id &&
          (!place.start_time || x.start === place.start_time));
        const [item] = matches.length === 1 ? matches : [];
        return item ? [`<button class="archive-place" data-archive-item="${esc(item.uid)}">${esc(place.name)}</button>`] : [];
      });
      return `<li><strong>${esc(goalValue(goal.value))}</strong><span>${esc(readableGoal(goal))}</span>${matches.join("")}</li>`;
    });
    const hard = requirements.filter(entry => entry.strength === "hard");
    const portions = [
      Number.isFinite(metrics.visit_minutes) ? `景点游览 ${metrics.visit_minutes} 分钟` : "",
      Number.isFinite(metrics.city_transit_minutes) ? `市内接驳 ${metrics.city_transit_minutes} 分钟` : "",
    ].filter(Boolean).join(" · ");
    return `${t.query ? `<div class="msg-user">${esc(t.query)}</div>` : ""}<div class="msg-bot archive-guide">
      <h2>${esc(archiveLabel(t))}</h2>
      <p>${t.people} 人 · ${t.days.length} 天 · 整团参考 ¥${metrics.total_cost_cny.toFixed(2)}</p>
      ${portions ? `<p class="plan-evidence">${esc(portions)}</p>` : ""}
      ${tradeoffs?.length ? `<p>相对首推，${esc(tradeoffs.join("，"))}。</p>` :
        first && !recommended ? "<p>搜索时最先通过核验的方案。</p>" : ""}
      ${hard.length ? `<h3>已核验的要求</h3><ul>${hard.map(entry =>
        `<li>${esc(entry.label)} · ${entry.scope === "round_trip" ? "往返 " : ""}${esc(goalValue(entry.value))}</li>`).join("")}</ul>` : ""}
      ${goalRows.length ? `<h3>偏好与去处</h3><ul class="archive-goals">${goalRows.join("")}</ul>` : ""}
    </div>`;
  }

  function renderChat(keepScroll = false) {
    const chat = $("#chat");
    const g = group();
    let html = "";
    if (!g && online.isActive()) {
      html = `<div class="welcome"><h2>你的旅行需求</h2><p>在行程区写下原话或填写选项，核对后即可开始规划。</p></div>`;
    } else if (!g) {
      html += `<div class="welcome"><h2>Hi，想去哪儿玩？</h2><p>告诉我出发地、目的地、天数、人数和偏好，我来帮你规划完整行程。</p>
        <div class="sugg">${["当前位置上海，我和朋友想去杭州玩三天，坐高铁往返，想看西湖和西溪湿地", "从南京出发去武汉 3 天 2 人，想逛历史街区、吃本地小吃", "一个人从北京去西安玩 2 天，预算 2000 元"].map((s) => `<button>${esc(s)}</button>`).join("")}</div></div>`;
    } else if (trip()?.archive) {
      html = renderArchivedGuide(trip());
    } else {
      g.versions.forEach((v, idx) => {
        if (v.query) html += `<div class="msg-user">${esc(v.query)}</div>`;
        html += thinkHtml(`v${idx}`, v.thinking);
        let body = md(v.guide || "", v);
        if (idx > 0) {
          body += `<div class="ver-ask">这是为您生成的新线路，是否保留？<div class="ver-btns">
            <button data-ver="${idx}" class="${state.v === idx ? "on" : ""}">保留新版本</button>
            <button data-ver="${idx - 1}" class="${state.v === idx - 1 ? "on" : ""}">回到上一个版本</button></div></div>`;
        }
        html += `<div class="msg-bot">${body}</div>`;
      });
    }
    const key = g ? g.id : "_new";
    (state.chat[key] || []).forEach((m, i) => {
      if (m.role === "user") html += `<div class="msg-user">${esc(m.text)}</div>`;
      else if (m.itineraryAssistant) {
        const actions = m.operations && editor.matches(m.base) ? m.operations.map((op, index) =>
          `<button type="button" data-assist-message="${i}" data-assist-option="${index}">${esc(
            op.place || (op.action === "meal_options" ? "看餐馆推荐" : "试排并核验"))}</button>`).join("") : "";
        html += `<div class="msg-bot ${m.pending ? "pending" : ""}" role="status">
          ${m.text.split("\n").map(line => `<p>${esc(line)}</p>`).join("")}
          ${actions ? `<div class="ver-btns assistant-choices">${actions}</div>` : ""}
          ${m.proposal && editor.matches(m.base) ? `<div class="ver-btns assistant-choices">
            <button type="button" data-assist-batch="${i}">查看修改清单</button></div>` : ""}</div>`;
      }
      else html += (m.thinking ? thinkHtml(`c${i}`, m.thinking) : "") + `<div class="msg-bot ${m.pending ? "pending" : ""}" data-ci="${i}">${md(m.text, trip())}</div>`;
    });
    const job = state.jobs[key];
    if (job) {
      const pr = job.progress || { steps: ["提交任务"], step: 0 };
      const label = API.mode === "mock" ? "正在读取固定示例" : job.kind === "revise" ? "正在调整行程" : "正在规划行程";
      html += `<div class="msg-bot"><b>${label}</b><ul class="progress">${pr.steps.map((s, i) =>
        `<li class="${i < pr.step ? "done" : i === pr.step ? "now" : ""}">${esc(s)}</li>`).join("")}</ul></div>`;
    }
    const prev = chat.scrollTop;
    chat.innerHTML = html;
    if (keepScroll) { chat.scrollTop = prev; return; }
    // 停在最后一个用户提问的开头，回复从头开始看
    const users = $$(".msg-user", chat), last = users[users.length - 1];
    chat.style.scrollBehavior = "auto";
    chat.scrollTop = last ? last.offsetTop - 12 : 0;
    chat.style.scrollBehavior = "";
    updateToBottom();
  }
  const openThinks = new Set();
  function thinkHtml(id, text) {
    if (!text) return "";
    const open = openThinks.has(id);
    const label = API.mode === "mock" ? "样例说明" : "行程生成说明";
    return `<div><button class="think ${open ? "open" : ""}" data-think="${id}" aria-expanded="${open}">${label} <span class="chev">${ic("chevronDown")}</span></button></div>${open ? `<div class="think-body">${esc(text)}</div>` : ""}`;
  }
  function updateToBottom() { const c = $("#chat"); $("#toBottom").hidden = c.scrollHeight - c.scrollTop - c.clientHeight < 60; }
  $("#chat").addEventListener("scroll", updateToBottom);
  $("#toBottom").addEventListener("click", () => { const c = $("#chat"); c.scrollTop = c.scrollHeight; });

  $("#chat").addEventListener("click", (e) => {
    const proposal = e.target.closest("[data-assist-batch]");
    if (proposal) {
      const reply = state.chat[group()?.id]?.[Number(proposal.dataset.assistBatch)];
      if (reply?.proposal) editor.reviewProposal(reply.base, reply.proposal);
      return;
    }
    const action = e.target.closest("[data-assist-option]");
    if (action) {
      const reply = state.chat[group()?.id]?.[Number(action.dataset.assistMessage)];
      const operation = reply?.operations?.[Number(action.dataset.assistOption)];
      if (!operation || !editor.matches(reply.base)) return toast("当前行程版本已切换，请重新发送请求。");
      if (operation.action === "meal_options") {
        const item = locate(operation.activity)?.item;
        if (item) editor.replace(item, operation.cuisine);
      } else {
        void editor.trial(reply.base, operation);
      }
      return;
    }
    const point = e.target.closest("[data-archive-item]");
    if (point) {
      const item = locate(point.dataset.archiveItem);
      if (item) {
        if (state.tab !== "detail") {
          state.tab = "detail";
          resetView();
        }
        focusItem(item.item.uid, true);
      }
      return;
    }
    const th = e.target.closest("[data-think]");
    if (th) { const id = th.dataset.think; openThinks.has(id) ? openThinks.delete(id) : openThinks.add(id); renderChat(true); return; }
    const link = e.target.closest(".poi-link");
    if (link) {
      const x = trip() && findByName(trip(), link.dataset.name);
      if (x) focusItem(x.uid, true);
      else toast("「" + link.dataset.name + "」不在当前行程中");
      return;
    }
    const vb = e.target.closest("[data-ver]");
    if (vb) { selectVersion(+vb.dataset.ver); return; }
    const sg = e.target.closest(".sugg button");
    if (sg) { $("#askInput").value = sg.textContent; onAskInput(); $("#askInput").focus(); }
  });

  const askInput = $("#askInput");
  function onAskInput() { $("#sendBtn").disabled = !askInput.value.trim(); askInput.style.height = "auto"; askInput.style.height = Math.min(120, askInput.scrollHeight) + "px"; }
  askInput.addEventListener("input", onAskInput);
  askInput.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#askForm").requestSubmit(); } });
  $("#askForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const v = askInput.value.trim(); if (!v) return;
    if (trip()?.archive) {
      if (!group()?.canEdit) return;
      if (state.chat[group().id]?.some(message => message.itineraryAssistant && message.pending)) {
        return toast("上一条请求正在处理。");
      }
      askInput.value = ""; onAskInput(); void askItinerary(v);
      return;
    }
    if (!group()) {
      const demo = API.mode === "mock";
      openModal(`<h3>确认旅行需求</h3><blockquote class="confirm-request">${esc(v)}</blockquote>
        <p>${demo ? "预览会打开杭州三日固定行程。你的原话会保留在对话里。" : "确认后会将这段原话提交给规划服务。"}</p>
        <div class="btns"><button data-close>继续填写</button><button class="pri" id="confirmRequest">${demo ? "打开固定示例" : "提交需求"}</button></div>`);
      $("#confirmRequest").onclick = () => {
        askInput.value = ""; onAskInput(); closeModal(); ask(v);
      };
      return;
    }
    askInput.value = ""; onAskInput(); ask(v);
  });

  // 坑：意图识别。后端 /api/chat 若返回 intent 可替换这里的正则
  const REVISE_RE = /(调整|修改|改成|改为|换成|换一|替换|删掉|删除|去掉|加上|增加|加一|减少|重新规划|重排|宽松|紧凑|不想去|延长|缩短)/;

  function pushChat(m) { const k = group() ? group().id : "_new"; (state.chat[k] ||= []).push(m); return m; }
  function saveChat() { LS.set("chat", Object.fromEntries(Object.entries(state.chat).map(([k, v]) => [k, v.filter((m) => !m.pending)]))); }

  async function askItinerary(text) {
    const base = editor.context();
    if (!base) return;
    const groupId = group().id;
    pushChat({role:"user", text});
    const reply = pushChat({role:"assistant", text:"正在确认要处理的行程安排…", itineraryAssistant:true, pending:true});
    renderChat();
    try {
      const result = await API.itineraryEdit("assist", {...base, text, day:activeDay(), focus:state.hl});
      reply.text = result.message;
      if (result.operations?.length) {
        reply.base = base;
        reply.operations = result.operations;
      }
      if (result.proposal) {
        reply.base = base;
        reply.proposal = result.proposal;
      }
    } catch (error) {
      reply.text = error.message || "暂时无法处理这条请求。可以重试，或从时间线修改。";
    } finally {
      reply.pending = false;
      saveChat();
      if (group()?.id === groupId) renderChat();
    }
  }

  async function ask(text) {
    if (!group()) return runJob("create", text);
    if (REVISE_RE.test(text)) return runJob("revise", text);
    pushChat({ role: "user", text });
    const reply = pushChat({ role: "assistant", text: "正在思考…", pending: true });
    renderChat();
    const k = group().id;
    const history = state.chat[k].filter((m) => !m.pending).slice(-10).map((m) => ({ role: m.role, content: m.text }));
    const node = () => $(`.msg-bot[data-ci="${state.chat[k].indexOf(reply)}"]`);
    try {
      const full = await API.chat({ trip: trip(), messages: history }, (partial) => {
        reply.text = partial.replace(/<think>[\s\S]*?(<\/think>|$)/g, "").trim() || "正在思考…";
        const n = node(); if (n) n.innerHTML = md(reply.text, trip());
        updateToBottom();
      });
      reply.text = (full || "").replace(/<think>[\s\S]*?<\/think>/g, "").trim() || "（没有返回内容）";
    } catch (err) {
      reply.text = `还没连上对话服务：${err.message}\n本地示例模式会直连 llama-server（网址加 ?llm=http://地址:端口），接后端后加 ?api=http://后端地址。`;
    }
    reply.pending = false; saveChat(); renderChat(true);
  }

  async function runJob(kind, text) {
    const k = group() ? group().id : "_new";
    if (state.jobs[k]) return toast("上一个规划任务还在进行中");
    pushChat({ role: "user", text });
    state.jobs[k] = { kind, progress: null };
    renderChat();
    try {
      const { jobId } = kind === "create" ? await API.createPlan({ text }) : await API.revisePlan(trip(), text);
      for (;;) {
        const j = await API.getJob(jobId);
        state.jobs[k].progress = j.progress || state.jobs[k].progress;
        if (j.status === "done") {
          delete state.jobs[k];
          // 这条用户消息已经作为新版本的 query 显示，从追加对话里移除
          const list = state.chat[k]; if (list && list[list.length - 1] && list[list.length - 1].text === text) list.pop();
          if (k === "_new") {
            state.groups.push({ id: j.result.id, title: j.result.title, versions: [j.result] });
            selectGroup(state.groups.length - 1);
          } else {
            const g = state.groups.find((x) => x.id === k);
            g.versions.push(j.result);
            if (group() === g) selectVersion(g.versions.length - 1);
          }
          saveChat(); return;
        }
        if (j.status === "failed") throw new Error(j.error || "规划失败");
        if (group() ? group().id === k : k === "_new") renderChat(true);
        await new Promise((r) => setTimeout(r, 1500));
      }
    } catch (err) {
      delete state.jobs[k];
      pushChat({ role: "assistant", text: `规划没有完成：${err.message}` });
      renderChat(true);
    }
  }

  $("#aiMore").addEventListener("click", (e) => openPop(e.currentTarget,
    `<button class="mi-row" data-act="clearChat">${ic("trash")}清空追加的对话</button><button class="mi-row" data-act="newTrip">${ic("plus")}新建行程</button>`));
  const setAi = (on) => { $("#layout").classList.toggle("no-ai", !on); $("#aiFab").hidden = on; setTimeout(() => map.invalidateSize(), 220); };
  $("#aiCollapse").addEventListener("click", () => setAi(false));
  $("#aiFab").addEventListener("click", () => setAi(true));

  // ======================= 中栏 =======================
  $("#mainTabs").addEventListener("click", (e) => {
    const b = e.target.closest("[data-tab]"); if (!b) return;
    state.tab = b.dataset.tab;
    $$("#mainTabs button").forEach((x) => x.classList.toggle("on", x === b));
    renderPlan();
  });
  $("#viewToggle").addEventListener("click", (e) => {
    const b = e.target.closest("[data-view]"); if (!b) return;
    state.view = b.dataset.view; LS.set("view", state.view); renderPlan();
  });
  $("#planExpand").addEventListener("click", () => {
    const L = $("#layout"); const wide = !L.classList.contains("no-map");
    L.classList.toggle("no-map", wide); $("#mapFab").hidden = !wide;
    $("#planExpand").innerHTML = ic(wide ? "collapse" : "expand");
    setTimeout(() => map.invalidateSize(), 220);
  });

  function dayLabel(t, d) {
    if (!t.startDate) return "";
    const dt = new Date(t.startDate + "T00:00:00"); dt.setDate(dt.getDate() + d - 1);
    return `${dt.getMonth() + 1}/${dt.getDate()}`;
  }
  function syncDayPicker(picker, options, sec) {
    if (picker.options.length !== options.length || options.some(([label], i) => picker.options[i]?.text !== label)) {
      picker.replaceChildren(...options.map(([label, value]) => new Option(label, value)));
    }
    picker.value = options.some(([, value]) => value === sec) ? sec : "overview";
  }
  function renderChips() {
    const t = trip();
    const atlasMap = Boolean(window.AtlasView && t?.archive);
    window.AtlasView?.setPlanContext(state.sec, state.tab);
    $("#chipsBar").hidden = !t || state.tab !== "detail";
    $("#mapDayControl").hidden = !t || (!phoneLayout.matches && !atlasMap);
    if (!t) return;
    const chips = [["总览", "overview"], ...t.days.map((d) => [`第${d.day}天<small>${dayLabel(t, d.day)}</small>`, `day-${d.day}`])];
    if (!t.archive) chips.push(["待安排" + (t.pending.length ? ` ${t.pending.length}` : ""), "pending"], ["备注", "note"]);
    const compact = Boolean(t.archive && t.days.length >= 5 && phoneLayout.matches);
    $("#dayJump").hidden = !compact;
    $("#dayChips").hidden = compact;
    const options = [["总览","overview"], ...t.days.map(day =>
      [`第 ${day.day} 天 ${dayLabel(t, day.day)}`.trim(), `day-${day.day}`])];
    if (atlasMap || phoneLayout.matches) syncDayPicker($("#mapDaySelect"), options, state.sec);
    if (compact) {
      syncDayPicker($("#dayJumpSelect"), options, state.sec);
      return;
    }
    $("#dayChips").innerHTML = chips.map(([l, s]) => `<button data-sec="${s}" class="${state.sec === s ? "on" : ""}">${l}</button>`).join("");
    const bar = $("#dayChips"), selected = bar.querySelector(".on");
    if (selected && phoneLayout.matches) {
      const chip = selected.getBoundingClientRect(), strip = bar.getBoundingClientRect();
      if (chip.right > strip.right) bar.scrollLeft += chip.right - strip.right;
      else if (chip.left < strip.left) bar.scrollLeft += chip.left - strip.left;
    }
  }
  $("#dayChips").addEventListener("click", (e) => { const b = e.target.closest("[data-sec]"); if (b) gotoSec(b.dataset.sec); });
  $("#dayJumpSelect").onchange = event => gotoSec(event.target.value);
  $("#mapDaySelect").onchange = event => selectMapDay(event.target.value);
  function selectMapDay(sec) {
    map.closePopup();
    if (state.tab !== "detail") $("#mainTabs [data-tab=detail]").click();
    gotoSec(sec, {restore: true});
    if (!trip()?.archive && state.filters.size) drawRecs();
  }
  $("#addDay").addEventListener("click", () => { const t = trip(); if (!t) return; t.days.push({ day: t.days.length + 1, title: null, note: "", items: [] }); commit("已添加第" + t.days.length + "天"); gotoSec(`day-${t.days.length}`); });

  function gotoSec(sec, {restore = false} = {}) {
    state.sec = sec; renderChips(); drawRoute(true);
    if (trip()?.archive) updateTripUrl();
    const el = document.getElementById("sec-" + sec), body = $("#planBody");
    if (el) {
      lockSpy = true;
      const behavior = restore || matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
      body.scrollTo({top:sec === "overview" ? 0 : el.offsetTop - 4, behavior});
      setTimeout(() => (lockSpy = false), 600);
    }
  }
  // 滚动联动：根据滚动位置决定当前是哪一节，地图跟着切
  let lockSpy = false;
  $("#planBody").addEventListener("scroll", () => {
    if (lockSpy || state.tab !== "detail") return;
    const body = $("#planBody"); let cur = "overview";
    $$("[data-spy]", body).forEach((el) => { if (el.offsetTop - body.scrollTop < 120) cur = el.dataset.spy; });
    if (cur !== state.sec) { state.sec = cur; renderChips(); drawRoute(false); if (trip()?.archive) updateTripUrl(); }
  });
  const activeDay = () => (/^day-/.test(state.sec) ? +state.sec.slice(4) : 0);

  function fmtDur(x) {
    if (x.duration) { const a = x.duration.min, b = x.duration.max; const f = (v) => (v >= 1 ? +v.toFixed(1) : +v.toFixed(1)); return `建议玩${f(a)}${a !== b ? "-" + f(b) : ""}小时`; }
    if (x.start && x.end) {
      const m = (t) => { const [h, mm] = t.split(":").map(Number); return h * 60 + mm; };
      let d = m(x.end) - m(x.start); if (d < 0) d += 1440;
      return d >= 60 ? `约${+(d / 60).toFixed(1)}小时` : `约${d}分钟`;
    }
    return "";
  }
  function itemMeta(x) {
    const a = [], b = [];
    if (x.kind === "attraction" || x.kind === "shopping") {
      const d = fmtDur(x); if (d) a.push(d);
      if (x.hours) a.push(`${x.hours}开放`);
      b.push(x.price == null ? "票价待核对" : x.price ? `<span class="price">¥${+x.price}</span>/人` : "免费");
    } else if (x.kind === "restaurant") {
      a.push(TYPE_CN[x.type] || "用餐"); if (x.category) a.push(x.category); if (x.hours) a.push(`${x.hours}营业`);
      if (x.price) b.push(`人均 <span class="price">¥${+x.price}</span>`);
      if (x.dishes.length) b.push(esc("推荐：" + x.dishes.slice(0, 3).join("、")));
    } else if (x.kind === "hotel") {
      a.push("入住"); if (x.category) a.push(x.category);
      if (x.price) b.push(`<span class="price">¥${+x.price}</span>${x.uid === x.raw?._uid ? "/间夜" : "/晚"}`);
    } else if (x.kind === "station" || x.kind === "airport") {
      if (x.code) a.push(x.code); if (x.start && x.end) a.push(`${x.start} 出发 · ${x.end} 到达`);
      if (x.price) b.push(`<span class="price">¥${+x.price}</span>/人`);
    } else { const d = fmtDur(x); if (d) a.push(d); }
    if (x.start && x.end && x.kind !== "station" && x.kind !== "airport") b.unshift(`${x.start}–${x.end}`);
    return [a.map(esc).join('<span class="bar">|</span>'), b.join(" · ")];
  }

  function placeDetails(info) {
    return Object.entries(info || {}).filter(([, v]) => v != null && v !== "" && !/^(unknown|nan)$/i.test(v))
      .map(([k, v]) => `<div><span class="k">${esc(k)}：</span>${esc(v)}</div>`).join("");
  }

  function itemHtml(x, n) {
    const [m1, m2] = itemMeta(x);
    const details = trip()?.archive && ["attraction","restaurant","hotel"].includes(x.kind) ? placeDetails(x.raw?._info) : "";
    const replaceLabel = {attraction:"换景点", restaurant:"换饭店", hotel:"换酒店"}[x.kind];
    const checkHours = trip()?.archive && x.pos && ["attraction","restaurant"].includes(x.kind);
    const canEdit = group()?.canEdit && replaceLabel && !x.lock;
    const k = KIND[x.kind] ? x.kind : "attraction";
    const thumb = x.image ? `<div class="thumb" style="background-image:url('${esc(x.image)}')"></div>` : `<div class="thumb ${k}">${KIND[k].glyph}</div>`;
    return `<div class="it ${state.hl === x.uid ? "hl" : ""} ${state.sel.has(x.uid) ? "sel" : ""}" data-uid="${x.uid}" draggable="${!trip()?.archive}">
      <label class="chk"><input type="checkbox" data-sel="${x.uid}" ${state.sel.has(x.uid) ? "checked" : ""}></label>
      <span class="tl-ic ${k}">${ic(KIND[k].icon)}</span>
      <div class="card">${window.AtlasView?.time(x) || ""}${thumb}<div class="body">
        <div class="nm">${n ? n + "." : ""}${esc(x.name)}</div>
        ${m1 ? `<div class="meta">${m1}</div>` : ""}${m2 ? `<div class="meta2">${m2}</div>` : ""}
        ${Object.hasOwn(fixedLabel, x.lock) ? `<span class="visit-lock">${fixedLabel[x.lock]}</span>` : ""}
        ${x.note ? `<div class="note">${esc(x.note)}</div>` : ""}
        ${details ? `<details class="place-details"><summary>地点详情</summary>${details}</details>` : ""}
        ${checkHours || canEdit ? `<div class="visit-actions">
          ${checkHours ? `<button type="button" data-check-hours="${x.uid}">查营业</button>` : ""}
          ${canEdit && x.kind === "hotel" ? `<button type="button" class="visit-edit" data-plan-replace="${x.uid}">${replaceLabel}</button>` : ""}
          ${canEdit && x.kind !== "hotel" ? `<button type="button" class="visit-edit" data-plan-edit="${x.uid}" aria-label="编辑${esc(x.name)}"
            aria-haspopup="true">${ic("edit")}编辑</button>` : ""}
        </div>` : ""}
      </div><button class="more" data-menu="${x.uid}">${ic("more")}</button></div>
      <div class="side-tools"><button class="drag" title="拖动排序">${ic("drag")}</button><button data-alt="${x.uid}" title="替换">${ic("refresh")}</button><button data-askabout="${x.uid}" title="问问 AI">${ic("sparkle")}</button></div>
    </div>`;
  }

  function weatherHtml(day, start) {
    return `<div class="timeline-weather" data-weather-day="${day}" data-weather-theme="neutral">
      <span class="weather-glyph" aria-hidden="true"><i></i></span>
      <div class="timeline-weather-copy">
        <span class="timeline-weather-date"></span>
        <span class="timeline-forecast" role="status"></span>
        <div class="timeline-weather-periods" hidden>
          <div><span>白天</span><strong data-weather-part="day"></strong><span data-weather-temp="day" hidden></span></div>
          <div><span>夜间</span><strong data-weather-part="night"></strong><span data-weather-temp="night" hidden></span></div>
        </div>
      </div>
      <a class="timeline-weather-source" href="https://lbs.amap.com/api/webservice/guide/api/weatherinfo"
        target="_blank" rel="noopener noreferrer" hidden>高德天气 ↗</a>
      ${start ? '<button type="button" data-weather-retry hidden>重查</button>' :
        '<button type="button" data-weather-date>选日期</button>'}
    </div>`;
  }

  // ---- 两项之间的交通：驾车 / 公共交通 / 步行 三选一 ----
  // 规划器给的 leg 算“真实”方案；后端可在 leg.options 里给出全部方式（坑，见 docs/webui-api.md）；缺的按直线距离估算
  const CAT = { walk: "walk", taxi: "car", car: "car", metro: "transit", bus: "transit", transit: "transit" };
  const CAT_UI = { car: ["car", "驾车", "car"], transit: ["bus", "公共交通", "bus"], walk: ["walk", "步行", "walk"],
    subway: ["metro", "公共交通", "bus"] };
  const routeChoiceMode = mode => mode === "subway" ? "transit" : mode;
  const fmtKm = (km) => (km == null ? "" : km < 0.1 ? "<100米" : km < 1 ? `${Math.round(km * 1000)}米` : `${+km.toFixed(1)}公里`);
  function estimate(road, c) {
    if (c === "walk") return { minutes: Math.max(1, Math.round(road / 4.5 * 60)), km: road };
    if (c === "car") return { minutes: Math.round(road / 30 * 60) + 3, km: road, cost: road < 3 ? 13 : Math.round(13 + (road - 3) * 2.5) };
    if (road < 1.2) return null; // 太近，没有公交方案
    return { minutes: Math.round(road / 18 * 60) + 10, km: road, cost: road < 10 ? 2 : 4 };
  }
  function prevOf(x) {
    const L0 = locate(x.uid); if (!L0 || L0.i === 0) return null;
    return L0.list[L0.i - 1];
  }
  function archivedEndpoints(item) {
    const segments = item.leg?.segments;
    if (!segments?.length) return null;
    const first = segments[0], last = segments[segments.length - 1];
    return [{name:first.from, pos:first.fromPos}, {...item, name:last.to, pos:last.toPos}];
  }
  function archivedLeg(x) {
    if (!x.leg) return null;
    const cur = x.leg.mode === "metro" ? "subway" : CAT[x.leg.mode] || "car";
    return {cur, chosen: {...x.leg, mode:cur}};
  }
  // ---- 真实路线（后端 /api/route，高德路径规划）缓存：key = 方式|起点|终点 ----
  const routeCache = new Map(), routeLoading = new Set();
  const rkey = (mode, a, b) => `${mode}|${a[0].toFixed(5)},${a[1].toFixed(5)}|${b[0].toFixed(5)},${b[1].toFixed(5)}`;
  const getCached = (mode, a, b) => (a && b ? routeCache.get(rkey(mode, a, b)) : null);
  const roadReference = (mode, a, b) => a && b ? window.SHORTTRIP_ROAD_REFERENCE?.[rkey(mode, a, b)] : null;
  const hasPath = path => Array.isArray(path) && path.length >= 2;
  const legGeometry = o => hasPath(o?.path) || (o?.segments?.some(s => hasPath(s.path)) ?? false);
  const isRoute = r => ["amap", "osrm"].includes(r?.source) && !r.unavailable &&
    Number.isFinite(r.minutes) && Number.isFinite(r.km) && legGeometry(r) &&
    (r.mode !== "subway" || r.segments?.some(s => s.type === "subway"));
  function cachedLeg(m, from, to) {
    const cached = getCached(m, from.pos, to.pos);
    if (isRoute(cached)) return cached;
    const saved = to.savedRoute;
    if (saved && isRoute(saved.data) && saved.expires > Date.now() &&
        saved.key === rkey(m, from.pos, to.pos) && saved.data.mode === m) return saved.data;
    return cached;
  }
  const SEG_MODE = { walk: "walk", subway: "metro", bus: "bus", railway: "railway" };
  function routeToOpt(r, c) {
    if (!r) return null;
    if (r.unavailable) return { mode: c, unavailable: true, amap: r.source === "amap" };
    return {
      mode: c, source: r.source, minutes: r.minutes, km: r.km, cost: r.cost, path: r.path, tmcs: r.tmcs, steps: r.steps,
      retrievedAt: r.retrievedAt,
      segments: (r.segments || []).map((g) => ({ mode: SEG_MODE[g.type] || "bus", name: g.name, from: g.from, to: g.to, stops: g.stops, minutes: g.minutes, km: g.km, path: g.path })),
      amap: r.source === "amap", est: r.source !== "amap", reason: r.reason,
    };
  }
  /** 拉取若干段路线（只拉缓存里没有的），完成后回调 */
  async function fetchRoutes(reqs, done) {
    const t = trip(); if (!t || API.mode !== "http") return;
    const need = reqs.filter((r) => r.from && r.to && !routeCache.has(rkey(r.mode, r.from, r.to)) && !routeLoading.has(rkey(r.mode, r.from, r.to)));
    if (!need.length) return;
    need.forEach((r) => routeLoading.add(rkey(r.mode, r.from, r.to)));
    try {
      const res = await API.getRoutes(t.city, need.map((r) => ({ from: r.from, to: r.to, mode: r.mode })));
      need.forEach((r, i) => routeCache.set(rkey(r.mode, r.from, r.to), res[i] || { source: "error" }));
    } catch (e) {
      console.warn("路线获取失败", e); // 记下失败，避免重画时反复请求
      need.forEach((r) => routeCache.set(rkey(r.mode, r.from, r.to), { source: "error", reason: e.message }));
    }
    finally { need.forEach((r) => routeLoading.delete(rkey(r.mode, r.from, r.to))); }
    done && done();
  }

  function legInfo(prev, x) {
    const straight = prev && prev.pos && x.pos ? API.haversine(prev.pos, x.pos) : null;
    if (straight == null && !x.leg) return null;
    const road = straight != null ? straight * 1.3 : x.leg.km;
    const opts = {};
    ((x.leg && x.leg.options) || []).forEach((o) => (opts[CAT[o.mode] || o.mode] = { ...o, mode: CAT[o.mode] || o.mode }));
    const plannedCat = x.leg ? CAT[x.leg.mode] || "car" : null;
    // 高德真实路线优先
    if (straight != null) Object.keys(CAT_UI).forEach((c) => {
      const r = cachedLeg(c, prev, x);
      if (isRoute(r) || (r?.source === "amap" && r.unavailable)) opts[c] = { ...routeToOpt(r, c), planned: c === plannedCat };
    });
    if (x.leg) { const c = plannedCat; opts[c] = opts[c] || { mode: c, minutes: x.leg.minutes, km: x.leg.km, cost: x.leg.cost, segments: x.leg.segments, planned: true }; }
    if (straight != null) for (const c of ["car", "walk"]) {
      const reference = roadReference(c, prev.pos, x.pos);
      if (reference && !opts[c]?.amap && !legGeometry(opts[c])) {
        opts[c] = {...opts[c], ...reference, mode: c, est: true};
      }
    }
    ["car", "transit", "walk"].forEach((c) => {
      if (opts[c] || road == null) return;
      const e = estimate(road, c);
      opts[c] = e ? { mode: c, ...e, est: true } : { mode: c, unavailable: true };
    });
    let cur = x.legChoice || (x.leg ? CAT[x.leg.mode] || "car" : road < 1.5 ? "walk" : "car");
    if (cur === "subway") opts.subway ??= {mode: "subway", unavailable: true};
    else if (!opts[cur] || opts[cur].unavailable) cur = "walk";
    return { cur, opts, straight, chosen: opts[cur] };
  }
  function amapRoutePoint(item) {
    return [item.pos[1], item.pos[0], (item.place || item.name).replaceAll(",", "，")].join(",");
  }
  function navUrl(prev, x, c) {
    if (!prev || !prev.pos || !x.pos) return null;
    return "https://uri.amap.com/navigation?" + new URLSearchParams({
      from: amapRoutePoint(prev), to: amapRoutePoint(x), mode: CAT_UI[c][2], coordinate: "gaode", callnative: "0", src: "ShortTrip"
    });
  }
  function legHtml(prev, x) {
    if (trip()?.archive) {
      const info = archivedLeg(x);
      if (!info) return "";
      const [from, to] = archivedEndpoints(x), leg = info.chosen, planned = x.leg.segments;
      const modes = [...new Set(planned.map(s => MODE[s.mode]?.[1] || s.mode))].join(" + ");
      const nav = navUrl(from, to, info.cur);
      return `<div class="leg archive-leg"><div class="archive-leg-data"><span class="mode">${ic(CAT_UI[info.cur][0])}</span>
        <div><div>${esc(from.name)} → ${esc(to.name)}</div><small>${esc(planned[0].start)}–${esc(planned.at(-1).end)} · ${esc(modes)} · ${leg.minutes} 分钟${leg.cost ? " · 约 ¥" + leg.cost.toFixed(2) : ""}</small></div></div>
        ${nav ? `<button class="leg-detail" data-route-detail="${x.uid}">路线详情</button><a class="nav" href="${esc(nav)}" target="_blank" rel="noopener noreferrer">高德 ↗</a>` : ""}</div>`;
    }
    const info = legInfo(prev, x); if (!info) return "";
    const o = info.chosen;
    const nav = navUrl(prev, x, info.cur);
    const badge = ["osm_reference","osrm"].includes(o.source) ? "道路参考" : o.est ? "估算" : !legGeometry(o) ? "线路待核实" : "";
    const details = o.unavailable ? "公共交通路线待核实" : `${o.minutes}分钟 · ${fmtKm(o.km)}${o.cost ? ` · ¥${+(+o.cost).toFixed(1)}` : ""}`;
    return `<div class="leg"><button class="leg-main" data-leg="${x.uid}"><span class="mode">${ic(CAT_UI[info.cur][0])}</span><span class="u">${details}</span>${badge && !o.unavailable ? `<span class="est">${badge}</span>` : ""}<span class="chev">${ic("chevronDown")}</span></button>${nav ? `<button class="leg-detail" data-route-detail="${x.uid}">路线详情</button><a class="nav" href="${esc(nav)}" target="_blank" rel="noopener noreferrer">高德 ↗</a>` : ""}</div>`;
  }
  function legPopHtml(uid) {
    const x = locate(uid).item, prev = prevOf(x), info = legInfo(prev, x);
    let h = `<div class="leg-pop">`;
    ["car", "transit", "walk"].forEach((c) => {
      const on = routeChoiceMode(info.cur) === c;
      const choice = on ? info.cur : c;
      const o = info.opts[choice], [icn, label] = CAT_UI[choice];
      if (c === "transit" && (!o || o.unavailable) && prev?.pos && x.pos) {
        h += `<button class="lp-row" data-act="routeModal" data-uid="${uid}" data-mode="transit">${ic(icn)}<b>${label}</b><span class="r">查询路线</span></button>`;
        return;
      }
      if (!o || o.unavailable) { h += `<div class="lp-row off">${ic(icn)}<b>${label}</b><span class="r">无可用路线</span></div>`; return; }
      h += `<button class="lp-row ${on ? "on" : ""}" data-act="legMode" data-uid="${uid}" data-m="${choice}">${ic(icn)}<b>${label}</b>${o.planned ? '<span class="tag">规划方案</span>' : ""}${o.amap ? '<span class="tag amap">高德</span>' : o.est ? '<span class="tag">估算</span>' : ""}<span class="r">${o.minutes}分钟 · ${fmtKm(o.km)}${o.cost ? ` · ¥${+(+o.cost).toFixed(1)}` : ""}</span><span class="ck">${on ? ic("check") : ""}</span></button>`;
      if (on && o.segments && o.segments.length) {
        h += `<div class="lp-seg">${o.segments.map((s) => `<div>${ic((MODE[s.mode] || ["car"])[0])}<span>${esc(s.name || (MODE[s.mode] || [0, s.mode])[1])}${s.from ? `：${esc(s.from)} → ${esc(s.to)}` : ""}${s.stops ? `（${s.stops}站）` : ""}</span><em>${s.minutes}分钟${s.km != null ? ` · ${fmtKm(+s.km)}` : ""}${s.cost ? ` · ¥${s.cost}` : ""}</em></div>`).join("")}</div>`;
      }
    });
    if (info.straight != null) h += `<div class="lp-row static">${ic("route")}<b>直线距离</b><span class="r">${fmtKm(info.straight)}</span><span class="ck"></span></div>`;
    if (prev && prev.pos && x.pos) h += `<button class="lp-row" data-act="routeModal" data-uid="${uid}">${ic("fit")}<b>查看路线详情</b><span class="r">地图与接驳</span><span class="ck">${ic("chevronRight")}</span></button>`;
    h += `<button class="lp-row batch" data-act="legBatch">批量更改同城交通方案 ${ic("chevronRight")}</button></div>`;
    return h;
  }

  function dayTitle(t, d) {
    if (window.AtlasView) return AtlasView.dayTitle(t, d);
    if (d.title) return d.title;
    const names = d.items.filter((x) => x.kind !== "station" && x.kind !== "airport" && x.kind !== "hotel").map((x) => x.name);
    return `第${d.day}天 · ${names.length ? names.slice(0, 6).join("→") + (names.length > 6 ? "…" : "") : t.city}`;
  }

  async function renderPlan() {
    window.AtlasView?.setPlanContext(state.sec, state.tab);
    renderReviewBar(trip());
    const body = $("#planBody"), t = trip();
    body.classList.toggle("list-view", state.view === "list");
    body.classList.toggle("selecting", state.sel.size > 0);
    $$("#viewToggle button").forEach((b) => b.classList.toggle("on", b.dataset.view === state.view));
    renderBatch();
    if (!t) {
      renderTimelineWeather(null, "");
      body.innerHTML = online.isActive() ? "" : `<div class="empty">${state.jobs._new ? "正在为你规划行程…" : "在左侧告诉 AI 你想去哪儿，行程会出现在这里"}</div>`;
      return;
    }
    if (state.tab === "booking") return renderBooking(body, t);

    let h = `<div id="sec-overview" data-spy="overview"><div class="sec-title">总览</div>`;
    if (t.missing && t.missing.length) h += `<div class="warn-box">这些地点在数据库里找不到坐标，地图上没画：${t.missing.map(esc).join("、")}</div>`;
    if (t.edited) h += `<div class="info-box">行程已手动调整，时间与交通为估算。可点「顺路优化」，或告诉 AI 帮你重新规划。</div>`;
    t.days.forEach((d) => {
      const parts = d.items.map((x) => (x.kind === "hotel" ? `<span class="stay">${ic("hotel")} 住在${esc(x.name)}</span>` : esc(x.name)));
      const cost = d.items.reduce((s, x) => s + (+x.cost || 0) + (+(x.leg && x.leg.cost) || 0), 0);
      h += `<div class="ov-card day" data-goto="day-${d.day}"><h3>${window.AtlasView ? esc(dayTitle(t, d)) : `第${d.day}天 · ${esc(t.city)}`}</h3>${cost ? `<span class="dcost">约 ¥${Math.round(cost)}</span>` : ""}
        <div class="chain">${parts.length ? parts.join(`<span class="arr">${ic("arrowDown").replace("<svg", '<svg style="transform:rotate(-90deg)"')}</span>`) : '<span class="ov-sub">还没有安排</span>'}</div></div>`;
    });
    if (!t.archive) {
      h += `<div class="ov-card" data-goto="pending"><h3><span class="hl-title">待安排</span></h3>${t.pending.length ? `<div class="chain">${t.pending.map((x) => esc(x.name)).join("、")}</div>` : ""}</div>`;
      h += `<div class="ov-card" data-goto="note"><h3><span class="hl-title">备注</span></h3>${t.note ? `<div class="ov-sub">${esc(t.note.slice(0, 60))}</div>` : ""}</div>`;
    }
    h += "</div>";

    const start = t.archive ? reviewDate(t, group()) : "";
    t.days.forEach((d, di) => {
      h += `<section class="day-sec" id="sec-day-${d.day}" data-spy="day-${d.day}" data-di="${di}">
        <div class="day-head"><h3>${esc(dayTitle(t, d))}</h3><button class="ib" data-daymenu="${di}">${ic("more")}</button></div>
        ${t.archive ? weatherHtml(d.day, start) : ""}
        <div class="day-note" contenteditable="true" data-daynote="${di}" data-ph="添加备注">${esc(d.note || "")}</div>
        <div class="tl">${!t.archive || group()?.canEdit ?
          `<div class="opt-row"><span class="ring"></span><button class="opt-btn" type="button" ${t.archive ? `data-plan-optimize="${d.day}"` : `data-opt="${di}"`}>${ic("route")}顺路优化</button></div>` : ""}`;
      if (!d.items.length) h += `<div class="empty-day" data-drop-day="${di}">把地点拖到这里，或从地图上「加入行程」</div>`;
      d.items.forEach((x, i) => { h += legHtml(d.items[i - 1], x) + itemHtml(x, i + 1); });
      if (t.archive && group()?.canEdit) {
        h += `<div class="day-add-row"><button class="day-add-place" type="button" data-plan-insert="${d.day}">${ic("plus")}添加地点</button></div>`;
      }
      h += `</div></section>`;
    });
    if (!t.archive) {
    h += `<section class="day-sec" id="sec-pending" data-spy="pending"><div class="day-head"><h3>待安排</h3></div><div class="tl">`;
    h += t.pending.length ? t.pending.map((x) => itemHtml(x, 0)).join("") : `<div class="empty-day" data-drop-day="-1">在地图上点地点「加入待安排」，或从行程里「移动到 → 待安排」</div>`;
    h += `</div></section><section class="day-sec" id="sec-note" data-spy="note"><div class="day-head"><h3>备注</h3></div>
      <textarea class="note-area" id="tripNote" placeholder="记录签证、行李、预算等信息">${esc(t.note || "")}</textarea></section>`;
    }
    body.innerHTML = h;
    renderTimelineWeather(t.archive ? t : null, start);
  }

  async function renderBooking(body, t) {
    if (t.archive) return renderBookingReview(body,t,reviewDate(t,group()),group().id,{
      current:() => trip() === t && state.tab === "booking",
      icon:kind => ic(KIND[kind].icon),
      chooseDate:() => {
        openArchiveReview(activeDay());
        $("#dayFacts .facts-date").focus();
      },
    });
    body.innerHTML = `<div class="empty">加载中…</div>`;
    const list = await API.getBookings(t);
    body.innerHTML = `<div class="sec-title">预订核对</div>` + (list.map((b) => {
      const officialRail = b.kind === "station" ? "https://www.12306.cn/" : null;
      const url = b.url || officialRail;
      return `<div class="bk"><span class="mi ${b.kind}">${ic(KIND[b.kind] ? KIND[b.kind].icon : "landmark")}</span>
        <div class="bn">${esc(b.name)}<small>第${b.day}天${b.code ? " · " + esc(b.code) : ""}${b.start ? " · " + esc(b.start) : ""}${b.price ? " · 参考 ¥" + esc(b.price) : ""}</small></div>
        ${b.status === "booked" ? '<span style="color:var(--green)">已预订</span>' : url
          ? `<a class="go" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${officialRail && !b.url ? "12306 核对 ↗" : "查看来源 ↗"}</a>`
          : '<span class="booking-pending">待核实</span>'}</div>`;
    }).join("") || '<div class="empty">没有需要预订的项目</div>');
  }

  // ---- 中栏事件 ----
  const pb = $("#planBody");
  pb.addEventListener("click", async (e) => {
    const T = e.target;
    let b;
    if (T.closest(".place-details")) return;
    if ((b = T.closest("[data-plan-edit]"))) {
      const item = locate(b.dataset.planEdit)?.item;
      if (!item || item.lock) return;
      const uid = esc(item.uid), isVisit = item.kind === "attraction";
      openPop(b, `<button type="button" class="mi-row" data-act="planChangeTime" data-uid="${uid}">${ic("clock")}调整时间</button>
        <button type="button" class="mi-row" data-act="planReplace" data-uid="${uid}">${ic("refresh")}${isVisit ? "换景点" : "换饭店"}</button>
        ${isVisit ? `<div class="sep"></div><button type="button" class="mi-row visit-remove" data-act="planRemove" data-uid="${uid}">${ic("trash")}试移除这一站</button>` : ""}`,
        b, "pop-visit");
      $("#pop button")?.focus({preventScroll:true});
      return;
    }
    if ((b = T.closest("[data-plan-replace]"))) return editor.replace(locate(b.dataset.planReplace).item);
    if ((b = T.closest("[data-plan-optimize]"))) return editor.optimize(Number(b.dataset.planOptimize));
    if ((b = T.closest("[data-plan-insert]"))) return editor.addDayPlace(trip(), Number(b.dataset.planInsert));
    if ((b = T.closest("[data-goto]"))) return gotoSec(b.dataset.goto);
    if ((b = T.closest("[data-sel]"))) { const u = b.dataset.sel; b.checked ? state.sel.add(u) : state.sel.delete(u); renderPlan(); return; }
    if ((b = T.closest("[data-menu]"))) { e.stopPropagation(); b.classList.add("on"); return itemMenu(b, b.dataset.menu); }
    if ((b = T.closest("[data-daymenu]"))) return dayMenu(b, +b.dataset.daymenu);
    if ((b = T.closest("[data-weather-date]"))) {
      openArchiveReview(Number(b.closest("[data-weather-day]").dataset.weatherDay));
      return $("#dayFacts .facts-date").focus();
    }
    if (T.closest("[data-weather-retry]")) return renderTimelineWeather(trip(), reviewDate(trip(), group()), true);
    if ((b = T.closest("[data-check-hours]"))) {
      const location = locate(b.dataset.checkHours);
      if (location?.di >= 0) openArchiveReview(trip().days[location.di].day, location.item.uid);
      return;
    }
    if ((b = T.closest("[data-route-detail]"))) return openRouteModal(b.dataset.routeDetail);
    if ((b = T.closest("[data-leg]"))) {
      e.stopPropagation(); b.closest(".leg").classList.add("open");
      const uid = b.dataset.leg; openPop(b, legPopHtml(uid), b, "pop-leg");
      const x = locate(uid).item, prev = prevOf(x);
      if (prev && prev.pos && x.pos) fetchRoutes(["car", "transit", "walk"].map((m) => ({ mode: m, from: prev.pos, to: x.pos })), () => {
        if (!$("#pop").hidden && popCtx === b) { openPop(b, legPopHtml(uid), b, "pop-leg"); b.closest(".leg").classList.add("open"); }
        renderPlan(); drawRoute(false);
      });
      return;
    }
    if (T.closest(".leg .nav")) return;
    if ((b = T.closest("[data-opt]"))) {
      const di = +b.dataset.opt; b.textContent = "优化中…";
      try { trip().days[di].items = await API.optimizeDay(trip(), di); commit("已按顺路重新排序"); } catch (err) { toast("优化失败：" + err.message); renderPlan(); }
      return;
    }
    if ((b = T.closest("[data-alt]"))) return altMenu(b, b.dataset.alt);
    if ((b = T.closest("[data-askabout]"))) { const x = locate(b.dataset.askabout).item; setAi(true); askInput.value = `关于「${x.name}」，`; onAskInput(); askInput.focus(); return; }
    if ((b = T.closest(".it"))) focusItem(b.dataset.uid, false);
  });
  pb.addEventListener("focusout", (e) => {
    const n = e.target.closest("[data-daynote]");
    if (n) { const d = trip().days[+n.dataset.daynote]; const v = n.textContent.trim(); if (v !== (d.note || "")) { d.note = v; commit(); } }
    if (e.target.id === "tripNote") { const v = e.target.value; if (v !== (trip().note || "")) { trip().note = v; commit("备注已保存"); } }
  });
  pb.addEventListener("mouseover", (e) => { const it = e.target.closest(".it"); hoverPin(it ? it.dataset.uid : null); });
  pb.addEventListener("mouseleave", () => hoverPin(null));

  // 拖拽排序（日内、跨天、待安排）
  let dragUid = null;
  pb.addEventListener("dragstart", (e) => { const it = e.target.closest(".it"); if (!it) return; dragUid = it.dataset.uid; it.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; });
  pb.addEventListener("dragend", () => { dragUid = null; $$(".dragging,.drag-over", pb).forEach((x) => x.classList.remove("dragging", "drag-over")); });
  pb.addEventListener("dragover", (e) => {
    if (!dragUid) return;
    const tgt = e.target.closest(".it,[data-drop-day]"); if (!tgt) return;
    e.preventDefault(); $$(".drag-over", pb).forEach((x) => x !== tgt && x.classList.remove("drag-over")); tgt.classList.add("drag-over");
  });
  pb.addEventListener("drop", (e) => {
    if (!dragUid) return; e.preventDefault();
    const tgt = e.target.closest(".it,[data-drop-day]"); if (!tgt) return;
    const src = locate(dragUid); if (!src) return;
    const [x] = src.list.splice(src.i, 1);
    if (tgt.dataset.dropDay != null) { const di = +tgt.dataset.dropDay; (di < 0 ? trip().pending : trip().days[di].items).push(x); }
    else { const dst = locate(tgt.dataset.uid); if (!dst) src.list.splice(src.i, 0, x); else dst.list.splice(dst.i, 0, x); }
    x.leg = null; commit();
  });

  function destOptions(act, uid) {
    const t = trip();
    return t.days.map((d, di) => `<button class="mi-row" data-act="${act}" data-uid="${uid}" data-to="${di}">第${d.day}天<span class="r">${d.items.length} 项</span></button>`).join("")
      + `<button class="mi-row" data-act="${act}" data-uid="${uid}" data-to="-1">待安排</button>`;
  }
  function itemMenu(anchor, uid) {
    const L = locate(uid);
    openPop(anchor, `<button class="mi-row" data-act="note" data-uid="${uid}">${ic("edit")}备注</button>
      ${!["station","airport"].includes(L.item.kind) ? `<button class="mi-row" data-act="editTime" data-uid="${uid}">${ic("calendar")}修改时刻</button>` : ""}
      ${L.di >= 0 && clock(L.item.start) != null
        ? `<button class="mi-row" data-act="visitLock" data-uid="${uid}">${ic("check")}${L.item.lock ? "解除时间锁定" : ["station","airport"].includes(L.item.kind) ? "锁定返程" : "锁定预约"}</button>` : ""}
      <button class="mi-row" data-act="moveMenu" data-uid="${uid}">${ic("move")}移动到<span class="r">${ic("chevronRight")}</span></button>
      ${L.i > 0 ? `<button class="mi-row" data-act="up" data-uid="${uid}">${ic("arrowUp")}上移一位</button>` : ""}
      ${L.i < L.list.length - 1 ? `<button class="mi-row" data-act="down" data-uid="${uid}">${ic("arrowDown")}下移一位</button>` : ""}
      <button class="mi-row" data-act="copyMenu" data-uid="${uid}">${ic("copy")}复制到<span class="r">${ic("chevronRight")}</span></button>
      <div class="sep"></div><button class="mi-row danger" data-act="del" data-uid="${uid}">${ic("trash")}删除</button>`, anchor);
    anchor.classList.add("on");
  }
  function dayMenu(anchor, di) {
    openPop(anchor, `<button class="mi-row" data-act="renameDay" data-di="${di}">${ic("edit")}修改标题</button>
      <button class="mi-row" data-act="insertDay" data-di="${di}">${ic("plus")}在后面插入一天</button>
      <button class="mi-row" data-act="clearDay" data-di="${di}">${ic("refresh")}清空到待安排</button>
      <div class="sep"></div><button class="mi-row danger" data-act="delDay" data-di="${di}">${ic("trash")}删除这一天</button>`);
  }
  async function altMenu(anchor, uid) {
    const L = locate(uid);
    openPop(anchor, `<div class="ph">正在找附近的替换选项…</div>`);
    const list = await API.getAlternatives(trip(), L.item);
    if ($("#pop").hidden) return;
    openPop(anchor, `<div class="ph">替换「${esc(L.item.name)}」</div>` + (list.map((p, i) =>
      `<button class="mi-row alt" data-act="replace" data-uid="${uid}" data-i="${i}">${ic(KIND[p.kind] ? KIND[p.kind].icon : "landmark")}${esc(p.name)}<small>${L.item.pos ? (API.haversine(L.item.pos, p.pos)).toFixed(1) + "km" : ""}</small></button>`).join("") || `<div class="ph">没有候选</div>`), { alts: list });
  }

  function itemFromPoi(p) {
    const info = p.info || {};
    const kind = p.kind === "fav" ? "attraction" : p.kind;
    const m = String(info["建议游玩(小时)"] || "").match(/([\d.]+)\s*-\s*([\d.]+)/);
    return {
      uid: "p" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6), kind,
      type: kind === "restaurant" ? "lunch" : kind === "hotel" ? "accommodation" : "attraction",
      name: p.name, place: p.name, pos: p.pos, start: null, end: null,
      price: info["门票"] != null ? +info["门票"] : info["人均"] != null ? +info["人均"] : info["价格"] != null ? +info["价格"] : null,
      cost: null, tickets: null, code: null, duration: m ? { min: +m[1], max: +m[2] } : null,
      hours: info["开放"] || info["营业"] || null, category: info["类型"] || info["菜系"] || null, dishes: [],
      image: p.image || null, rating: p.rating ?? null, note: "", leg: null,
      raw: { position: p.name, type: kind === "restaurant" ? "lunch" : kind === "hotel" ? "accommodation" : "attraction", transports: [] },
    };
  }

  // 弹层里的所有动作
  $("#pop").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-act],[data-ver]"); if (!b) return;
    if (b.dataset.ver) { closePop(); return selectVersion(+b.dataset.ver); }
    const act = b.dataset.act, uid = b.dataset.uid, t = trip();
    const L = uid ? locate(uid) : null;
    const anchor = popCtx && popCtx.nodeType ? popCtx : b;
    switch (act) {
      case "archiveHistory": closePop(); return editor.history();
      case "planChangeTime": closePop(); if (L?.item && !L.item.lock) return editor.editTime(L.item); return;
      case "planReplace": closePop(); if (L?.item && !L.item.lock) return editor.replace(L.item); return;
      case "planRemove": closePop(); if (L?.item && !L.item.lock) return editor.removeVisit(L.item); return;
      case "moveMenu": return openPop(anchor, `<div class="ph">移动到</div>` + destOptions("moveTo", uid), anchor);
      case "copyMenu": return openPop(anchor, `<div class="ph">复制到</div>` + destOptions("copyTo", uid), anchor);
      case "moveTo": case "copyTo": {
        const to = +b.dataset.to; let x = L.item;
        if (act === "moveTo") L.list.splice(L.i, 1);
        else { x = { ...clone(x), uid: x.uid + "c" + Date.now().toString(36) }; delete x.lock; }
        x.leg = null; (to < 0 ? t.pending : t.days[to].items).push(x);
        closePop(); return commit(act === "moveTo" ? "已移动" : "已复制");
      }
      case "up": case "down": {
        const j = L.i + (act === "up" ? -1 : 1); [L.list[L.i], L.list[j]] = [L.list[j], L.list[L.i]];
        L.list[L.i].leg = null; L.list[j].leg = null; closePop(); return commit();
      }
      case "del": L.list.splice(L.i, 1); state.sel.delete(uid); closePop(); return commit("已删除");
      case "visitLock": {
        const item = L.item, unlock = Boolean(item.lock);
        if (unlock) delete item.lock;
        else item.lock = ["station","airport"].includes(item.kind) ? "departure" : "reservation";
        closePop();
        return commit(unlock ? "已解除时间锁定" : fixedLabel[item.lock], true);
      }
      case "editTime": {
        closePop();
        const item = L.item;
        openModal(`<h3>修改「${esc(item.name)}」时刻</h3>
          <form id="timeForm"><div class="time-fields"><label>开始时刻<input name="start" type="time" value="${esc(item.start || "")}" required></label>
          <label>结束时刻<input name="end" type="text" inputmode="numeric" maxlength="5" placeholder="18:30" value="${esc(item.end || "")}" required></label></div>
          <p id="timeAlert" role="alert"></p><div class="btns"><button type="button" data-close>取消</button><button class="pri">应用修改</button></div></form>`);
        $("#timeForm").onsubmit = event => {
          event.preventDefault();
          const data = new FormData(event.currentTarget), start = data.get("start"), end = data.get("end").trim();
          if (clock(start) == null || clock(end) == null || clock(end) <= clock(start)) {
            $("#timeAlert").textContent = "结束时刻需要晚于开始时刻，请填写 HH:MM。";
            return;
          }
          item.start = start; item.end = end;
          if (item.raw) { item.raw.start_time = start; item.raw.end_time = end; }
          closeModal(); commit("已更新到访时刻");
        };
        return;
      }
      case "note": {
        closePop();
        openModal(`<h3>备注 · ${esc(L.item.name)}</h3><textarea id="noteText">${esc(L.item.note || "")}</textarea><div class="btns"><button data-close>取消</button><button class="pri" id="noteOk">保存</button></div>`);
        $("#noteText").focus();
        $("#noteOk").addEventListener("click", () => { L.item.note = $("#noteText").value.trim(); closeModal(); commit(); });
        return;
      }
      case "replace": {
        const p = popCtx.alts[+b.dataset.i]; const x = itemFromPoi(p);
        x.start = L.item.start; x.end = L.item.end; x.type = L.item.type === "attraction" || L.item.kind === p.kind ? L.item.type : x.type;
        x.raw = { ...L.item.raw, position: p.name, transports: [] };
        L.list[L.i] = x; closePop(); return commit(`已替换为「${p.name}」`);
      }
      case "renameDay": {
        closePop(); const d = t.days[+b.dataset.di];
        openModal(`<h3>第${d.day}天标题</h3><textarea id="dtText" placeholder="留空则自动生成">${esc(d.title || "")}</textarea><div class="btns"><button data-close>取消</button><button class="pri" id="dtOk">保存</button></div>`);
        $("#dtOk").addEventListener("click", () => { d.title = $("#dtText").value.trim() || null; closeModal(); commit(); });
        return;
      }
      case "insertDay": { const di = +b.dataset.di; t.days.splice(di + 1, 0, { day: 0, title: null, note: "", items: [] }); t.days.forEach((d, i) => (d.day = i + 1)); closePop(); return commit("已插入一天"); }
      case "clearDay": { const d = t.days[+b.dataset.di]; t.pending.push(...d.items.map((x) => ({ ...x, leg: null }))); d.items = []; closePop(); return commit("已移到待安排"); }
      case "delDay": {
        if (t.days.length <= 1) { closePop(); return toast("至少保留一天"); }
        const [d] = t.days.splice(+b.dataset.di, 1); t.pending.push(...d.items.map((x) => ({ ...x, leg: null }))); t.days.forEach((x, i) => (x.day = i + 1));
        state.sec = "overview"; closePop(); return commit("已删除，这一天的地点放到了待安排");
      }
      case "batchMove": case "batchCopy": {
        const to = +b.dataset.to; const dst = to < 0 ? t.pending : t.days[to].items;
        [...state.sel].forEach((u) => {
          const l = locate(u); if (!l) return;
          let x = l.item;
          if (act === "batchMove") l.list.splice(l.i, 1);
          else { x = { ...clone(x), uid: u + "c" + Date.now().toString(36) }; delete x.lock; }
          x.leg = null; dst.push(x);
        });
        state.sel.clear(); closePop(); return commit("已处理所选地点");
      }
      case "routeModal": closePop(); return openRouteModal(uid, b.dataset.mode);
      case "legMode": L.item.legChoice = b.dataset.m; closePop(); return commit(null, true);
      case "legBatch": return openPop(anchor, `<div class="ph">整个行程的同城交通都改为</div>` + ["car", "transit", "walk"].map((c) =>
        `<button class="mi-row" data-act="legBatchSet" data-m="${c}">${ic(CAT_UI[c][0])}${CAT_UI[c][1]}<span class="r">${c === "walk" ? "远距离会自动改用其他方式" : ""}</span></button>`).join("")
        + `<div class="sep"></div><button class="mi-row" data-act="legBatchSet" data-m="">${ic("refresh")}恢复规划方案</button>`, anchor);
      case "legBatchSet": {
        const m = b.dataset.m;
        t.days.forEach((d) => d.items.forEach((x, i) => {
          if (!i) return;
          if (!m) { delete x.legChoice; return; }
          const info = legInfo(d.items[i - 1], x);
          // 步行超过 3 公里不现实，保留原方式
          if (m === "walk" && info && info.opts.walk && info.opts.walk.km > 3) return;
          x.legChoice = m;
        }));
        closePop(); return commit("已批量更改交通方式", true);
      }
      case "clearDate": t.startDate = null; closePop(); renderTop(); renderChips(); return commit();
      case "dlView": closePop(); return download(`${t.id}.view.json`, t);
      case "dlRaw": closePop(); return download(`${t.id}.json`, toChinaTravel(t));
      case "print": closePop(); return window.print();
      case "dupVersion": { closePop(); const c = clone(t); c.id = t.id + "-" + Date.now().toString(36); c.query = "（手动复制的版本）"; group().versions.push(c); return selectVersion(group().versions.length - 1); }
      case "resetLocal": closePop(); try { Object.keys(localStorage).filter((k) => k.startsWith("st.")).forEach((k) => localStorage.removeItem(k)); } catch { /* ignore */ } return location.reload();
      case "clearChat": closePop(); delete state.chat[group() ? group().id : "_new"]; saveChat(); return renderChat();
      case "newTrip": closePop(); return selectGroup(-1);
      case "rate": state.rateMin = +b.dataset.v; LS.set("rateMin", state.rateMin); closePop(); renderFilters(); return drawRecs();
      case "layer": closePop(); return setBase(b.dataset.v);
    }
  });

  function renderBatch() {
    const bar = $("#batchBar");
    bar.hidden = !state.sel.size || !trip();
    if (bar.hidden) return;
    bar.innerHTML = `已选 ${state.sel.size} 项<span class="spacer"></span><button data-b="move">移动到</button><button data-b="copy">复制到</button><button data-b="del">删除</button><button data-b="cancel">取消</button>`;
  }
  $("#batchBar").addEventListener("click", (e) => {
    const b = e.target.closest("[data-b]"); if (!b) return;
    const k = b.dataset.b;
    if (k === "cancel") { state.sel.clear(); return renderPlan(); }
    if (k === "del") { [...state.sel].forEach((u) => { const l = locate(u); if (l) l.list.splice(l.i, 1); }); state.sel.clear(); return commit("已删除所选地点"); }
    openPop(b, destOptions(k === "move" ? "batchMove" : "batchCopy", ""));
  });

  // ======================= 地图 =======================
  const map = L.map("map", { zoomControl: false, attributionControl: true, maxZoom:18 }).setView([30.26, 120.16], 12);
  const weatherPane = map.createPane("weather", map.getPane("mapPane"));
  weatherPane.classList.add("weather-atmosphere");
  weatherPane.setAttribute("aria-hidden", "true");
  function positionWeatherPane() {
    const {x, y} = map.getSize();
    weatherPane.style.width = x + "px";
    weatherPane.style.height = y + "px";
    L.DomUtil.setPosition(weatherPane, map.containerPointToLayerPoint([0,0]));
  }
  map.on("move zoom resize", positionWeatherPane);
  positionWeatherPane();
  const planningLayer = L.layerGroup().addTo(map);
  let planningPlaces = null;
  const planningMarkers = new Map();
  const placeChoiceLabels = {want:"这趟想去", must:"这趟必去", skip:"这趟跳过"};

  function showPlanningPlaces(selection, refresh = false) {
    const changedPlaces = planningPlaces?.places !== selection?.places;
    const changedChoices = JSON.stringify(planningPlaces?.choices) !== JSON.stringify(selection?.choices);
    planningPlaces = selection;
    if (!changedPlaces && !changedChoices && !refresh) return;
    $("#layout").classList.toggle("planning-map", Boolean(selection));
    planningLayer.clearLayers();
    planningMarkers.clear();
    if (!selection) return;
    for (const place of selection.places) {
      const selected = selection.choices[place.id];
      const favorite = {id:"attraction:" + place.name, name:place.name,
        city:selection.city, kind:"attraction", pos:[place.location.lat, place.location.lon]};
      const saved = state.favs.some(item => sameFavoritePlace(item,favorite));
      const marker = L.marker([place.location.lat, place.location.lon], {
        title: place.name,
        icon: L.divIcon({className:"", iconSize:null, html:
          `<div class="rec"><div class="dotg ${saved ? "fav" : ""}"></div><div class="lb">${esc(place.name)}</div></div>`}),
      });
      const choices = Object.entries(placeChoiceLabels).map(([value, label]) =>
        `<button type="button" data-planning-choice="${value}" aria-pressed="${selected === value}">${label}</button>`).join("");
      marker.bindPopup(`<div class="pp"><h4>${esc(place.name)}</h4><div class="acts planning-map-actions">${choices}
        ${selected ? '<button type="button" data-planning-choice="clear">撤销本次选择</button>' : ""}
        <button type="button" data-planning-choice="favorite">${saved ? "取消收藏" : "收藏备用"}</button>
        </div></div>`);
      marker.on("popupopen", event => {
        event.popup.getElement().querySelectorAll("[data-planning-choice]").forEach(button => {
          button.onclick = async () => {
            const choice = button.dataset.planningChoice;
            button.disabled = true;
            try {
              if (choice === "favorite") {
                const index = state.favs.findIndex(item => sameFavoritePlace(item,favorite));
                const next = index < 0 ? [...state.favs, favorite]
                  : state.favs.filter((_, i) => i !== index);
                await API.setFavorites(next);
                state.favs = next;
                if (planningPlaces === selection) showPlanningPlaces(selection, true);
                toast(index < 0 ? "已收藏备用" : "已取消收藏");
              } else if (await online.choosePlace(place.id, choice)) {
                toast(placeChoiceLabels[choice] || "已撤销本次选择");
              }
            } catch (error) { toast(error.message, 4500); }
            finally { button.disabled = false; }
          };
        });
      });
      marker.addTo(planningLayer);
      planningMarkers.set(place.id, marker);
    }
    if (changedPlaces && !phoneLayout.matches) {
      setMap(true);
      fitPlanningPlaces();
    }
  }

  function fitPlanningPlaces() {
    if (planningMarkers.size) map.fitBounds([...planningMarkers.values()].map(marker => marker.getLatLng()),
                                            {padding:[45, 52], maxZoom:14, animate:false});
  }

  function focusPlanningPlace(id) {
    const marker = planningMarkers.get(id);
    if (!marker) return;
    setMap(true);
    requestAnimationFrame(() => {
      if (planningMarkers.get(id) !== marker) return;
      map.stop();
      map.setView(marker.getLatLng(), Math.max(map.getZoom(), 15), {animate:false});
      marker.openPopup();
    });
  }
  const roadCredit = '道路参考 FOSSGIS / <a href="https://www.openstreetmap.org/copyright">© OpenStreetMap contributors</a> · <a href="https://www.openstreetmap.org/fixthemap">地图纠错</a>';
  map.attributionControl.addAttribution(roadCredit);
  L.control.scale({ imperial: false, position: "bottomleft" }).addTo(map);
  const amapStreetTiles = (options) => L.tileLayer("https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}",
    {subdomains:"1234", maxZoom:18, attribution:"© 高德地图", ...options});
  const BASES = {
    std: ["高德道路地图", amapStreetTiles({detectRetina:true, maxNativeZoom:17})],
    detail: ["高德街道细节", amapStreetTiles({tileSize:128, zoomOffset:1, maxNativeZoom:17})],
    sat: ["高德卫星图", L.layerGroup([
      L.tileLayer("https://webst0{s}.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}", { subdomains: "1234", maxZoom: 18, attribution: "© 高德地图" }),
      L.tileLayer("https://webst0{s}.is.autonavi.com/appmaptile?style=8&x={x}&y={y}&z={z}", { subdomains: "1234", maxZoom: 18 })])],
  };
  let baseKey = LS.get("base", "std");
  function setBase(key) {
    baseKey = Object.hasOwn(BASES, key) ? key : "std";
    Object.values(BASES).forEach(([, layer]) => map.removeLayer(layer));
    BASES[baseKey][1].addTo(map);
    LS.set("base", baseKey);
  }
  setBase(baseKey);

  const routeLayer = L.layerGroup().addTo(map);
  const directionPane = map.createPane("route-directions");
  directionPane.style.zIndex = "430";
  directionPane.style.pointerEvents = "none";
  const directionLayer = L.layerGroup().addTo(map);
  const directedPaths = [];
  const pinLayer = L.layerGroup().addTo(map);
  const pins = {}; // uid -> marker
  const mapRouteMisses = new Set(), mapRoutesInFlight = new Set();
  let mapRouteRequests = new Map();

  function mapRouteRequest(mode, from, to) {
    if (!mode || !from || !to || from.every((v, i) => v === to[i]) || !API.canLookupRoute) return null;
    return { key: rkey(mode, from, to), mode, from, to };
  }

  const stationName = name => (name || "").replace(/\s/g, "").replace(/(?:[-－]地铁站|[（(]地铁站[）)]|地铁站)$/, "");

  function matchingTransitPath(segment, parts) {
    const type = segment.mode === "metro" ? "subway" : segment.mode;
    const matches = (name, pos, station, point) => point &&
      (!station || !name || stationName(name) === stationName(station)) && API.haversine(pos, point) <= 0.25;
    const first = parts.findIndex(part => part.type === type &&
      matches(segment.from, segment.fromPos, part.from, part.path?.[0]));
    if (first === -1) return null;
    const path = [];
    for (const part of parts.slice(first)) {
      const points = part.path;
      if ((part.type !== type && part.type !== "walk") || !points?.length ||
          (part.type === type && !hasPath(points)) ||
          (path.length && API.haversine(path.at(-1), points[0]) > 0.05)) return null;
      path.push(...points);
      if (part.type === type && matches(segment.to, segment.toPos, part.to, points.at(-1))) return path;
    }
    return null;
  }

  function archivedSegmentRoute(s) {
    if (hasPath(s.path)) return {path: s.path, source: "plan"};
    const mode = s.mode === "metro" ? "subway" : CAT[s.mode];
    const {fromPos: from, toPos: to} = s;
    const cached = getCached(mode, from, to);
    if (isRoute(cached)) {
      if ((mode === "walk" || mode === "car") && hasPath(cached.path)) {
        return {path: cached.path, source: cached.source};
      }
      const path = matchingTransitPath(s, cached.segments || []);
      if (path) return {path, source: cached.source};
      return {reason: "没有匹配计划站点的完整线路图"};
    }
    const reference = roadReference(mode, from, to);
    if (hasPath(reference?.path)) return {path: reference.path, source: reference.source};
    return {request: mapRouteRequest(mode, from, to)};
  }

  function loadMapRoutes(city) {
    for (const request of mapRouteRequests.values()) {
      if (mapRoutesInFlight.size >= 2) return;
      const {key, mode, from, to} = request;
      if (mapRoutesInFlight.has(key) || mapRouteMisses.has(key)) continue;
      mapRoutesInFlight.add(key);
      void API.getRoute({mode, from, to, city, signal: AbortSignal.timeout(20000)})
        .then((result) => {
          if (isRoute(result) && result.mode === mode) routeCache.set(key, result);
          else mapRouteMisses.add(key);
        })
        .catch(() => mapRouteMisses.add(key))
        .finally(() => {
          mapRoutesInFlight.delete(key);
          if (mapRouteRequests.has(key)) {
            drawRoute(false, true);
            if (!trip()?.archive && !mapRouteMisses.has(key)) renderPlan();
          } else if (trip()) loadMapRoutes(trip().city);
        });
    }
  }

  $("#routeMapRetry").onclick = () => {
    for (const key of mapRouteRequests.keys()) mapRouteMisses.delete(key);
    drawRoute(false, true);
  };

  function routeLabelPoint(path) {
    const distances = path.slice(1).map((p, i) => API.haversine(path[i], p));
    let remaining = distances.reduce((sum, km) => sum + km, 0) / 2;
    for (const [i, km] of distances.entries()) {
      if (km > 0 && remaining <= km) return path[i].map((v, axis) => v + (path[i+1][axis] - v) * remaining / km);
      remaining -= km;
    }
    return path[0];
  }

  // 推荐地点（绿色），近的合并成数字气泡
  const recLayer = L.markerClusterGroup({
    maxClusterRadius: 60, showCoverageOnHover: false, zoomToBoundsOnClick: false, spiderfyOnMaxZoom: false, disableClusteringAtZoom: 17,
    iconCreateFunction: (c) => {
      const ms = c.getAllChildMarkers().sort((a, b) => (b.options.poi.heat || 0) - (a.options.poi.heat || 0));
      return L.divIcon({ className: "", iconSize: null, html: `<div class="gcluster"><div class="n">${c.getChildCount()}</div><div class="lb">${esc(ms[0].options.poi.name.slice(0, 8))}等</div></div>` });
    },
  }).addTo(map);
  recLayer.on("clusterclick", (e) => {
    const ms = e.layer.getAllChildMarkers();
    const kinds = new Set(ms.map((m) => m.options.poi.kind));
    const title = kinds.size === 1 ? KIND[[...kinds][0]].label : "地点";
    const sorted = ms.slice().sort((a, b) => (b.options.poi.heat || 0) - (a.options.poi.heat || 0));
    const html = `<div class="plist"><h4>${title}${ms.length}个</h4><div class="scroll">${sorted.map((m, i) => `<div class="row" data-ri="${i}"><span>${esc(m.options.poi.name)}</span>${m.options.poi.heat != null ? `<b class="heat">${ic("fire")} ${m.options.poi.heat}</b>` : ""}</div>`).join("")}</div></div>`;
    const pop = L.popup({ maxWidth: 400, className: "plist-pop" }).setLatLng(e.layer.getLatLng()).setContent(html).openOn(map);
    pop.getElement().addEventListener("click", (ev) => {
      const r = ev.target.closest("[data-ri]"); if (!r) return;
      const m = sorted[+r.dataset.ri]; map.closePopup();
      recLayer.zoomToShowLayer(m, () => m.openPopup());
    });
  });

  function mapPlaceName(item) {
    return (item.kind === "airport" || item.kind === "station") && item.place ? item.place : item.name;
  }

  function pinIcon(x, n, { dim, label, hl }) {
    const k = KIND[x.kind] ? x.kind : "attraction";
    return L.divIcon({
      className: "", iconSize: [34, 42], iconAnchor: [17, 40], popupAnchor: [0, -38],
      html: `<div class="pin ${k} ${dim ? "dim" : ""} ${hl ? "hl" : ""}"><div class="b"><b>${n}</b>${ic(KIND[k].icon)}</div>${label ? `<div class="plabel">${esc(mapPlaceName(x))}</div>` : ""}</div>`,
    });
  }
  function itemPopup(x, d) {
    const [m1, m2] = itemMeta(x);
    const nearby = x.pos ? trip()?.days.flatMap(day => day.items.filter(item => item.uid !== x.uid && item.pos &&
      map.distance(x.pos, item.pos) < 24).map(item => ({item, day:day.day}))) : [];
    const label = TYPE_CN[x.type] || KIND[x.kind]?.label || "";
    const terminal = (x.kind === "airport" || x.kind === "station") && x.place
      ? x.place === x.raw?.start ? "出发" : x.place === x.raw?.end ? "抵达" : "" : "";
    const context = terminal ? terminal + KIND[x.kind].label : label;
    return `<div class="pp"><h4>${esc(mapPlaceName(x))}</h4><div><span class="k">第${d}天 · ${context}</span></div>${m1 ? `<div>${m1}</div>` : ""}${m2 ? `<div>${m2}</div>` : ""}
      <div class="acts"><button class="solid" data-pp-focus="${x.uid}">在行程中查看</button></div>
      ${nearby?.length ? `<details class="map-close-stops"><summary>附近重叠的地点 · ${nearby.length}</summary>
        ${nearby.map(({item,day}) => `<button data-pp-focus="${esc(item.uid)}">第${day}天 · ${esc(mapPlaceName(item))}</button>`).join("")}</details>` : ""}</div>`;
  }

  function showMapStop(item, day) {
    const card = $("#mapStop");
    card.innerHTML = `<button type="button" class="ib map-stop-close" aria-label="关闭地点详情">${ic("close")}</button>` + itemPopup(item, day);
    card.hidden = false;
    card.scrollTop = 0;
    queueMapLabels();
  }

  function closeMapStop() {
    const card = $("#mapStop");
    if (card.hidden) return;
    card.hidden = true;
    queueMapLabels();
  }

  function renderRouteDirections() {
    directionLayer.clearLayers();
    for (const {path, from, to} of directedPaths) {
      const start = map.project(path[0]);
      const end = map.project(path.at(-1));
      const ordered = from && to &&
        start.distanceTo(map.project(to)) + end.distanceTo(map.project(from)) <
        start.distanceTo(map.project(from)) + end.distanceTo(map.project(to))
        ? [...path].reverse() : path;
      const points = ordered.map(point => map.project(point));
      const distances = [0];
      for (let i = 1; i < points.length; i++) {
        distances.push(distances[i - 1] + points[i].distanceTo(points[i - 1]));
      }
      const length = distances.at(-1);
      if (length < 56) continue;
      const count = Math.min(3, Math.ceil(length / 145));
      for (let i = 0; i < count; i++) {
        const distance = length * (i + .36) / count;
        const next = distances.findIndex(value => value > distance);
        const vector = points[next].subtract(points[next - 1]);
        const angle = Math.atan2(vector.y, vector.x) * 180 / Math.PI;
        const fraction = (distance - distances[next - 1]) / (distances[next] - distances[next - 1]);
        const position = map.unproject(points[next - 1].add(vector.multiplyBy(fraction)));
        L.marker(position, {pane:"route-directions", keyboard:false, interactive:false,
          icon:L.divIcon({className:"", iconSize:[16,16],
            html:`<span class="atlas-route-direction" aria-hidden="true" style="--direction:${angle}deg">${ic("chevronRight")}</span>`})
        }).addTo(directionLayer);
      }
    }
  }

  function drawRoute(fit = true, geometryOnly = false) {
    selectWeatherDay(trip()?.archive ? activeDay() : 0);
    routeLayer.clearLayers();
    directionLayer.clearLayers();
    directedPaths.length = 0;
    mapRouteRequests = new Map();
    if (!geometryOnly) {
      closeMapStop();
      pinLayer.clearLayers();
      for (const k in pins) delete pins[k];
    }
    const t = trip(); $("#routeKey").hidden = !t; if (!t) return;
    const ad = activeDay();
    const atlasMap = Boolean(window.AtlasView && t.archive);
    const atlasOverview = atlasMap && !ad;
    const bounds = [];
    let drawn = 0, loading = 0;
    const missing = [];
    function track(path, request, detail = "") {
      if (hasPath(path)) { drawn++; return; }
      if (request && !mapRouteMisses.has(request.key)) loading++;
      else missing.push(detail);
      if (request) mapRouteRequests.set(request.key, request);
    }
    t.days.forEach((d, di) => {
      const active = !ad || ad === d.day;
      if (!active && t.archive && (phoneLayout.matches || atlasMap)) return;
      const c = ad && !atlasMap ? PIN_COLOR : dayColor(d.day);
      const allPts = [];
      let prev = null;
      d.items.forEach((x, xi) => {
        const target = x.pos;
        const info = t.archive ? archivedLeg(x) : xi ? legInfo(d.items[xi - 1], x) : null;
        const ch = info && info.chosen;
        const showRoute = active || !t.archive;
        const segs = ch?.segments?.length && (!hasPath(ch.path) || ch.segments.some(s => hasPath(s.path))) ? ch.segments
          : hasPath(ch?.path) ? [{ mode: ch.mode, fromPos: prev, toPos: target, path: ch.path }]
          : !t.archive && prev && target ? [{ mode: ch ? ch.mode : null, fromPos: prev, toPos: target, path: null }] : [];
        let labelPath;
        segs.forEach((s) => {
          if (!showRoute) return;
          const a = s.fromPos || (!t.archive && prev), b = s.toPos || (!t.archive && target);
          if (t.archive && a && b && a.every((v, i) => v === b[i])) return;
          const geometry = t.archive ? archivedSegmentRoute(s) : {path:s.path, source:ch?.source};
          const known = hasPath(geometry.path);
          const endpoints = s.from && s.to ? `${s.from} → ${s.to}` : "";
          const reason = geometry.reason || "暂未取得线路图";
          if (t.archive) track(geometry.path, geometry.request, endpoints ? `${endpoints}：${reason}` : reason);
          if (atlasOverview && !known) return;
          if (!known && (!a || !b)) return;
          if (!known && segs.length > 1 && !s.fromPos && !s.toPos) return;
          const path = known ? geometry.path : [a, b];
          if (known) {
            if (!labelPath || s.mode === x.leg?.mode) labelPath = path;
            if (atlasMap && ad === d.day) directedPaths.push({path,from:a,to:b});
            L.polyline(path, { color: "#fff", weight: atlasOverview ? 5 : active ? 9 : 5,
              opacity: atlasOverview ? .65 : active ? .95 : .4, interactive: false }).addTo(routeLayer);
          }
          const line = L.polyline(path, { color: known ? c : "#566987", weight: atlasOverview ? 3 : active ? (known ? 5 : 3) : 3,
            opacity: atlasOverview ? .75 : active ? (known ? 0.96 : 0.72) : 0.28, dashArray: known ? null : "5 8", lineCap: "round",
            interactive: Boolean(active && ch), bubblingMouseEvents: false,
            className: known ? "road-reference-line" : "unverified-route-line" }).addTo(routeLayer);
          if (atlasOverview) {
            line.bindTooltip(`查看第 ${d.day} 天路线`, {sticky:true});
            line.on("click", () => selectMapDay(`day-${d.day}`));
          } else if (active && ch) {
            const source = {amap: "高德线路参考", osrm: "OpenStreetMap 道路参考", osm_reference: "OpenStreetMap 道路参考", plan: "行程路线"};
            const caption = known ? source[geometry.source] || "道路参考" : reason;
            line.bindTooltip(esc(endpoints ? `${endpoints} · ${caption}` : caption), {sticky: true});
            line.on("click", () => openRouteModal(x.uid));
          }
          allPts.push(...path);
        });
        if (!t.archive && active && prev && target && info) {
          const request = API.mode === "mock" ? mapRouteRequest(info.cur, prev, target) : null;
          track(labelPath, request);
        }
        // 当天视图：每段交通的用时标签
        const [startPoint, endPoint] = t.archive ? (archivedEndpoints(x) || []).map(p => p.pos) : [prev, target];
        if (ad === d.day && startPoint && endPoint && Number.isFinite(ch?.minutes)) {
          const mid = routeLabelPoint(labelPath || [startPoint, endPoint]);
          const duration = `${t.archive ? "计划 " : ""}${ch.minutes}分钟`;
          L.marker(mid, { icon: L.divIcon({ className: "", iconSize: null, html: `<div class="dur" style="--c:${c}" title="查看路线详情">${ic(CAT_UI[ch.mode][0])}${duration}</div>` }) })
            .on("click", () => openRouteModal(x.uid)).addTo(routeLayer);
        }
        if (target) { allPts.push(target); prev = target; }
      });
      // 总览：每天中间一个“第N天”标签
      if (!ad && allPts.length) {
        const mid = (atlasOverview && d.items.find(x => x.kind === "attraction" && x.pos)?.pos) ||
          allPts[Math.floor(allPts.length / 2)];
        const marker = L.marker(mid, {interactive:atlasOverview, keyboard:atlasOverview,
          title:atlasOverview ? `查看第 ${d.day} 天路线` : "",
          icon:L.divIcon({className:atlasOverview ? "atlas-day-marker" : "",iconSize:null,html:
            `${atlasOverview ? `<span class="atlas-day-leader" style="--c:${c}" aria-hidden="true"></span>` : ""}<div class="daypill" style="--c:${c}">第${d.day}天${atlasOverview ? ic("chevronRight") : ""}</div>`})
        }).addTo(routeLayer);
        if (atlasOverview) marker.on("click", () => selectMapDay(`day-${d.day}`));
      }
      if (active && t.archive) bounds.push(...allPts);
      if (geometryOnly || atlasOverview) return;
      d.items.forEach((x, i) => {
        if (!x.pos) return;
        const label = active && (Boolean(ad) || (di === 0 && i === 0) || state.hl === x.uid);
        const mobileStop = phoneLayout.matches && t.archive;
        const m = L.marker(x.pos, { icon: pinIcon(x, i + 1, { dim: !active, label, hl: state.hl === x.uid }),
          title: mapPlaceName(x), zIndexOffset: active ? 1000 : 0, riseOnHover: true, bubblingMouseEvents: false });
        if (!mobileStop) m.bindPopup(itemPopup(x, d.day));
        m.on("click", () => {
          focusItem(x.uid, !phoneLayout.matches && activeDay() === d.day, true);
          if (mobileStop) showMapStop(x, d.day);
        });
        m.addTo(pinLayer);
        m.options.meta = { x, n: i + 1, active, label };
        pins[x.uid] = m;
        if (active) bounds.push(x.pos);
      });
    });
    const road = $("#routeMapDrawn"), pending = $("#routeMapMissing"), waiting = $("#routeMapLoading");
    road.hidden = !drawn; road.textContent = `已绘制 ${drawn} 段`;
    pending.hidden = !missing.length; pending.textContent = `${missing.length} 段缺少线路图`;
    pending.title = missing.filter(Boolean).join("\n");
    waiting.hidden = !loading; waiting.textContent = `正在查询 ${loading} 段`;
    $("#routeMapRetry").hidden = ![...mapRouteRequests.keys()].some(key => mapRouteMisses.has(key));
    $("#routeKey").hidden = !drawn && !missing.length && !loading;
    if (fit && bounds.length) {
      const size = map.getSize();
      map.fitBounds(bounds, {
        padding:[Math.min(90, Math.round(size.x * .13)), Math.min(80, Math.round(size.y * .13))],
        maxZoom:15, animate:!phoneLayout.matches
      });
    }
    renderRouteDirections();
    queueMapLabels();
    loadMapRoutes(t.city);
    if (t.archive) return;
    // 当前显示的各段，按所选方式拉真实路线，拿到后重画（只拉缺的，不会循环）
    const reqs = [];
    t.days.forEach((d) => { if (ad && ad !== d.day) return; d.items.forEach((x, i) => {
      const p0 = d.items[i - 1]; if (!i || !p0.pos || !x.pos) return;
      const info = legInfo(p0, x); if (info) reqs.push({ mode: info.cur, from: p0.pos, to: x.pos });
    }); });
    fetchRoutes(reqs, () => { drawRoute(false); renderPlan(); });
  }

  function hoverPin(uid) {
    for (const [u, m] of Object.entries(pins)) {
      const on = u === uid || u === state.hl;
      const { x, n, active, label } = m.options.meta;
      if (m._hlOn !== on) { m.setIcon(pinIcon(x, n, { dim: !active, label: label || on, hl: on })); m.setZIndexOffset(on ? 3000 : active ? 1000 : 0); m._hlOn = on; }
    }
    queueMapLabels();
  }

  let mapLabelFrame;
  function queueMapLabels() {
    cancelAnimationFrame(mapLabelFrame);
    mapLabelFrame = requestAnimationFrame(() => {
      const labels = $$("#map .plabel, #map .dur, #map .daypill, #map .atlas-route-direction");
      const priority = label => label.closest(".pin.hl") ? 0 : label.matches(".plabel") ? 1 : label.matches(".atlas-route-direction") ? 3 : 2;
      const frame = $("#map").getBoundingClientRect();
      const intersect = (a, b) => a.left < b.right + 4 && a.right + 4 > b.left && a.top < b.bottom + 4 && a.bottom + 4 > b.top;
      const occupied = $$("#map .pin:not(.dim) .b").map(pin => pin.getBoundingClientRect());
      const overlays = $$(".map-top, #mapWeather, #routeKey, .map-ctl, #mapDayControl, #mapStop, #mapFab")
        .filter(node => getComputedStyle(node).visibility === "visible")
        .map(node => node.getBoundingClientRect()).filter(box => box.width && box.height);
      const fits = box => box.left >= frame.left && box.right <= frame.right &&
        box.top >= frame.top && box.bottom <= frame.bottom &&
        !occupied.some(other => intersect(box, other)) && !overlays.some(other => intersect(box, other));
      const nearby = [[0,0],[0,-34],[0,34],[-42,-27],[42,-27],[-42,27],[42,27],[0,-62],[0,62]];
      for (const label of labels.sort((a, b) => priority(a) - priority(b))) {
        const dayMarker = label.closest(".atlas-day-marker");
        if (dayMarker) {
          const anchor = dayMarker.getBoundingClientRect();
          const halfWidth = label.offsetWidth / 2, halfHeight = label.offsetHeight / 2;
          let placed = false;
          for (const [x, y] of nearby) {
            const box = {
              left: anchor.left + x - halfWidth, right: anchor.left + x + halfWidth,
              top: anchor.top + y - halfHeight, bottom: anchor.top + y + halfHeight,
            };
            if (!fits(box)) continue;
            dayMarker.style.setProperty("--day-x", `${x}px`);
            dayMarker.style.setProperty("--day-y", `${y}px`);
            dayMarker.style.setProperty("--leader-length", `${Math.hypot(x, y)}px`);
            dayMarker.style.setProperty("--leader-angle", `${Math.atan2(y, x)}rad`);
            occupied.push(box);
            placed = true;
            break;
          }
          dayMarker.classList.toggle("map-label-occluded", !placed);
        } else {
          const box = label.getBoundingClientRect();
          const visible = fits(box);
          label.classList.toggle("map-label-occluded", !visible);
          if (visible) occupied.push(box);
        }
      }
    });
  }
  map.on("zoomend resize", renderRouteDirections);
  map.on("moveend zoomend resize", queueMapLabels);

  // 中栏 ⇄ 地图 联动
  function focusItem(uid, scrollList, fromMap = false) {
    const L0 = locate(uid); if (!L0) return;
    state.hl = uid;
    if ((!fromMap || scrollList) && L0.di >= 0 && (trip().archive || activeDay()) && activeDay() !== L0.di + 1) {
      state.sec = `day-${L0.di + 1}`; renderChips(); drawRoute(false);
      if (trip().archive) updateTripUrl();
    }
    $$(".it.hl").forEach((e) => e.classList.remove("hl"));
    const el = $(`.it[data-uid="${uid}"]`);
    if (el) {
      el.classList.add("hl");
      if (scrollList) {
        lockSpy = true;
        const behavior = matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
        el.scrollIntoView({block: "center", behavior});
        setTimeout(() => (lockSpy = false), 700);
      }
    }
    hoverPin(uid);
    const m = pins[uid];
    if (m && !fromMap) {
      const focusedTrip = trip();
      map.flyTo(m.getLatLng(), Math.max(map.getZoom(), 14), { duration: 0.5 });
      setTimeout(() => { if (trip() === focusedTrip && state.hl === uid) pins[uid]?.openPopup(); }, 520);
    }
    return el;
  }
  function openTimelineStop(uid) {
    map.closePopup();
    if (phoneLayout.matches) setMap(false);
    if (state.tab !== "detail") $("#mainTabs [data-tab=detail]").click();
    const card = focusItem(uid, true, true);
    if (card) {
      card.tabIndex = -1;
      card.focus({preventScroll: true});
    }
  }

  $("#mapStop").addEventListener("click", event => {
    const button = event.target.closest("[data-pp-focus]");
    if (button) openTimelineStop(button.dataset.ppFocus);
    else if (event.target.closest(".map-stop-close")) closeMapStop();
  });
  map.on("click popupopen", closeMapStop);
  map.on("popupopen", (e) => {
    const el = e.popup.getElement();
    el.querySelectorAll("[data-pp-focus]").forEach(b => b.addEventListener("click", () => openTimelineStop(b.dataset.ppFocus)));
    el.querySelectorAll("[data-pp-add]").forEach((b) => b.addEventListener("click", () => addPoi(b.dataset.ppAdd, b.dataset.to)));
    el.querySelectorAll("[data-pp-insert]").forEach(b => b.addEventListener("click", () => {
      const place = recPois[Number(b.dataset.ppInsert)];
      const currentTrip = trip();
      if (place?.kind !== "attraction" || !currentTrip?.archive || !group()?.canEdit) return;
      map.closePopup();
      editor.insertVisit(place.name, currentTrip.days, activeDay());
    }));
    el.querySelectorAll("[data-pp-meal]").forEach((b) => b.addEventListener("click", () => {
      const place = recPois[Number(b.dataset.ppMeal)];
      const currentTrip = trip();
      if (!place || place.kind !== "restaurant" || !currentTrip?.archive || !group()?.canEdit) return;
      map.closePopup();
      editor.replaceMeal(place.name, currentTrip.days, activeDay());
    }));
    el.querySelectorAll("[data-pp-fav]").forEach((b) => b.addEventListener("click", () => toggleFav(b.dataset.ppFav, b)));
  });

  // 路线预览使用高德底图和路径规划返回的坐标。
  const routeDialog = $("#routeDialog");
  const routeSelect = $("#rmMode");
  const routeSlot = $("#routeMapSlot");
  const routeMapStatus = $("#routeMapStatus");
  let routeRequest, routePreview;

  function closeRouteDialog() {
    routeRequest?.abort();
    routePreview?.remove();
    routePreview = null;
    routeDialog.close();
    if (trip()?.archive) drawRoute(false);
  }
  $("#routeClose").onclick = closeRouteDialog;
  routeDialog.addEventListener("cancel", event => {
    event.preventDefault();
    closeRouteDialog();
  });

  function openRouteModal(uid, mode) {
    const location = locate(uid);
    const item = location?.item;
    const archived = Boolean(trip()?.archive);
    const editable = Boolean(group()?.canEdit);
    const plannedModes = {walk:"walk", car:"taxi", transit:"metro"};
    const [from, visit] = archived && item ? archivedEndpoints(item) || [] : [item && prevOf(item), item];
    if (!from?.pos || !visit?.pos) return toast("缺少坐标，无法查询路线");
    if (from.pos.every((coordinate, index) => coordinate === visit.pos[index])) return toast("这两站的起终点相同");
    const current = routeChoiceMode((archived ? archivedLeg(item) : legInfo(from, visit)).cur);
    const city = trip().city;
    const startClock = item?.leg?.segments?.[0]?.start;
    const canDateLeg = archived && location.di >= 0 && /^([01]\d|2[0-3]):[0-5]\d$/.test(startClock);
    const dateField = $("#rmDepartureDateField"), dateInput = $("#rmDepartureDate");
    dateInput.value = "";
    const tripStart = canDateLeg ? reviewDate(trip(), group()) : "";
    if (tripStart) {
      const date = new Date(tripStart + "T00:00:00Z");
      if (Number.isFinite(date.getTime())) {
        date.setUTCDate(date.getUTCDate() + trip().days[location.di].day - 1);
        dateInput.value = date.toISOString().slice(0, 10);
      }
    }
    const nav = $("#rmNav"), retry = $("#rmRetry"), adopt = $("#rmUse"), note = $("#rmNote");
    let confirmed = null;

    $("#routeHeading").textContent = from.name + " → " + visit.name;
    const planned = $("#rmPlanned");
    planned.hidden = !archived;
    if (archived) {
      planned.textContent = "归档安排 · " + item.leg.segments.map(s =>
        `${s.start}–${s.end} ${MODE[s.mode]?.[1] || s.mode} ${s.from} → ${s.to}`).join(" · ");
    }
    routeSelect.value = routeChoiceMode(mode || current);
    closePop();
    if (!routeDialog.open) routeDialog.showModal();
    const sourceClock = new Intl.DateTimeFormat("zh-CN", {timeZone:"Asia/Shanghai", hour:"2-digit", minute:"2-digit", second:"2-digit"});

    routePreview?.remove();
    routePreview = L.map(routeSlot, {scrollWheelZoom:false, maxZoom:18});
    const preview = routePreview;
    const tiles = amapStreetTiles({detectRetina:true, maxNativeZoom:17}).addTo(preview);
    const pathLayer = L.featureGroup().addTo(preview);
    const endpoints = L.latLngBounds([from.pos, visit.pos]);
    [from, visit].forEach((point, index) => {
      const label = index === 0 ? "起点" : "终点";
      L.marker(point.pos, {
        title:label + "：" + point.name,
        icon:L.divIcon({
          className:"route-endpoint " + (index === 0 ? "route-start" : "route-end"),
          html:index === 0 ? "起" : "终",
          iconSize:[30,30],
        }),
      }).bindTooltip(esc(label + "：" + point.name)).addTo(preview);
    });
    let mapMessage = "";
    let tilesUnavailable = false;
    function showMapStatus() {
      routeMapStatus.hidden = false;
      routeMapStatus.textContent = tilesUnavailable
        ? "高德底图加载失败。可重新查询，或点「在高德打开」。"
        : mapMessage;
    }
    tiles.on("load", () => {
      tilesUnavailable = !routeSlot.querySelector(".leaflet-tile-loaded");
      showMapStatus();
    });
    function drawPreview(selected, result, loading) {
      const usable = result?.source === "amap" && !result.unavailable;
      const segments = usable ? (result.segments || []).filter(segment => hasPath(segment.path)) : [];
      if (usable && !segments.length && hasPath(result.path)) segments.push({type:selected,path:result.path});
      pathLayer.clearLayers();
      for (const segment of segments) {
        const walk = segment.type === "walk";
        L.polyline(segment.path, {
          color:walk ? "#247e69" : "#2461da", weight:5, opacity:.92,
          dashArray:walk ? "4 7" : null,
        }).addTo(pathLayer);
      }
      const bounds = L.latLngBounds(endpoints.getSouthWest(), endpoints.getNorthEast());
      if (segments.length) bounds.extend(pathLayer.getBounds());
      preview.fitBounds(bounds, {padding:[42,42], maxZoom:16, animate:false});
      const label = CAT_UI[selected][1];
      mapMessage = segments.length
        ? "高德" + label + "路线" + (selected === "transit" && segments.some(segment => segment.type === "walk") ? " · 绿色虚线为步行" : "")
        : loading ? "正在加载高德" + label + "路线…" : "暂未取得高德" + label + "路线图。可点「在高德打开」核对。";
      showMapStatus();
    }

    function showSummary(selected, result, fallback, loading = false, departure) {
      confirmed = isRoute(result) ? routeToOpt(result, selected) : fallback;
      adopt.textContent = editable ? (selected === "transit" ? "试排地铁" : "试排此方式") : "采用此方式";
      adopt.disabled = selected === current || loading || (!editable && !confirmed);
      adopt.hidden = archived ? !editable : (selected === "transit" && !confirmed);
      retry.disabled = loading;

      const label = CAT_UI[selected][1];
      const period = departure ? departure.replace("T", " ") + " 出发" : "";
      if (loading) {
        note.textContent = `正在查询高德${label}耗时…`;
        return;
      }
      const amapResult = result?.source === "amap";
      if (departure && (!amapResult || result.unavailable || !(result.durationSeconds > 0))) {
        note.textContent = period + " · 待核实。" + (amapResult && result.unavailable ? "高德未返回匹配的线路。" : "暂未取得该时段的高德接驳耗时。");
        return;
      }
      if (amapResult && result.unavailable) {
        note.textContent = `高德暂未找到可用的${label}路线。`;
        return;
      }
      const minutes = departure ? Math.ceil(result?.durationSeconds / 60) : result?.minutes;
      if (!amapResult || !Number.isFinite(minutes) || minutes <= 0) {
        note.textContent = `暂未获取高德${label}耗时。`;
        return;
      }

      const when = Date.parse(result.retrievedAt);
      const time = Number.isFinite(when) ? " · " + sourceClock.format(when) + " 查询" : "";
      const distance = Number.isFinite(result.km) && result.km > 0 ? " · " + fmtKm(result.km) : "";
      const queryTime = period ? "按 " + period : canDateLeg && selected === "transit" ? "未指定接驳日期" : "本次查询";
      const leaveTime = clock(startClock);
      const plannedArrival = clock(item.leg?.segments?.at(-1)?.end);
      const plannedMinutes = leaveTime != null && plannedArrival != null && plannedArrival >= leaveTime
        ? plannedArrival - leaveTime : item.leg?.minutes;
      const extra = archived ? minutes - plannedMinutes : 0;
      const warning = extra > 0 ? `。比原计划接驳多 ${extra} 分钟，请核对下一站到达时间。` : "";
      const lockTime = clock(item.start);
      const remaining = departure && item.lock && lockTime != null && leaveTime != null && lockTime >= leaveTime
        ? lockTime - leaveTime - minutes : null;
      const lockLabel = item.lock === "departure" ? "返程" : "预约";
      const margin = remaining == null ? "" : remaining >= 0 ? ` · 预计提前 ${remaining} 分钟抵达${lockLabel}地点` : ` · 预计比${lockLabel}时间晚 ${-remaining} 分钟`;
      note.textContent = `高德预计（${queryTime}）· ${label}约 ${minutes} 分钟${distance}${time}${margin}${warning}`;
    }

    async function selectMode(refresh = false) {
      routeRequest?.abort();
      const controller = new AbortController();
      routeRequest = controller;
      const selected = routeSelect.value;
      const dateApplicable = canDateLeg && selected === "transit";
      dateField.hidden = !dateApplicable;
      const departure = dateApplicable && dateInput.value ? dateInput.value + "T" + startClock : undefined;
      nav.href = navUrl(from, visit, selected);
      const cached = cachedLeg(selected, from, visit);
      const reference = roadReference(selected, from.pos, visit.pos);
      const fallback = isRoute(cached) ? routeToOpt(cached, selected) : reference || null;
      const lookup = API.canLookupRoute;
      drawPreview(selected, !departure && !refresh ? cached : null, lookup);
      if (refresh && tilesUnavailable) tiles.redraw();
      showSummary(selected, null, fallback, lookup, departure);
      if (!lookup) return;
      let result;
      try {
        const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(19000)]);
        result = await API.getRoute({from:from.pos, to:visit.pos, mode:selected, city, signal, refresh, departure});
      } catch { /* The same endpoints remain available through AMap. */ }
      if (!routeDialog.open || controller !== routeRequest || controller.signal.aborted) return;
      if (result?.mode !== selected || (departure && result?.requestedDeparture !== departure)) result = null;
      if (isRoute(result) && !result.requestedDeparture) {
        routeCache.set(rkey(selected, from.pos, visit.pos), result);
      }
      drawPreview(selected, result, false);
      showSummary(selected, result, fallback, false, departure);
    }

    routeSelect.onchange = () => { void selectMode(); };
    dateInput.onchange = () => { void selectMode(); };
    retry.onclick = () => { void selectMode(true); };
    adopt.onclick = () => {
      const selected = routeSelect.value;
      if (editable && plannedModes[selected] && selected !== current) {
        closeRouteDialog();
        return void editor.transport(item, plannedModes[selected]);
      }
      if (archived || !confirmed || prevOf(visit)?.uid !== from.uid) return;
      visit.legChoice = selected;
      const data = cachedLeg(selected, from, visit);
      if (isRoute(data)) {
        const minutes = data.source === "amap" && selected !== "walk" ? 5 : 60;
        visit.savedRoute = {key: rkey(selected, from.pos, visit.pos), expires: Date.now() + minutes * 60_000, data};
      } else delete visit.savedRoute;
      closeRouteDialog();
      commit("已更新这段的交通方式", true);
    };

    routeSelect.focus();
    void selectMode();
  }

  // ---- 推荐地点筛选 ----
  const FILTERS = ["attraction", "hotel", "restaurant", "shopping", "airport", "station", "fav"];
  function renderFilters() {
    $("#mapFilters").innerHTML = FILTERS.map((k) => {
      const on = state.filters.has(k);
      const rate = k === "attraction" && on ? `<button class="rate" data-rate>${state.rateMin ? state.rateMin + "分以上" : "全部评分"} ${ic("chevronDown")}</button>` : "";
      return `<div class="mf ${on ? "on" : ""}"><button data-f="${k}"><span class="mi ${k}">${ic(KIND[k].icon)}</span>${KIND[k].label}</button>${rate}</div>`;
    }).join("");
  }
  $("#mapFilters").addEventListener("click", (e) => {
    if (e.target.closest("[data-rate]")) {
      return openPop(e.target.closest("[data-rate]"), `<div class="ph">评分（数据接入后生效）</div>` + [[0, "全部评分"], [4, "4分以上"], [4.5, "4.5分以上"], [5, "5分以上"]].map(([v, l]) =>
        `<button class="mi-row" data-act="rate" data-v="${v}">${state.rateMin === v ? ic("check") : '<svg class="i"></svg>'}${l}</button>`).join(""));
    }
    const b = e.target.closest("[data-f]"); if (!b) return;
    const k = b.dataset.f; state.filters.has(k) ? state.filters.delete(k) : state.filters.add(k);
    LS.set("filters", [...state.filters]); renderFilters(); drawRecs();
  });
  $("#mfArrow").addEventListener("click", () => { const f = $("#mapFilters"); f.scrollLeft = f.scrollLeft + 240 >= f.scrollWidth - f.clientWidth ? 0 : f.scrollLeft + 240; });

  let recPois = [];
  async function drawRecs() {
    recLayer.clearLayers(); recPois = [];
    const req = ++drawRecs.seq;
    const t = trip();
    if (!t || !state.filters.size) return;
    const inPlan = new Set(); t.days.forEach((d) => d.items.forEach((x) => { inPlan.add(x.place); inPlan.add(x.name); }));
    const lists = await Promise.allSettled([...state.filters].map(k => k === "fav"
      ? state.favs.filter(p => p.city === t.city).map(p => ({ ...p, _fav: true }))
      : API.getPois({ city:t.city, kind:k, snapshot:t.archive?.snapshot })));
    if (req !== drawRecs.seq) return;
    const seen = new Set(), markers = [];
    const canReplaceMeal = t.archive && group()?.canEdit
      && t.days.some(day => day.items.some(item => item.kind === "restaurant" && !item.lock));
    lists.filter(r => r.status === "fulfilled").flatMap(r => r.value).forEach((p) => {
      const pin = JSON.stringify([p.id || p.name,p.pos]);
      if (!p.pos || inPlan.has(p.name) || seen.has(pin)) return;
      if (state.rateMin && p.rating != null && p.rating < state.rateMin) return;
      seen.add(pin);
      const i = recPois.push(p) - 1;
      const fav = state.favs.some(f => sameFavoritePlace(f,{...p,city:t.city}));
      const m = L.marker(p.pos, { poi: p, icon: L.divIcon({ className: "", iconSize: null, html: `<div class="rec"><div class="dotg ${p._fav ? "fav" : ""}"></div><div class="lb">${esc(p.name)}</div></div>` }) });
      const info = placeDetails(p.info);
      const meal = canReplaceMeal && p.kind === "restaurant" ? `<button class="solid" data-pp-meal="${i}">试换用餐</button>` : "";
      const visit = group()?.canEdit && p.kind === "attraction" ? `<button class="solid" data-pp-insert="${i}">试加到行程</button>` : "";
      const add = t.archive ? meal + visit : `<button class="solid" data-pp-add="${i}" data-to="${activeDay() ? activeDay() - 1 : -1}">${activeDay() ? `加入第${activeDay()}天` : "加入待安排"}</button>`;
      m.bindPopup(`<div class="pp"><h4>${esc(p.name)}</h4>${p.heat != null ? `<div style="color:var(--fire)">${ic("fire")} ${p.heat}</div>` : ""}${info}
        <div class="acts">${add}<button data-pp-fav="${i}">${fav ? "取消收藏" : "收藏"}</button></div></div>`);
      markers.push(m);
    });
    recLayer.addLayers(markers);
    if (lists.some(r => r.status === "rejected")) {
      toast(markers.length ? "部分类别未加载" : "地点读取失败，请重新选择类别");
    }
  }
  drawRecs.seq = 0;
  function addPoi(i, to) {
    const p = recPois[+i]; if (!p || trip()?.archive) return;
    const x = itemFromPoi(p); to = +to;
    (to < 0 ? trip().pending : trip().days[to].items).push(x);
    map.closePopup(); commit(`已加入${to < 0 ? "待安排" : `第${to + 1}天`}`); drawRecs();
  }
  async function toggleFav(i, btn) {
    const p = recPois[+i]; if (!p) return;
    const favorite = {...p, city:trip().city, _fav:undefined};
    const k = state.favs.findIndex(f => sameFavoritePlace(f,favorite));
    if (k >= 0) state.favs.splice(k, 1); else state.favs.push(favorite);
    await API.setFavorites(state.favs);
    btn.textContent = k >= 0 ? "收藏" : "取消收藏"; toast(k >= 0 ? "已取消收藏" : "已收藏");
    if (state.filters.has("fav")) drawRecs();
  }

  // ---- 地图右侧按钮 ----
  $("#ctlFit").addEventListener("click", () => planningPlaces ? fitPlanningPlaces() : drawRoute(true));
  $("#ctlIn").addEventListener("click", () => map.zoomIn());
  $("#ctlOut").addEventListener("click", () => map.zoomOut());
  $("#ctlLayer").addEventListener("click", (e) => openPop(e.currentTarget, Object.entries(BASES).map(([k, [l]]) => `<button class="mi-row" data-act="layer" data-v="${k}">${baseKey === k ? ic("check") : '<svg class="i"></svg>'}${l}</button>`).join("")));
  let meMarker = null;
  $("#ctlLocate").addEventListener("click", () => {
    if (!navigator.geolocation) return toast("浏览器不支持定位");
    navigator.geolocation.getCurrentPosition((p) => {
      const ll = [p.coords.latitude, p.coords.longitude];
      if (meMarker) meMarker.remove();
      meMarker = L.circleMarker(ll, { radius: 8, color: "#fff", weight: 3, fillColor: "#2f6bff", fillOpacity: 1 }).addTo(map);
      map.flyTo(ll, 14);
    }, (err) => toast("定位失败：" + err.message));
  });
  const phoneLayout = matchMedia("(max-width: 700px)");
  function setMap(on) {
    if (!on) closeMapStop();
    $("#layout").classList.toggle("no-map", !phoneLayout.matches && !on);
    $("#layout").classList.toggle("show-map", phoneLayout.matches && on);
    $("#mapFab").hidden = !phoneLayout.matches && on;
    $("#mapFab").setAttribute("aria-pressed", String(on));
    $("#mapFab").lastChild.textContent = on && phoneLayout.matches ? " 返回行程" : " 地图";
    $("#planExpand").innerHTML = ic(on && !phoneLayout.matches ? "expand" : "collapse");
    requestAnimationFrame(() => { map.invalidateSize(); if (on && phoneLayout.matches) drawRoute(true); });
  }
  $("#mapCollapse").addEventListener("click", () => setMap(false));
  $("#mapFab").addEventListener("click", () => setMap(!phoneLayout.matches || !$("#layout").classList.contains("show-map")));
  phoneLayout.addEventListener("change", () => {
    setMap(!phoneLayout.matches);
    renderChips();
    drawRoute(false);
  });

  // ======================= 切换 =======================
  let openingRevision = null;

  function selectGroup(gi) {
    openingRevision = null;
    $("#archiveError").hidden = true;
    if (online.isActive()) online.leave();
    const url = new URL(location.href);
    const onFlow = ["new","job","request","preview"].some(key => url.searchParams.has(key));
    if (onFlow) {
      ["new","job","request","preview","trip","candidate","day","revision"].forEach(key => url.searchParams.delete(key));
      history.replaceState(null, "", url);
    }
    state.revision = null;
    state.g = gi; state.v = gi >= 0 && !group().archive ? group().versions.length - 1 : 0;
    resetView();
  }
  function selectVersion(v, sec = group()?.archive ? state.sec : "overview") {
    openingRevision = null;
    state.v = v;
    state.revision = null;
    resetView(sec);
  }
  function updateTripUrl() {
    if (comparisonStudy) return;
    const owner = group(), t = trip();
    const u = new URL(location.href);
    u.searchParams.set("trip", owner.id);
    if (t.archive) {
      u.searchParams.set("candidate", baseTrip().id);
      const revision = state.revision?.revision.id || openingRevision?.id;
      if (revision) u.searchParams.set("revision", revision);
      else u.searchParams.delete("revision");
      if (activeDay()) u.searchParams.set("day", activeDay());
      else u.searchParams.delete("day");
    } else if (u.searchParams.has("candidate")) {
      u.searchParams.delete("candidate");
      u.searchParams.delete("day");
    }
    history.replaceState(null, "", u);
    if (owner.jobId && t.archive && (owner.canEdit || owner.preview) && !openingRevision) {
      const saved = LS.get("planningJobs", []);
      let current = saved.find(run => run.job === owner.jobId);
      if (!current) {
        current = {job:owner.jobId, title:owner.title, request:t.archive.requestId,
          startDate:t.startDate || undefined, people:t.people || undefined, provider:t.archive.modelProvider || undefined};
        saved.unshift(current);
      }
      Object.assign(current, {candidate:baseTrip().id, revision:state.revision?.revision.id, day:activeDay(),
        preview:owner.preview?.id});
      LS.set("planningJobs", saved.slice(0, 12));
    }
  }
  function resetView(sec = "overview") {
    state.sec = sec; state.hl = null; state.sel.clear(); state.openLegs.clear();
    closePop();
    if (!trip()) state.tab = "detail";
    $$("#mainTabs button").forEach((x) => x.classList.toggle("on", x.dataset.tab === state.tab));
    if (trip() && !acceptedEdits.has(trip())) acceptedEdits.set(trip(), clone(trip()));
    renderTop(); renderChips(); renderPlan(); renderChat(); drawRoute(true); drawRecs();
    if (sec === "overview") $("#planBody").scrollTop = 0;
    else gotoSec(sec, {restore:true});
    if (trip()) updateTripUrl();
  }

  const online = createPersonalizedFlow($("#onlineIntake"), $("#onlineStatus"), {
    showPlaces: showPlanningPlaces,
    focusPlace: focusPlanningPlace,
    clearPlan() {
      state.g = -1;
      state.revision = null;
      resetView();
    },
    remember(job, spec) {
      const core = ["origin_city", "destination_city", "days"].map(key =>
        spec.items.find(item => item.field === key)?.value);
      const title = core[0] && core[1] ? `${core[0]} → ${core[1]}${core[2] ? " " + core[2] + "日游" : ""}`
        : spec.raw_text?.slice(0, 30) || "旅行需求";
      const value = key => spec.items.find(item => item.field === key)?.value;
      LS.set("planningJobs", [{job, request:spec.request_id, title,
        startDate:value("start_date"), people:value("people_number"), provider:spec.model_provider},
        ...LS.get("planningJobs", []).filter(run => run.job !== job)].slice(0, 12));
    },
    publish(next) {
      if (comparisonStudy) {
        const {pair, scenario, token} = comparisonStudy;
        const versions = [pair.reference,pair.alternative].map(id => next.versions.find(version => version.id === id));
        if (next.jobId !== pair.job || versions.some(version => !version?.archive)) return;
        const comparisonReady = $("#archiveDelta");
        if (comparisonReady && !$("#modal").hidden) return;
        $("#layout").inert = true;
        document.body.classList.add("comparison-task");
        compareArchived(versions, 0, comparisonStudy);
        requestAnimationFrame(() => requestAnimationFrame(() => {
          window.parent.postMessage({type:"shorttrip-comparison-ready", token, scenario}, location.origin);
        }));
        return;
      }
      const previousRevision = state.revision;
      const previous = group()?.id === next.id ? trip() : null;
      const focus = previous?.id && state.hl;
      const url = new URL(location.href);
      const wanted = (previous && baseTrip()?.id) || url.searchParams.get("candidate");
      const reopen = next.canEdit ? url.searchParams.get("revision") : null;
      const day = previous ? activeDay() : Number(url.searchParams.get("day"));
      let index = state.groups.findIndex(group => group.id === next.id);
      if (index < 0) { index = state.groups.length; state.groups.push(next); }
      else state.groups[index] = next;
      state.g = index;
      const requestedVersion = next.versions.findIndex(v => v.id === wanted);
      state.v = Math.max(0, requestedVersion);
      state.revision = previous && next.canEdit ? previousRevision : null;
      const restoration = reopen && !state.revision && (!wanted || requestedVersion >= 0) ? {id:reopen} : null;
      openingRevision = restoration;
      state.tab = "detail";
      resetView(trip().days.some(d => d.day === day) ? `day-${day}` : "overview");
      if (focus && trip().id === previous.id) focusItem(focus, true);
      const warn = message => {
        $("#archiveError").textContent = message;
        $("#archiveError").hidden = false;
      };
      if (next.canEdit && wanted && requestedVersion < 0) {
        warn("旧链接指向的候选已找不到，当前显示此任务的可用方案。");
        return;
      }
      if (restoration) {
        API.itineraryEdit("state", null, {id:reopen}).then(saved => {
          if (openingRevision !== restoration) return;
          openingRevision = null;
          if (!saved.group || !showEditedVersion(saved.group)) {
            updateTripUrl();
            warn("旧链接指向的修改版本无法打开，当前显示原方案。");
          }
        }).catch(error => {
          if (openingRevision !== restoration) return;
          openingRevision = null;
          updateTripUrl();
          warn(error.message + " 当前显示原方案。");
        });
      }
    },
  });

  function showEditedVersion(raw, change) {
    const item = raw.versions[0];
    if (raw.jobId !== group()?.jobId || item?.revision?.original !== baseTrip()?.id || !item.revision.adopted) return false;
    openingRevision = null;
    state.revision = API.fromChinaTravel(item);
    resetView(["insert_visit", "batch"].includes(change?.action) ? `day-${change.day}` : state.sec);
    if (change?.action === "batch") {
      const day = trip().days.find(entry => entry.day === change.day);
      const modified = change.changes.find(entry => entry.slot && entry.before.name !== entry.after.name)
        || change.changes.find(entry => entry.slot);
      const target = modified && day?.items[modified.slot - 1];
      if (target) focusItem(target.uid, true, true);
    }
    return true;
  }
  const editor = createItineraryEditor({
    open: openModal, close: closeModal,
    streetTiles: amapStreetTiles,
    restaurantCatalog: () => API.getPois({city:trip().city, kind:"restaurant", snapshot:trip().archive.snapshot}),
    active: panel => !$("#modal").hidden && $("#modalBox").contains(panel),
    current: () => group()?.canEdit ? {job:group().jobId, candidate:trip().id,
      original:baseTrip().id, parent:trip().revision?.id || null} : null,
    adoptVersion: showEditedVersion,
    original: () => selectVersion(state.v),
    lookup: uid => locate(uid)?.item,
  });
  const renderTransferReview = createTransferReview({panel:$("#transferReview"), clock, reviewDate, setReviewDate,
    dateChanged: () => {renderTop(); renderPlan();}});
  const {render:renderTimelineWeather, selectDay:selectWeatherDay} =
    createTimelineWeather($("#planBody"), $("#mapPane"), $("#mapWeather"), queueMapLabels);

  function startPersonalized() {
    selectGroup(-1);
    showDrawer(false);
    if (phoneLayout.matches) setMap(false);
    void online.open();
    resetView();
  }

  // ======================= 启动 =======================
  (async function boot() {
    fillIcons();
    $("#sampleMode").hidden = API.mode !== "mock";
    renderFilters();
    if (window.AtlasView || innerWidth <= 980) setAi(false);
    if (phoneLayout.matches) setMap(false);
    try { state.favs = (await API.getFavorites()) || []; } catch { state.favs = []; }
    try { state.groups = await API.listTrips(); } catch (e) { toast("加载行程失败：" + e.message, 4000); state.groups = []; }
    const want = params.get("trip");
    let archived;
    const wantArchive = want === "personalized" || want?.startsWith("personalized-");
    const resumeTask = API.hasPersonalized && (params.has("job") || params.has("request"));
    if (API.hasArchive || wantArchive) {
      try {
        const archives = await API.getArchivedTrips();
        archived = archives.find(group => group.id === want) || archives[0];
        state.groups.push(...archives);
      } catch {
        if (wantArchive && !resumeTask) {
          $("#archiveError").hidden = false;
          $("#archiveError").textContent = "归档暂时无法打开，请刷新后重试。";
          return;
        }
        if (!resumeTask) toast("暂时无法读取规划归档", 4000);
      }
    }
    $("#startTrip").hidden = !API.hasPersonalized;
    if (online.isActive()) return;
    if (API.hasPersonalized && (params.has("job") || params.has("request") || params.has("new"))) {
      state.g = -1;
      if (params.has("job") || params.has("request")) {
        void online.resume(params.get("job"), params.get("request"), params.get("preview"));
      } else void online.open();
      resetView();
      return;
    }
    if (archived && (want === "personalized" || want === archived.id)) {
      state.g = state.groups.indexOf(archived);
      state.v = Math.max(0, archived.versions.findIndex(v => v.id === params.get("candidate")));
      const day = Number(params.get("day"));
      resetView(trip().days.some(d => d.day === day) ? `day-${day}` : "overview");
      return;
    }
    let gi = state.groups.findIndex((g) => g.id === want);
    if (gi < 0) gi = state.groups.length ? 0 : -1;
    selectGroup(gi);
    const d = +params.get("day"); if (d && trip()) setTimeout(() => gotoSec(`day-${d}`), 50);
  })();
})();
