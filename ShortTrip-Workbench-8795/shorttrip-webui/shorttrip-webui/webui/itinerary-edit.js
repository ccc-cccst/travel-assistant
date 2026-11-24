(function () {
  "use strict";
  const esc = value => String(value ?? "").replace(/[&<>"']/g,
    char => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"})[char]);
  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
  const labels = {time:"调整活动时刻", replace:"更换地点", transport:"更换交通", hotel:"更换酒店", optimize:"顺路优化", advance_transfer:"提前出发"};
  const meals = {breakfast:"早餐", lunch:"午餐", dinner:"晚餐"};
  labels.insert_visit = "加一个景点";
  labels.remove_visit = "移除这一站";
  labels.batch = "组合修改";
  const statusText = 'role="status"';

  window.createItineraryEditor = function ({open, close, active, current, adoptVersion, original, lookup, streetTiles, restaurantCatalog}) {
    const post = (path, body) => API.itineraryEdit(path, body);
    const get = (path, query) => API.itineraryEdit(path, null, query);
    const prewarm = base => {
      if (base) void post("prewarm", base).catch(() => {});
    };
    const matches = base => {
      const now = current();
      return Boolean(base && now && now.job === base.job && now.candidate === base.candidate && now.parent === base.parent);
    };
    const show = (body, buttons = '<button data-close>关闭</button>') => {
      open(`<div id="editView">${body}<div class="btns">${buttons}</div></div>`);
      return document.querySelector("#editView");
    };
    const message = (title, text) => show(`<h3>${esc(title)}</h3><p ${statusText}>${esc(text)}</p>`);
    const button = (id, label) => `<button id="${id}" class="pri" type="button">${label}</button>`;

    function summary(change) {
      if (change.action === "batch") {
        const activityChanges = change.changes.filter(({before, after}) =>
          !after || before.name !== after.name || before.time.join() !== after.time.join());
        const transferChanges = change.changes.filter(entry => !activityChanges.includes(entry));
        const rows = activityChanges.map(({before, after}) => {
          const replaced = after && before.name !== after.name;
          const title = !after ? "移除" : replaced ? "更换地点" : "调整时刻";
          const name = replaced ? before.name + " → " + after.name : before.name;
          const when = after && after.time.join() !== before.time.join()
            ? before.time.join("–") + " → " + after.time.join("–") : before.time.join("–");
          return `<li><strong>${title}</strong><span>${esc(name)}</span><small>${esc(when)}</small></li>`;
        });
        const locked = change.locks || transferChanges.filter(({before}) => before.locked).map(({before, after}) =>
          ({name:before.name,start:before.time[0],arrival:after.arrival}));
        const fixed = locked.map(({name,start,arrival}) => `${name} ${start} 保留${arrival ? "，预计 " + arrival + " 抵达" : ""}`);
        const paceLabel = {morning:"上午", afternoon:"下午", evening:"晚上"}[change.pace?.period] || "当天";
        return `<h4>第 ${change.day} 天的修改</h4>
          ${change.pace ? `<p class="edit-lock-note">${paceLabel}的游览和接驳合计少
            ${change.pace.before - change.pace.after} 分钟。</p>` : ""}
          ${rows.length ? `<ol class="edit-changes">${rows.join("")}</ol>` : ""}
          ${fixed.length ? `<p class="edit-lock-note">${esc(fixed.join("。"))}</p>` : ""}
          ${transferChanges.length ? `<details class="compare-metrics"><summary>查看 ${transferChanges.length} 处接驳变化</summary>
            <ul class="check-list">${transferChanges.map(({before, after}) =>
              `<li>${esc(after.name)} · ${before.route_minutes} → ${after.route_minutes} 分钟
              ${before.arrival && after.arrival && before.arrival !== after.arrival
                ? ` · 预计抵达 ${esc(before.arrival)} → ${esc(after.arrival)}` : ""}</li>`).join("")}</ul></details>` : ""}`;
      }
      if (["insert_visit","remove_visit"].includes(change.action)) {
        const anchor = change.anchor;
        return `<p>第 ${change.day} 天 · ${change.action === "insert_visit" ? "新增" : "移除"} ${esc(change.name)} ${esc(change.time.join("–"))}</p>
          <p>${anchor ? esc(anchor.label) + "最晚 " + esc(anchor.deadline) + " 抵达，快照预计 " + esc(anchor.arrival)
            + " 到达，" + (anchor.before_minutes == null ? "余量 " : "余量由 " + anchor.before_minutes + " 变为 ")
            + anchor.after_minutes + " 分钟。" : "当天后续没有取得可量化的预约或返程余量。"}其他活动保留原时刻。</p>`;
      }
      if (change.old_hotel) return `<p>${esc(change.old_hotel)} → ${esc(change.new_hotel)}
        ${change.last_day ? `（${change.last_day - change.day + 1} 晚）` : ""}</p>`;
      if (change.action === "optimize") return `<p>第 ${change.day} 天 · 顺路优化</p>
        <p>原顺序 · ${esc(change.original_order.join(" → "))}</p><p>新顺序 · ${esc(change.order.join(" → "))}</p>`;
      if (change.action === "advance_transfer") return `<p>第 ${change.day} 天 · ${esc(change.from)} → ${esc(change.to)}</p>
        <p>出发 ${esc(change.original_departure)} → ${esc(change.departure)}。本地预计 ${esc(change.local_arrival)} 抵达，计划最晚 ${esc(change.deadline)} 抵达。</p>`;
      if (change.action === "transport") return `<p>第 ${change.day} 天的接驳已重新计算。</p>`;
      return `<p>第 ${change.day} 天 · ${esc(change.original_name)} ${esc(change.original_time.join("–"))}
        → ${esc(change.name)} ${esc(change.time.join("–"))}</p>`;
    }
    function differences({before, after}) {
      const rows = PlanDifferences.metricRows(before, after);
      return `<table class="version-table"><thead><tr><th>指标</th><th>修改前</th><th>修改后</th><th>变化</th></tr></thead><tbody>
        ${rows.map(({label, before:x, after:y, change:delta, format:fmt}) => {
          const value = n => Number.isFinite(n) ? fmt(n) : "待核实";
          return `<tr><th scope="row">${label}</th><td>${value(x)}</td><td>${value(y)}</td><td>${delta == null ? "—" : delta ? (delta > 0 ? "+" : "−") + fmt(Math.abs(delta)) : "持平"}</td></tr>`;
        }).join("")}
        </tbody></table>`;
    }
    function mealDetail(change) {
      if (!change.connections) return "";
      const legs = change.connections.map(leg => {
        const oldRoute = (leg.from_before || leg.from) + " → " + (leg.to_before || leg.to);
        return `<li>${esc(oldRoute)}（${leg.before} 分钟）<br>${esc(leg.from)} → ${esc(leg.to)}（${leg.after} 分钟）</li>`;
      });
      const anchor = change.anchor;
      return `<h4>用餐前后接驳</h4><ul class="check-list">${legs.join("")}</ul>
        ${anchor ? `<p>预计 ${esc(anchor.arrival)} 到达，${esc(anchor.label)}要求 ${esc(anchor.deadline)} 前到达。
        预留时间 ${anchor.before_minutes} → ${anchor.after_minutes} 分钟。</p>` : ""}`;
    }
    async function settle(base, operation, stillHere, onStatus) {
      const {id} = await post("preview", {...base, ...operation});
      let polls = 0;
      while (stillHere()) {
        const result = await get("state", {id});
        if (!stillHere()) return null;
        if (!["starting","planning"].includes(result.state)) return result;
        if (result.message) onStatus?.(result.message);
        await delay(polls++ < 2 ? 200 : 650);
      }
      return null;
    }
    async function trial(base, operation) {
      if (!matches(base)) return message("请重新试排", "当前行程版本已切换。");
      const pending = message(labels[operation.action], "正在重新计算接驳、时刻和费用…");
      const stillHere = () => active(pending) && matches(base);
      try {
        const result = await settle(base, operation, stillHere, text => {
          const status = pending.querySelector("[role=status]");
          if (status.textContent !== text) status.textContent = text;
        });
        if (!result) return;
        if (result.state !== "complete") {
          message(labels[operation.action], result.message || "这项调整无法生成可用行程。");
          return;
        }
        const change = result.change;
        const table = differences(change);
        const metrics = PlanDifferences.metricRows(change.before, change.after).slice(0, 2);
        const overview = metrics.map(({short, change:delta, format}) =>
          delta == null ? short + "待核实" : delta === 0 ? short + "持平" :
          short + (delta > 0 ? "多 " : "少 ") + format(Math.abs(delta))).join(" · ");
        const panel = show(`<h3>修改预览已通过核验</h3>${change.action === "batch" ? `<p class="edit-key-delta">${esc(overview)}</p>` : ""}
          ${summary(change)}${mealDetail(change)}
          ${change.action === "batch" ? `<details class="compare-metrics"><summary>查看费用和时间明细</summary>${table}</details>` : table}
          <p class="compare-note">预约、返程和原需求经过复核。采用后将保存为一个独立版本。</p>
          <p ${statusText} class="edit-error"></p>`, '<button data-close>返回原方案</button>' + button("adoptEdit","采用并保存"));
        const apply = panel.querySelector("#adoptEdit");
        apply.onclick = async () => {
          if (apply.disabled) return;
          if (!matches(base)) return message("请重新试排", "当前行程版本已切换。");
          apply.disabled = true;
          try {
            const adopted = await post("adopt", {id:result.id});
            if (active(panel)) { close(); adoptVersion(adopted.group, result.change); }
          } catch (error) {
            if (active(panel)) panel.querySelector(".edit-error").textContent = error.message;
            apply.disabled = false;
          }
        };
      } catch (error) {
        if (stillHere()) message(labels[operation.action], error.message);
      }
    }

    function reviewProposal(base, proposal) {
      if (!matches(base)) return message("请重新发送请求", "当前行程版本已切换。");
      const panel = show(`<h3>确认第 ${proposal.day} 天的调整</h3>
        <fieldset class="proposal-actions"><legend>要一起试排的修改</legend>
        ${proposal.checklist.map((line, i) => `<label><input type="checkbox" value="${i}" checked><span>${esc(line)}</span></label>`).join("")}
        </fieldset>
        ${proposal.locks.length ? `<details class="compare-metrics" open><summary>保留的预约与返程</summary>
          <ul class="check-list">${proposal.locks.map(lock => `<li>第 ${lock.day} 天 · ${esc(lock.start)} ${esc(lock.name)}</li>`).join("")}</ul>
          </details>` : ""}`, '<button data-close>取消</button>' + button("batchTrial", "一起试排"));
      const controls = [...panel.querySelectorAll("input[type=checkbox]")];
      const submit = panel.querySelector("#batchTrial");
      panel.onchange = () => {
        submit.disabled = !controls.slice(0, proposal.operations.length).some(control => control.checked);
      };
      submit.onclick = () => {
        const operations = proposal.operations.filter((_, i) => controls[i].checked);
        const pace = proposal.pace && controls[proposal.operations.length]?.checked ? proposal.pace : null;
        if (operations.length === 1 && !pace) {
          const operation = operations[0];
          if (operation.action === "meal_options") {
            const item = lookup(operation.activity);
            if (item) replace(item, operation.cuisine);
          } else {
            void trial(base, operation);
          }
          return;
        }
        void trial(base, {action:"batch",schema_version:1, day:proposal.day, operations, pace,
          pace_period:pace ? proposal.pace_period : null});
      };
    }

    function editTime(item) {
      const base = current();
      prewarm(base);
      const activity = item.kind === "restaurant" ? "用餐" : "游览";
      const panel = show(`<h3>调整「${esc(item.name)}」</h3><div class="time-fields">
        <label>${activity}开始<input name="start" type="time" value="${esc(item.start)}"></label>
        <label>${activity}结束<input name="end" type="time" value="${esc(item.end)}"></label>
        </div><p ${statusText} class="edit-error"></p>`, '<button data-close>取消</button>' + button("checkTimeEdit","试排并核验"));
      panel.querySelector("#checkTimeEdit").onclick = () => {
        const start = panel.querySelector('[name="start"]').value, end = panel.querySelector('[name="end"]').value;
        if (!start || !end || start >= end) {
          panel.querySelector(".edit-error").textContent = "请选择有效的活动起止时刻。";
          return;
        }
        void trial(base, {action:"time", activity:item.uid, start, end});
      };
    }
    function insertVisit(name, days, day, loadPlaces) {
      const base = current();
      prewarm(base);
      if (!base) return;
      const title = loadPlaces ? "第 " + day + " 天 · 添加地点" : "试加「" + name + "」";
      const picker = loadPlaces ? `<div class="visit-place-picker">
        <label>搜索景点或商业街 <input name="visit_query" type="search" maxlength="120" placeholder="输入地点名称"></label>
        <label>选择地点 <select name="visit_place" disabled></select></label>
        <p class="visit-search-status" role="status"></p>
        <button class="visit-search-retry" type="button" hidden>重新读取地点</button>
        </div>` : "";
      const panel = show(`<h3>${esc(title)}</h3>${picker}<div class="time-fields">
        <label>安排日期<select name="day">${days.map(d => `<option value="${d.day}" ${d.day === day ? "selected" : ""}>第 ${d.day} 天</option>`).join("")}</select></label>
        <label>游览时段<select name="period"><option value="morning">上午 08–12 时</option><option value="afternoon" selected>下午 12–18 时</option><option value="evening">晚上 18–22 时</option></select></label>
        <label>停留分钟<input type="number" name="duration" min="15" max="240" step="1" value="60"></label></div>
        <p>系统会在所选时段找可用空档，核对门票、开放时间和前后接驳。</p><p class="edit-error" role="status"></p>`,
        '<button data-close>取消</button>' + button("checkMapVisit","试排并核验"));
      const check = panel.querySelector("#checkMapVisit");
      const select = panel.querySelector('[name="visit_place"]');
      check.onclick = () => {
        const duration = panel.querySelector('[name="duration"]').valueAsNumber;
        if (!Number.isInteger(duration) || duration < 15 || duration > 240) {
          panel.querySelector(".edit-error").textContent = "请填写 15–240 的整数分钟。";
          return;
        }
        const place = select ? select.value : name;
        if (!place) return;
        void trial(base, {action:"insert_visit", place, duration, day:Number(panel.querySelector('[name="day"]').value),
          period:panel.querySelector('[name="period"]').value});
      };
      if (loadPlaces) connectVisitPicker(panel, check, select, loadPlaces, base);
    }

    function connectVisitPicker(panel, check, select, loadPlaces, base) {
      const search = panel.querySelector('[name="visit_query"]');
      const status = panel.querySelector(".visit-search-status");
      const retry = panel.querySelector(".visit-search-retry");
      let places = [];
      const selection = () => { check.disabled = !select.value; };
      function filter() {
        const query = search.value.trim().toLocaleLowerCase();
        const previous = select.value;
        const matches = places.filter(place => place.name.toLocaleLowerCase().includes(query));
        const visible = matches.slice(0, 20);
        select.replaceChildren(new Option("请选择地点", ""), ...visible.map(place => new Option(place.name,place.name)));
        if (visible.some(place => place.name === previous)) select.value = previous;
        select.disabled = !visible.length;
        status.textContent = visible.length ? (query && matches.length > visible.length ? "可继续缩小搜索范围。" : "") :
          query ? "没有找到可添加的地点，试试其他关键词。" : "这份行程的地点目录暂无可添加的地点。";
        selection();
      }
      async function load() {
        check.disabled = true;
        search.disabled = select.disabled = true;
        retry.hidden = true;
        status.textContent = "正在读取行程的地点目录…";
        try {
          places = await loadPlaces();
          if (!active(panel) || !matches(base)) return;
          search.disabled = false;
          filter();
          search.focus();
        } catch {
          if (!active(panel)) return;
          status.textContent = "暂时无法读取地点目录。";
          retry.hidden = false;
        }
      }
      search.oninput = filter;
      select.onchange = selection;
      retry.onclick = load;
      void load();
    }

    function addDayPlace(trip, day) {
      const snapshot = trip.archive?.snapshot;
      const planned = new Set(trip.days.flatMap(d => d.items.map(item => item.place || item.name)));
      const anchor = trip.days.find(d => d.day === day)?.items.filter(item => item.pos).at(-1)?.pos;
      const loadPlaces = async () => {
        const catalog = await Promise.all(["attraction","shopping"].map(kind => API.getPois({city:trip.city, kind, snapshot})));
        const places = catalog.flat().filter(place => !planned.has(place.name));
        return anchor ? places.sort((a,b) => API.haversine(anchor,a.pos) - API.haversine(anchor,b.pos)) : places;
      };
      insertVisit(null, trip.days, day, loadPlaces);
    }

    function replace(item, cuisine = "") {
      const base = current();
      const place = {attraction:"景点", restaurant:"餐馆", hotel:"酒店"}[item.kind];
      if (!base || !place) return;
      prewarm(base);
      const unit = {attraction:"元/人", restaurant:"元/人", hotel:"元/间夜"}[item.kind];
      const isMeal = item.kind === "restaurant";
      const scope = item.kind === "hotel" ? "同一家酒店的连续住宿会一起更换。"
        : "保留原定时段，并核验营业时间和前后接驳。";
      const panel = show(`<section class="replace-picker">
        <h3>更换${place}</h3>
        <p class="replace-original">当前${place} <strong>${esc(item.name)}</strong></p>
        <div class="replace-grid">
          <div class="replace-map-panel">
            <div class="replace-map" aria-label="候选${place}地图"></div>
            <button class="replace-search-area" type="button" disabled>搜地图区域</button>
            <p class="replace-map-notice" role="status" hidden>高德底图加载失败，可从下方或右侧列表选点。</p>
          </div>
          <div class="replace-results">
            <form id="searchEditPlaces"><input name="q" type="search" maxlength="120" aria-label="搜索${place}" placeholder="搜索${place}名称"><button>搜索</button></form>
            ${isMeal ? '<button id="recommendMeals" type="button">按接驳推荐</button>' : ""}
            <p class="replace-status" role="status"></p>
            <ul class="replace-list" aria-label="候选${place}"></ul>
          </div>
        </div>
        <p class="compare-note replace-scope">${esc(scope)}${cuisine ? " 推荐菜系为" + esc(cuisine) + "。" : ""}</p>
        <p class="replace-selection" role="status">点地图或列表选择${place}。</p>
        </section>`, '<button data-close>取消</button>' + button("checkReplacement","试排并核验"));
      const status = panel.querySelector(".replace-status");
      const check = panel.querySelector("#checkReplacement");
      const mapElement = panel.querySelector(".replace-map");
      const notice = panel.querySelector(".replace-map-notice");
      const list = panel.querySelector(".replace-list");
      const selection = panel.querySelector(".replace-selection");
      const search = panel.querySelector('[name="q"]');
      const area = panel.querySelector(".replace-search-area");
      const map = L.map(mapElement, {scrollWheelZoom:false, maxZoom:18});
      const tiles = streetTiles({maxNativeZoom:17}).addTo(map);
      const candidateLayer = L.layerGroup().addTo(map);
      const validPosition = pos => Array.isArray(pos) && pos.length === 2 && pos.every(Number.isFinite);
      const anchor = validPosition(item.pos) ? item.pos : null;
      const markerIcon = (label, style = "") => L.divIcon({
        className:"replace-pin " + style, html:label, iconSize:[30,30],
      });
      if (anchor) {
        L.marker(anchor, {icon:markerIcon("原", "replace-pin-original"), title:"当前" + place})
          .bindTooltip(esc(item.name)).addTo(map);
        map.setView(anchor, 14, {animate:false});
        area.disabled = false;
      }
      tiles.on("load", () => {notice.hidden = Boolean(mapElement.querySelector(".leaflet-tile-loaded"));});
      const resizer = new ResizeObserver(() => map.invalidateSize({pan:false}));
      resizer.observe(mapElement);
      const observer = new MutationObserver(() => {
        if (active(panel)) return;
        observer.disconnect();
        resizer.disconnect();
        map.remove();
      });
      observer.observe(panel.parentElement, {childList:true});
      observer.observe(panel.parentElement.parentElement, {attributes:true,attributeFilter:["hidden"]});
      let sequence = 0;
      let selected = null;
      let choices = [];
      function select(choice, fromMap) {
        selected = choice.entry;
        for (const row of choices) {
          const active = row === choice;
          row.button.setAttribute("aria-pressed", String(active));
          row.marker?.getElement()?.classList.toggle("replace-pin-selected", active);
          row.marker?.setZIndexOffset(active ? 1000 : 0);
        }
        if (fromMap) {
          const row = choice.button.parentElement;
          if (row.offsetTop < list.scrollTop) list.scrollTop = row.offsetTop;
          else if (row.offsetTop + row.offsetHeight > list.scrollTop + list.clientHeight) {
            list.scrollTop = row.offsetTop + row.offsetHeight - list.clientHeight;
          }
        } else if (choice.marker) {
          map.panInside(choice.marker.getLatLng(), {padding:[36,36], animate:false});
        }
        selection.textContent = "已选 " + selected.name;
        check.disabled = false;
      }
      function showChoices(entries, keepView) {
        const positions = anchor ? [anchor] : [];
        choices = entries.map((entry, index) => {
          const distance = entry.km == null ? "" : "距原地点约 " + entry.km.toFixed(1) + " 公里（直线）";
          const price = entry.price != null && Number.isFinite(Number(entry.price)) ? "参考 " + Number(entry.price) + " " + unit : "";
          const transfer = entry.minutes == null ? "" : "前后接驳 " + entry.minutes + " 分钟";
          const meta = [transfer, distance, price].filter(Boolean).join(" · ");
          const row = document.createElement("li");
          row.innerHTML = `<button class="replace-choice" type="button" aria-pressed="false">
            <span class="replace-number">${index + 1}</span><span><strong>${esc(entry.name)}</strong>
            ${meta ? `<small>${esc(meta)}</small>` : ""}</span></button>`;
          const button = row.firstElementChild;
          const choice = {entry, button, marker:null};
          if (validPosition(entry.pos)) {
            positions.push(entry.pos);
            choice.marker = L.marker(entry.pos, {icon:markerIcon(index + 1), title:entry.name})
              .bindTooltip(esc(entry.name)).addTo(candidateLayer);
            choice.marker.on("click", () => select(choice, true));
          }
          button.onclick = () => select(choice, false);
          list.append(row);
          return choice;
        });
        if (positions.length && !keepView) map.fitBounds(positions, {padding:[38,38],maxZoom:16,animate:false});
        if (positions.length) area.disabled = false;
      }
      async function find(q = "", bounds) {
        const ticket = ++sequence;
        check.disabled = true;
        selected = null;
        selection.textContent = "点地图或列表选择" + place + "。";
        candidateLayer.clearLayers();
        list.replaceChildren();
        const recommend = isMeal && !bounds && !q.trim();
        status.textContent = recommend ? "正在计算用餐前后的接驳…" : "正在查找" + place + "…";
        const stillHere = () => ticket === sequence && active(panel) && matches(base);
        try {
          let entries;
          if (recommend) {
            const catalog = restaurantCatalog().catch(() => []);
            const result = await settle(base, {action:"meal_options", activity:item.uid,
              ...(cuisine ? {cuisine} : {})}, stillHere);
            if (!result) return;
            if (result.state !== "complete") throw new Error(result.message);
            const positions = new Map((await catalog).map(entry => [entry.name, entry.pos]));
            entries = result.meal_options.map(entry => ({...entry, pos:positions.get(entry.name)}));
          } else {
            entries = await post("places", {...base, activity:item.uid, q, bounds});
          }
          if (!stillHere()) return;
          showChoices(entries, Boolean(bounds));
          if (entries.length) status.textContent = entries.length + " 个候选";
          else if (bounds) status.textContent = "本区域没有匹配的" + place + "。可移动地图或修改搜索词。";
          else status.textContent = recommend ? "暂未找到按接驳推荐的餐馆。可按店名或地图区域搜索。" : "没有找到匹配的" + place + "。";
        } catch (failure) { if (stillHere()) status.textContent = failure.message; }
      }
      panel.querySelector("form").onsubmit = event => {
        event.preventDefault();
        void find(search.value);
      };
      area.onclick = () => {
        const bounds = map.getBounds();
        void find(search.value, [bounds.getSouth(), bounds.getWest(), bounds.getNorth(), bounds.getEast()]);
      };
      if (isMeal) panel.querySelector("#recommendMeals").onclick = () => {
        search.value = "";
        void find();
      };
      check.onclick = () => {
        if (selected) void trial(base, {action:"replace", activity:item.uid, place:selected.name});
      };
      void find();
    }
    function replaceMeal(name, days, day) {
      prewarm(current());
      const base = current();
      if (!base) return;
      const available = days.flatMap(d => d.items.filter(item => item.kind === "restaurant" && !item.lock)
        .map(item => ({item, day:d.day})));
      if (!available.length) return message("试换用餐", "当前方案没有可替换的用餐安排。");
      const selected = available.find(row => row.day === day && row.item.type === "lunch")
        || available.find(row => row.day === day) || available[0];
      const panel = show(`<h3>用「${esc(name)}」替换一餐</h3>
        <label>选择原用餐安排<select id="mealToReplace">${available.map(row =>
          `<option value="${esc(row.item.uid)}"${row === selected ? " selected" : ""}>第 ${row.day} 天 · ${meals[row.item.type] || "用餐"} ${esc(row.item.start)} · ${esc(row.item.name)}</option>`
        ).join("")}</select></label>`, '<button data-close>取消</button>' + button("checkMapMeal","试排并核验"));
      panel.querySelector("#checkMapMeal").onclick = () => void trial(base, {action:"replace",
        activity:panel.querySelector("#mealToReplace").value, place:name});
    }
    async function history() {
      const base = current();
      const panel = message("行程版本", "正在读取已保存版本…");
      try {
        const versions = await get("history", {job:base.job, original:base.original});
        if (!active(panel)) return;
        const groups = versions.map(version => {
          const title = version.change.old_hotel ? "酒店 · " + version.change.new_hotel :
            labels[version.change.action] + " · 第 " + version.change.day + " 天";
          return `<li><button type="button" data-saved-version="${esc(version.id)}">${esc(title)}</button></li>`;
        });
        const currentPanel = show(`<h3>行程版本</h3><ul class="edit-versions"><li><button id="editOriginal" type="button">打开原方案</button></li>${groups.join("")}</ul>
          <p ${statusText} class="edit-error"></p>`);
        currentPanel.querySelector("#editOriginal").onclick = () => { close(); original(); };
        currentPanel.onclick = async event => {
          const id = event.target.closest("[data-saved-version]")?.dataset.savedVersion;
          if (!id) return;
          try {
            const revision = await get("state", {id});
            if (active(currentPanel) && revision.group) { close(); adoptVersion(revision.group); }
          } catch (error) { if (active(currentPanel)) currentPanel.querySelector(".edit-error").textContent = error.message; }
        };
      } catch (error) { if (active(panel)) message("行程版本", error.message); }
    }
    return {editTime, replace, replaceMeal, insertVisit, addDayPlace, history, context:current, matches, trial, reviewProposal,
            removeVisit:item => trial(current(), {action:"remove_visit", activity:item.uid}),
            hotel:() => trial(current(), {action:"hotel"}),
            optimize:day => trial(current(), {action:"optimize", day}),
            transport:(item, mode) => trial(current(), {action:"transport", activity:item.uid, mode})};
  };
})();
