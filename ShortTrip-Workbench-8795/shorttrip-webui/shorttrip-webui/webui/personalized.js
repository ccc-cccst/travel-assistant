/* The main workbench owns itinerary selection; this module owns the request and job flow. */
(function () {
  "use strict";
  const esc = value => String(value ?? "").replace(/[&<>"']/g,
    char => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"})[char]);
  const stages = {
    world_facts: "正在查找班次、地点和营业信息",
    trip_sketch: "正在构想逐日游览安排",
    search: "正在试排交通、住宿和活动",
    assessment: "正在逐项核验候选",
    meal_refinement: "正在比较用餐安排",
    visit_refinement: "正在检查可补充的体验",
    extended_search: "正在比较更多备选",
    model_alternative: "正在核验模型提出的备选",
    recovery: "正在尝试可行的调整",
  };
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  const modelLabels = {deepseek:"云端 DeepSeek", qwen:"本地 Qwen 4B", program:"仅用表单"};
  const evidence = {form:"表单填写", user_confirmation:"用户已确认", assumption:"可调整的默认项", map_selection:"本次地图选择"};
  function valueText(item) {
    const v = item.value;
    if (item.field === "appointment") return `${v.name} · ${v.date || "第" + v.day + "天"} · ${v.start_time} · ${v.duration_minutes} 分钟`;
    const units = {"CNY/group":"元（整团）", "CNY/person":"元／人", "CNY/group/night":"元／团／晚",
      "CNY/person/night":"元／人／晚", "CNY/person/meal":"元／人／餐"};
    return (item.operator === "lte" ? "≤ " : "") + (Array.isArray(v) ? v.join("、") : String(v ?? "")) +
      (units[item.unit] ? " " + units[item.unit] : "");
  }

  const calendarDate = item => item.field === "start_date" && /^\d{4}-\d{2}-\d{2}$/.test(item.value);
  window.createPersonalizedFlow = function (intake, banner, {publish, remember, clearPlan, showPlaces, focusPlace}) {
    let spec, job, submission, controller, active = false;
    let shown = "", lastPreview = null;
    let referenceCities = new Set();
    let resumePreview;
    let picked, placeRows = [], placeQuery = "", placeRequest, choicePending = false, choicesOpen = false;

    function begin() {
      controller?.abort();
      controller = new AbortController();
      active = true;
      return controller.signal;
    }
    function url(fields) {
      const link = new URL(location.href);
      Object.entries(fields).forEach(([key, value]) => value
        ? link.searchParams.set(key, value) : link.searchParams.delete(key));
      history.replaceState(null, "", link);
    }
    function say(message, actions = "", detail = "") {
      const next = `<div role="status">${esc(message)}</div>${actions}${detail}`;
      if (banner.innerHTML !== next) banner.innerHTML = next;
      banner.hidden = false;
    }
    function unresolved(status) {
      const items = status.requirements_to_review || [];
      return items.length ? `<ul class="online-unresolved">${items.map(item => `<li>
        ${esc(item.label)}${item.value != null ? " · " + esc(valueText({value:item.value})) : ""}
        ${item.source ? `<q>${esc(item.source)}</q>` : ""}</li>`).join("")}</ul>` : "";
    }
    function get(name, query, signal = controller.signal) {
      return API.personalized(name, null, query, signal);
    }
    function post(name, payload, signal = controller.signal) {
      return API.personalized(name, payload, null, signal);
    }
    function error(problem, retry = "", holder = banner) {
      if (problem.name === "AbortError") return;
      if (holder === banner) say(problem.message, retry);
      else holder.textContent = problem.message;
    }
    const button = (name, label) => `<button type="button" data-online="${name}">${label}</button>`;
    const input = (name, label, type, value, extra = "") => `<label>${label}<input name="${name}" type="${type}" value="${esc(value)}" ${extra}></label>`;
    const options = (values, selected, placeholder) => `<option value="">${placeholder}</option>` +
      values.map(value => `<option ${value === selected ? "selected" : ""}>${esc(value)}</option>`).join("");
    const referenceNote = "厦门和西安按旅行快照试排。历史班次、票价和住宿估价请在出发前核对。";

    async function renderForm(seed = {}) {
      showPlaces(null);
      const signal = controller.signal;
      intake.hidden = false;
      intake.innerHTML = `<p role="status">正在读取可规划城市…</p>`;
      banner.hidden = true;
      let config;
      try { config = await get("options", seed.form?.target_city ? {city:seed.form.target_city} : null, signal); }
      catch (failure) {
        if (signal.aborted) return;
        intake.innerHTML = `<h2>新建旅行规划</h2><p>${esc(failure.message)}</p>${button("edit","重新连接")}`;
        return;
      }
      if (signal.aborted) return;
      referenceCities = new Set(config.reference_cities || []);
      const f = seed.form || {}, profile = f.profile || {}, appointment = f.appointments?.[0] || {};
      const providers = config.model_providers || {deepseek:config.model_ready, qwen:false, program:true};
      const selected = providers[seed.model_provider] ? seed.model_provider :
        Object.keys(modelLabels).find(name => providers[name]);
      intake.innerHTML = `<h2>新建旅行规划</h2><form id="planningRequest">
        <label>规划方式<select name="model_provider">${Object.entries(modelLabels).map(([name,label]) =>
          `<option value="${name}" ${name === selected ? "selected" : ""} ${providers[name] ? "" : "disabled"}>${label}${providers[name] ? "" : "（未就绪）"}</option>`).join("")}</select></label>
        <label>你想怎样旅行？<textarea name="raw_text" rows="4" placeholder="例如：想从南京坐高铁去上海玩两天，喜欢博物馆，行程不要太赶。">${esc(seed.raw_text)}</textarea></label>
        <div class="planning-fields">
          <label>出发城市<select name="start_city">${options(config.cities, f.start_city, "选择出发地")}</select></label>
          <label>目的城市<select name="target_city">${options(config.cities, f.target_city, "选择目的地")}</select></label>
          ${input("days","玩几天","number",f.days,'min="1" step="1"')}
          ${input("people_number","几个人","number",f.people_number,'min="1" step="1"')}
          ${input("start_date","出发日期（必填）","date",f.start_date,'required')}
        </div>
        <details ${Object.keys(f).length > 4 ? "open" : ""}><summary>预算、偏好和预约</summary>
          <div class="planning-fields">
            ${input("budget","整团预算上限（元）","number",f.budget,'min="1" step="any"')}
            <label>往返交通<select name="mode">${options(["高铁","火车","飞机"],f.intercity_modes?.[0],"由系统选择")}</select></label>
            <label>行程节奏<select name="pace">${options(["舒缓","均衡","充实"],profile.pace,"无特定偏好")}</select></label>
            <label>景点兴趣<select name="interest" disabled></select></label>
            <label>喜欢的菜系<select name="cuisine" disabled></select></label>
            <label>住宿偏好<select name="hotel_feature" disabled></select></label>
            ${input("appointment_name","预约地点","text",appointment.name)}
            ${input("appointment_day","行程第几天","number",appointment.day,'min="1"')}
            ${input("appointment_start","预约开始","time",appointment.start_time)}
            ${input("appointment_duration","预约时长（分钟）","number",appointment.duration_minutes,'min="1"')}
          </div>
        </details>
        <p data-reference-cities hidden>${referenceNote}</p>
        <p class="planning-error" role="status"></p><button class="pri" type="submit">核对需求</button>
      </form>`;
      const fields = intake.querySelector("#planningRequest").elements;
      function syncPlanningMode() {
        const manual = fields.model_provider.value === "program";
        fields.raw_text.closest("label").hidden = manual;
        fields.raw_text.disabled = manual;
      }
      fields.model_provider.onchange = syncPlanningMode;
      syncPlanningMode();
      function fillCatalog(catalog, previous = {}) {
        const fromCounts = values => Object.keys(values || {}).map(label => ({id:label, label}));
        for (const [name, choices, selected] of [
          ["interest", catalog?.interests?.filter(item => item.count), previous.interests?.[0]],
          ["cuisine", fromCounts(catalog?.cuisines), previous.cuisines?.[0]],
          ["hotel_feature", fromCounts(catalog?.hotel_features), previous.hotel_features?.[0]],
        ]) {
          fields[name].replaceChildren(new Option(choices?.length ? "无特定偏好" : "请先选择目的地", ""),
            ...(choices || []).map(item => new Option(item.label, item.id, false, item.id === selected)));
          fields[name].disabled = !choices?.length;
        }
      }
      function syncReferenceNote() {
        intake.querySelector("[data-reference-cities]").hidden =
          ![fields.start_city.value, fields.target_city.value].some(city => referenceCities.has(city));
      }
      fields.start_city.onchange = syncReferenceNote;
      syncReferenceNote();
      fillCatalog(f.target_city ? config.options : null, profile);
      fields.target_city.onchange = async () => {
        syncReferenceNote();
        const city = fields.target_city.value;
        fillCatalog(null);
        if (!city) return;
        try {
          const updated = await get("options", {city}, signal);
          if (!signal.aborted && fields.target_city.value === city) fillCatalog(updated.options);
        } catch (failure) {
          if (!signal.aborted) error(failure, "", intake.querySelector(".planning-error"));
        }
      };
    }

    function renderSpec(current) {
      spec = current;
      const city = spec.items.find(item => item.field === "destination_city")?.value;
      if (picked !== spec.request_id) {
        picked = spec.request_id;
        placeRows = [];
        placeQuery = "";
        choicesOpen = false;
      }
      intake.hidden = false;
      banner.hidden = true;
      const groups = [["fact","旅行信息"],["hard","必须满足"],["soft","希望满足"]];
      const fields = groups.map(([kind, title]) => {
        const rows = spec.items.filter(item => item.strength === kind).map(item => {
          const source = item.source?.text || evidence[item.source?.type] || "";
          return `<li><strong>${esc(item.label)}</strong> ${esc(valueText(item))}
            ${source ? `<small>${esc(source)}</small>` : ""}</li>`;
        });
        return rows.length ? `<h3>${title}</h3><ul>${rows.join("")}</ul>` : "";
      });
      const issues = spec.issues.map(issue => {
        const choices = (issue.choices || []).map((choice, i) =>
          `<label class="planning-choice"><input type="radio" name="${esc(issue.id)}" value="${i}"> ${esc(valueText(choice))}</label>`);
        const typed = issue.input_type === "date" ? input(esc(issue.id), "确认日期", "date", "") :
          issue.input_type === "number" ? input(esc(issue.id), "确认人数", "number", "", 'min="1"') : "";
        return `<fieldset><legend>${esc(issue.message)}</legend>${typed}${choices.join("")}</fieldset>`;
      });
      const canResolve = spec.issues.some(issue => issue.input_type || issue.choices?.length);
      const hasDate = spec.items.some(calendarDate);
      const canPreview = spec.issues.some(issue => issue.code === "conflicting_values" && issue.choices?.length > 1);
      const hasBudget = spec.items.some(item => item.field === "budget" && item.scope === "total_trip" && item.strength === "hard");
      const chosen = spec.items.filter(item => item.source?.type === "map_selection");
      const picker = city ? `<details id="planningPlacePicker"${choicesOpen ? " open" : ""}>
        <summary>选择这趟想去的景点</summary>
        <form id="planningPlaceSearch"><label>搜索${esc(city)}景点<input name="q" maxlength="80" value="${esc(placeQuery)}"></label>
        <button type="submit">搜索景点</button></form>
        <p class="place-status" role="status"></p>
        <div class="planning-place-results"></div></details>` : "";
      intake.innerHTML = `<h2>确认旅行需求</h2>${modelLabels[spec.model_provider] ? `<p>${modelLabels[spec.model_provider]}</p>` : ""}${spec.raw_text ? `<blockquote>${esc(spec.raw_text)}</blockquote>` : ""}
        <div class="planning-spec">${fields.join("")}</div>
        ${spec.items.some(item => ["origin_city", "destination_city"].includes(item.field) &&
            referenceCities.has(item.value)) ? `<p>${referenceNote}</p>` : ""}
        ${chosen.length ? `<ul class="planning-picked">${chosen.map(item => `<li><span>${esc(item.label)} · ${esc(item.value)}</span>
          <button type="button" data-place-clear="${esc(item.source.place_id)}" aria-label="撤销 ${esc(item.value)}">撤销</button></li>`).join("")}</ul>` : ""}
        ${picker}
        ${issues.length ? `<form id="planningResolve"><h3>待确认事项</h3>${issues.join("")}
          ${canResolve ? '<button type="submit">应用确认</button>' : ""}</form>` : ""}
        ${hasBudget && spec.ready ? '<label class="planning-choice"><input id="planningRelaxBudget" type="checkbox"> 原预算无可行解时，允许查看更高预算的预览</label>' : ""}
        ${!hasDate ? '<p>请确认具体出发日期后再规划。</p>' : ""}
        <p class="planning-error" role="status"></p><div class="planning-actions">${button("edit",hasDate ? "返回修改" : "返回填写日期")}
        ${hasDate && (spec.ready || canPreview) ? `<button class="pri" type="button" data-online="start">${spec.ready ? "按确认内容规划" : "查看可行调整"}</button>` : ""}</div>`;
      if (city) {
        const panel = intake.querySelector("#planningPlacePicker");
        panel.ontoggle = () => {
          if (!panel.isConnected) return;
          choicesOpen = panel.open;
          if (panel.open && !placeRows.length && !placeRequest) void searchPlaces(placeQuery);
          else renderPlaces(city);
        };
        renderPlaces(city);
      }
    }
    function renderPlaces(city) {
      const results = intake.querySelector(".planning-place-results");
      if (!results) return;
      results.innerHTML = placeRows.map((place, i) => `<button type="button" data-place-focus="${i}">${esc(place.name)} · 看地图</button>`).join("");
      const choices = Object.fromEntries(spec.items.filter(i => i.source?.type === "map_selection")
        .map(i => [i.source.place_id, i.source.choice]));
      showPlaces(choicesOpen && placeRows.length ? {city, places:placeRows, choices} : null);
    }
    async function searchPlaces(query) {
      const current = spec, city = current.items.find(item => item.field === "destination_city")?.value;
      const signal = controller.signal, token = {};
      placeRequest = token;
      placeQuery = query;
      const status = intake.querySelector(".place-status");
      if (status) status.textContent = "正在查找景点…";
      try {
        const result = await get("places", {city, q:query}, signal);
        if (signal.aborted || spec.request_id !== current.request_id || placeRequest !== token) return;
        placeRows = result.places;
        renderPlaces(city);
        const latest = intake.querySelector(".place-status");
        if (latest) latest.textContent = placeRows.length ? "点景点查看地图并选择本次安排。" : "没有找到可用于规划的景点。";
      } catch (failure) {
        if (!signal.aborted && placeRequest === token && status) status.textContent = failure.message;
      } finally {
        if (placeRequest === token) placeRequest = null;
      }
    }
    async function choosePlace(id, choice) {
      if (intake.hidden || !spec || choicePending) return false;
      const current = spec, signal = controller.signal;
      choicePending = true;
      const startButton = intake.querySelector('[data-online="start"]');
      if (startButton) startButton.disabled = true;
      try {
        const updated = await post("place-preference", {request_id:current.request_id,
          request_revision:current.request_revision, place_id:id, choice}, signal);
        if (signal.aborted || spec.request_id !== current.request_id) return false;
        submission = null;
        renderSpec(updated);
        return true;
      } catch (failure) {
        if (signal.aborted) return false;
        if (failure.status === 409) {
          try {
            const updated = await get("request", {id:current.request_id}, signal);
            if (!signal.aborted) renderSpec(updated);
          } catch { /* The original error is still shown. */ }
        }
        throw failure;
      } finally {
        choicePending = false;
        if (startButton) startButton.disabled = false;
      }
    }
    async function withForm(action) {
      const signal = controller.signal, box = intake.querySelector(".planning-error");
      const controls = [...intake.querySelectorAll("button")].filter(control => !control.disabled);
      controls.forEach(control => control.disabled = true);
      if (box) box.textContent = "正在核对需求…";
      try { await action(signal); }
      catch (failure) { if (!signal.aborted) error(failure, "", box || banner); }
      finally { controls.forEach(control => control.disabled = false); }
    }
    intake.addEventListener("submit", event => {
      event.preventDefault();
      const data = new FormData(event.target);
      if (event.target.id === "planningPlaceSearch") {
        void searchPlaces(data.get("q").trim());
        return;
      }
      if (event.target.id === "planningResolve") {
        const choices = Object.fromEntries([...data].filter(([, value]) => value !== "").map(([id, value]) =>
          [id, spec.issues.find(issue => issue.id === id)?.input_type === "date" ? value : Number(value)]));
        if (!Object.keys(choices).length) {
          intake.querySelector(".planning-error").textContent = "请选择或填写待确认的事项。";
          return;
        }
        void withForm(async signal => {
          let updated;
          try { updated = await post("resolve", {request_id:spec.request_id, request_revision:spec.request_revision, choices}, signal); }
          catch (failure) {
            if (failure.status !== 409) throw failure;
            updated = await get("request", {id:spec.request_id}, signal);
          }
          if (!signal.aborted) { submission = null; renderSpec(updated); }
        });
        return;
      }
      const form = {};
      for (const key of ["start_city","target_city","days","people_number","start_date","budget"]) {
        if (data.get(key)) form[key] = ["days","people_number","budget"].includes(key) ? Number(data.get(key)) : data.get(key);
      }
      if (data.get("mode")) form.intercity_modes = [data.get("mode")];
      const profile = Object.fromEntries([["interests","interest"],["cuisines","cuisine"],["hotel_features","hotel_feature"]]
        .filter(([, field]) => data.get(field)).map(([key, field]) => [key, [data.get(field)]]));
      if (data.get("pace")) profile.pace = data.get("pace");
      if (Object.keys(profile).length) form.profile = profile;
      const appointment = {name:data.get("appointment_name").trim(), day:Number(data.get("appointment_day")),
        start_time:data.get("appointment_start"), duration_minutes:Number(data.get("appointment_duration"))};
      if (Object.values(appointment).some(Boolean)) {
        if (!Object.values(appointment).every(Boolean)) {
          intake.querySelector(".planning-error").textContent = "请填全预约地点、行程日、开始时间和时长。";
          return;
        }
        form.appointments = [appointment];
      }
      const raw_text = (data.get("raw_text") || "").trim();
      if (!raw_text && !form.start_city && !form.target_city) {
        intake.querySelector(".planning-error").textContent = "请写一句旅行需求，或填写出发地和目的地。";
        return;
      }
      void withForm(async signal => {
        const current = await post("interpret", {raw_text, form, model_provider:data.get("model_provider")}, signal);
        if (signal.aborted) return;
        submission = null;
        url({new:1, request:current.request_id, job:null, preview:null});
        renderSpec(current);
      });
    });

    function display(raw, preview = null) {
      const signature = JSON.stringify([preview?.id, raw.versions.map(v => [v.id, v.archive.recommended])]);
      if (signature === shown) return;
      shown = signature;
      lastPreview = preview;
      intake.hidden = true;
      showPlaces(null);
      publish(API.verifiedGroup({...raw, preview, canEdit: Boolean(raw.recommendedId) && !preview}), job);
    }
    function reviewRecovery(status) {
      const previews = status.previews || [];
      if (resumePreview) {
        const selected = previews.find(preview => preview.id === resumePreview);
        if (selected) display(selected.group, selected);
        else status = {...status, message:"旧链接指向的调整预览已找不到。请从下方选择其他可用调整。"};
        resumePreview = null;
      }
      const message = status.message || status.recovery.cause?.message || "原需求暂时无法生成完整行程。";
      const label = preview => (preview.change.parts || [preview.change]).map(change =>
        `${change.label}调整为 ${valueText({value:change.after})}`).join("、");
      if (lastPreview) {
        say(`待确认预览 · ${label(lastPreview)}`,
          button("adopt","采用这项调整") + button("other","回到调整列表"));
      } else {
        const choices = previews.map(preview =>
          `<button type="button" data-online="preview" data-id="${esc(preview.id)}">${esc(label(preview))}</button>`).join("");
        const draft = status.recovery.draft;
        const detail = draft?.stops?.length ? `<div class="online-draft"><strong>${esc(draft.city)}当地游览建议</strong>
          <p>${esc(draft.condition)}</p><ul>${draft.stops.map(stop =>
            `<li>${esc(stop.name)} · 建议预留 ${esc(stop.stay_minutes)} 分钟</li>`).join("")}</ul>
          <p>仍待安排 · ${esc(draft.pending.join("、"))}</p></div>` : "";
        const actions = choices + button("edit","修改需求") + (previews.length ? "" : button("retry","重试规划"));
        say(message + (previews.length ? " 可查看调整后的候选。" : ""), actions, unresolved(status) + detail);
      }
      banner.onclick = async event => {
        const option = event.target.closest("[data-online]");
        if (!option) return;
        if (option.dataset.online === "other") {
          lastPreview = null; shown = "";
          url({preview:null, trip:null, candidate:null, day:null});
          clearPlan();
          reviewRecovery(status);
          return;
        }
        if (option.dataset.online === "preview") {
          const entry = previews.find(preview => preview.id === option.dataset.id);
          display(entry.group, entry);
          url({preview:entry.id});
          reviewRecovery(status);
        } else if (option.dataset.online === "adopt" && lastPreview) {
          const signal = controller.signal;
          option.disabled = true;
          try {
            spec = await post("adopt", {job_id:job, attempt_id:lastPreview.id}, signal);
            if (signal.aborted) return;
            lastPreview = null;
            url({preview:null, request:spec.request_id});
            await monitor(signal);
          } catch (failure) {
            if (!signal.aborted) error(failure, button("resume","刷新任务") + button("edit","修改需求"));
          } finally { option.disabled = false; }
        } else bannerAction(event);
      };
    }
    async function monitor(signal = controller.signal) {
      try {
        while (!signal.aborted) {
          const status = await get("job", {id:job}, signal);
          if (signal.aborted) return;
          if (status.group) display(status.group);
          const running = ["starting","planning"].includes(status.state);
          if (running) {
            say((status.group ? "首份已通过核验 · " : "") + (stages[status.stage] || "正在启动规划"));
          } else if (status.state === "complete" && status.group) {
            const change = status.recovered?.change;
            if (change) {
              const adopted = (change.parts || [change]).map(item =>
                `${item.label}采用 ${valueText({value:item.after})}`).join("、");
              say(`已按你确认的调整规划 · ${adopted}`);
            } else {
              banner.hidden = true;
              banner.replaceChildren();
            }
            banner.onclick = bannerAction;
            return;
          } else {
            if (status.recovery) reviewRecovery(status);
            else {
              say(status.message || "规划未交付，请修改需求或重试。",
                button("edit","修改需求") + button("retry","重试规划"), unresolved(status));
              banner.onclick = bannerAction;
            }
            return;
          }
          await wait(750);
        }
      } catch (failure) {
        if (!signal.aborted) {
          error(failure, button("resume","继续查看任务") + button("edit","返回修改"));
          banner.onclick = bannerAction;
        }
      }
    }
    async function submit() {
      if (choicePending) return;
      if (!spec?.items.some(calendarDate)) return;
      const signal = begin();
      const try_budget_alternatives = intake.querySelector("#planningRelaxBudget")?.checked || false;
      submission ||= crypto.randomUUID().replaceAll("-", "");
      intake.hidden = true;
      showPlaces(null);
      say("正在提交已确认的需求…");
      try {
        const started = await post("start", {request_id:spec.request_id, request_revision:spec.request_revision,
          try_budget_alternatives, submission_id:submission}, signal);
        if (signal.aborted) return;
        job = started.id;
        shown = "";
        url({new:1, request:spec.request_id, job, preview:null, trip:null, candidate:null, day:null});
        remember(job, spec);
        await monitor(signal);
      } catch (failure) {
        if (signal.aborted) return;
        if (failure.status === 409) {
          try { const latest = await get("request", {id:spec.request_id}, signal); submission = null; renderSpec(latest); }
          catch (problem) { error(problem, button("edit","返回修改")); }
        } else error(failure, button("resubmit","重试提交") + button("edit","返回修改"));
      }
    }
    function edit() {
      begin();
      clearPlan();
      url({new:1, job:null, preview:null, request:null, candidate:null, trip:null, day:null});
      return renderForm(spec || {});
    }
    async function bannerAction(event) {
      const action = event.target.closest("[data-online]")?.dataset.online;
      if (action === "edit") return edit();
      if (action === "resume") { banner.onclick = bannerAction; return monitor(begin()); }
      if (action === "retry") submission = null;
      if (action === "retry" || action === "resubmit") return submit();
    }
    banner.onclick = bannerAction;
    intake.onclick = event => {
      const placeButton = event.target.closest("[data-place-focus]");
      if (placeButton) focusPlace(placeRows[Number(placeButton.dataset.placeFocus)]?.id);
      const remove = event.target.closest("[data-place-clear]");
      if (remove) void choosePlace(remove.dataset.placeClear, "clear").catch(failure => {
        const status = intake.querySelector(".planning-error");
        if (status) status.textContent = failure.message;
      });
      if (event.target.closest('[data-online="edit"]')) void edit();
      if (event.target.closest('[data-online="start"]')) void submit();
    };
    return {
      isActive: () => active,
      choosePlace,
      open() { spec = job = submission = lastPreview = null; shown = ""; banner.onclick = bannerAction; return edit(); },
      async resume(savedJob, requestId, preview) {
        const signal = begin();
        spec = null; job = savedJob; shown = ""; lastPreview = null; resumePreview = preview;
        say(savedJob ? "正在读取规划任务…" : "正在恢复已核对的需求…");
        try {
          if (requestId) spec = await get("request", {id:requestId}, signal);
          if (signal.aborted) return;
          if (savedJob) await monitor(signal);
          else if (spec) renderSpec(spec);
        } catch (failure) {
          if (signal.aborted) return;
          if (savedJob) await monitor(signal);
          else error(failure, button("edit","新建需求"));
        }
      },
      leave() { controller?.abort(); active = false; intake.hidden = banner.hidden = true; showPlaces(null); },
    };
  };
})();
