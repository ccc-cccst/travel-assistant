"use strict";

const $ = id => document.getElementById(id);
const node = (tag, text, className) => {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
};
const button = (text, className, handler) => {
  const element = node("button", text, className);
  element.type = "button";
  element.addEventListener("click", handler);
  return element;
};
const zhNumber = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 });
const number = value => Number.isFinite(value) ? zhNumber.format(value) : "待核实";
const modeNames = { train: "火车", airplane: "飞机", walk: "步行", metro: "地铁", taxi: "打车", bus: "公交" };
const types = { train: "火车", airplane: "飞机", attraction: "游览", lunch: "午餐",
  dinner: "晚餐", breakfast: "早餐", accommodation: "住宿", rest: "休息" };
const weightLabels = { cost: "节省费用", transport: "减少接驳", walking: "少走路", pace: "舒适节奏",
  interest: "兴趣匹配", variety: "体验多样", coherence: "行程连贯", experience: "游览时间" };
const limits = { max_daily_walk_minutes: "每日最多步行", max_daily_transport_minutes: "每日最多市内接驳",
  max_daily_attractions: "每日最多景点数" };
const stageNames = { queued: "等待开始规划", searching: "正在搜索交通、住宿与每日活动",
  comparing: "首份方案已通过核验，继续找备选", validating: "正在核验候选并比较取舍", finished: "规划结束" };
const requestStatuses = { needs_clarification: "还需要确认几项资料", invalid_request: "请检查出行需求",
  planning_error: "需求检查未完成", busy: "规划服务正忙" };
const tripStatuses = { budget_exhausted: "规划已达到时限", no_feasible_plan: "本轮未找到符合条件的行程",
  planning_error: "规划未完成", busy: "规划服务正忙", missing_confirmation: "需求确认已失效" };
let catalog = null;
let cityLookup = 0;
let catalogReady = false;
let busy = false;
let confirmation = null;
let jobId = null;
let profileEdited = false;
let shownOptions = [];
let activeOption = 0;
let activeDay = 0;
let activeStop = null;
let zoom = 1;
let stops = [];
let optionSignature = "";
let lastStage = "";
let lastProgressEventCount = -1;
let planningSource = null;
let tripRefs = new Map();
let visitBeingEdited = null;

async function api(path, payload) {
  let reply;
  try {
    reply = await fetch(path, {
      signal: AbortSignal.timeout(310000),
      ...(payload === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload) })
    });
  } catch {
    throw new Error("暂时无法连接规划服务，请检查服务后重试。");
  }
  const body = await reply.json();
  if (!reply.ok) {
    const error = new Error(body.issues?.[0]?.message || body.error || "请求未完成，请重试。");
    error.status = body.status;
    throw error;
  }
  return body;
}

function flagBusy(value) {
  busy = value;
  $("fields").disabled = value || !catalogReady;
  if ($("try-example")) $("try-example").disabled = value || !catalogReady;
  $("show-saved").disabled = value;
}

function selectOptions(select, choices, initial) {
  select.replaceChildren(...choices.map(([value, label]) => {
    const option = node("option", label);
    option.value = value;
    return option;
  }));
  select.value = initial ?? choices[0][0];
}

function chips(panel, choices, selected = []) {
  panel.replaceChildren(...choices.map(value => {
    const label = node("label", undefined, "chip-option");
    const input = node("input");
    input.type = "checkbox";
    input.value = value;
    input.checked = selected.includes(value);
    label.append(input, node("span", value));
    return label;
  }));
}

function applyPreset() {
  const preset = catalog.presets[$("preset").value];
  for (const [key, value] of Object.entries(preset.weights)) $("weight-" + key).value = value;
  for (const input of $("interests").querySelectorAll("input")) {
    input.checked = (preset.interest_types || []).includes(input.value);
  }
}

function formMessage(message, retry) {
  const panel = $("form-status");
  panel.hidden = !message;
  panel.replaceChildren();
  if (message) panel.append(node("p", message));
  if (retry) panel.append(button("重试加载选项", "button secondary", retry));
}

async function populateOptions() {
  const call = ++cityLookup;
  const city = $("to").value;
  formMessage("正在加载城市选项…");
  try {
    const data = await api("/options" + (city ? "?city=" + encodeURIComponent(city) : ""));
    if (call !== cityLookup) return;
    if (!catalog) {
      const choices = [["", "从描述识别"], ...data.cities.map(value => [value, value])];
      selectOptions($("from"), choices);
      selectOptions($("to"), choices);
      selectOptions($("preset"), Object.entries(data.presets).map(([value, preset]) => [value, preset.label]), "balanced");
      for (const [key, label] of Object.entries(weightLabels)) {
        const field = node("label", label);
        const input = node("input");
        input.type = "number";
        input.id = "weight-" + key;
        input.min = "0";
        input.max = "10";
        input.step = ".5";
        field.append(input);
        $("weights").append(field);
      }
    }
    catalog = data;
    chips($("interests"), city ? data.interest_types : []);
    chips($("food"), city ? data.restaurant_types : []);
    if (!city) {
      for (const id of ["interests", "food"]) $(id).append(node("p", "先在表单中选择目的地。", "status-text"));
    }
    $("musttypes").checked = false;
    $("mustfood").checked = false;
    for (const input of [$("musttypes"), $("mustfood")]) input.disabled = !city;
    const preset = catalog.presets[$("preset").value];
    for (const input of $("interests").querySelectorAll("input")) {
      input.checked = (preset.interest_types || []).includes(input.value);
    }
    if (!catalogReady) applyPreset();
    catalogReady = true;
    formMessage("");
  } catch (error) {
    if (call !== cityLookup) return;
    formMessage(error.message, populateOptions);
  } finally {
    if (call === cityLookup) flagBusy(busy);
  }
}

function collect() {
  const checked = id => Array.from($(id).querySelectorAll("input:checked"), input => input.value);
  const query = { nature_language: $("text").value.trim() };
  for (const [id, field] of [["from", "start_city"], ["to", "target_city"], ["returncap", "return_departure_latest"]]) {
    if ($(id).value) query[field] = $(id).value;
  }
  for (const [id, field] of [["days", "days"], ["people", "people_number"], ["budget", "budget"],
    ["rooms", "rooms"], ["hotelcap", "hotel_price_limit"], ["foodcap", "food_price_limit"]]) {
    if ($(id).value !== "") query[field] = Number($(id).value);
  }
  const hard_limits = {};
  for (const [id, field] of [["walklimit", "max_daily_walk_minutes"],
    ["transitlimit", "max_daily_transport_minutes"], ["stoplimit", "max_daily_attractions"]]) {
    if ($(id).value !== "") hard_limits[field] = Number($(id).value);
  }
  if ($("mode").value) query.intercity_modes = [$("mode").value];
  const interest_types = checked("interests");
  const restaurant_types = checked("food");
  if ($("musttypes").checked) query.must_attraction_types = interest_types;
  if ($("mustfood").checked) query.must_restaurant_types = restaurant_types;
  if (profileEdited || interest_types.length || restaurant_types.length || Object.keys(hard_limits).length) {
    const weights = Object.fromEntries(Object.keys(weightLabels).flatMap(key => {
      const input = $("weight-" + key);
      const value = Number(input.value);
      return input.value !== "" && value !== catalog.presets[$("preset").value].weights[key] ? [[key, value]] : [];
    }));
    query.profile = { preset: $("preset").value, weights, restaurant_types, hard_limits };
    if ($("to").value) query.profile.interest_types = interest_types;
  }
  return query;
}

function stage(step, title, description, moveFocus = false) {
  [...$("steps").children].forEach((item, index) => {
    item.classList.toggle("done", index < step);
    if (index === step) item.setAttribute("aria-current", "step");
    else item.removeAttribute("aria-current");
  });
  $("work-title").textContent = title;
  if ($("announcement").textContent !== description) $("announcement").textContent = description;
  if (moveFocus) $("work-title").focus();
}

function actions(...items) {
  const bar = node("div", undefined, "buttons");
  bar.append(...items);
  return bar;
}

function notice(title, items) {
  const box = node("div", undefined, "notice");
  if (title) box.append(node("strong", title));
  const list = node("ul");
  for (const item of items) list.append(node("li", item));
  box.append(list);
  return box;
}

function backToForm() {
  $("text").focus();
}

function facts(result) {
  const request = result.request;
  const grid = node("dl", undefined, "facts");
  if (!request) return grid;
  const add = (label, value, wide = false) => {
    if (value === undefined || value === null || value === "") return;
    const box = node("div", undefined, wide ? "fact long-fact" : "fact");
    box.append(node("dt", label), node("dd", value));
    grid.append(box);
  };
  add("出发地 → 目的地", (request.start_city || "待确认") + " → " + (request.target_city || "待确认"));
  add("旅行天数 · 人数", (request.days ? request.days + " 天" : "天数待确认") + " · " + request.people_number + " 人");
  add("总预算上限", request.budget == null ? "未指定" : "¥" + number(request.budget));
  add("城际交通", request.intercity_modes?.map(mode => modeNames[mode] || mode).join("、"));
  add("市内交通", request.local_modes?.map(mode => modeNames[mode] || mode).join("、"));
  add("住宿", [request.rooms ? request.rooms + " 间房" : null,
    request.room_type ? "每间 " + request.room_type + " 人" : null,
    request.hotel_price_limit == null ? null : "每间每晚 ≤ ¥" + number(request.hotel_price_limit)].filter(Boolean).join(" · "));
  add("餐费上限", request.food_price_limit == null ? null : "每人每餐 ¥" + number(request.food_price_limit));
  add("去程出发时间窗", [request.outbound_earliest && "最早 " + request.outbound_earliest,
    request.outbound_latest && "最晚 " + request.outbound_latest].filter(Boolean).join(" · "));
  add("返程时间要求", [request.return_departure_latest && "最晚 " + request.return_departure_latest + " 出发",
    request.return_arrival_latest && "最晚 " + request.return_arrival_latest + " 抵达"].filter(Boolean).join(" · "));
  for (const [title, fields] of [
    ["必须安排", ["must_attractions", "must_restaurants", "must_attraction_types", "must_restaurant_types"]],
    ["需要避开", ["excluded_attractions", "excluded_restaurants", "excluded_attraction_types", "excluded_restaurant_types"]],
    ["想体验", ["preferred_attractions", "preferred_restaurants", "preferred_attraction_types", "preferred_restaurant_types"]],
  ]) add(title, [...new Set(fields.flatMap(field => request[field] || []))].join("、"), true);
  const profile = request.profile || {};
  add("规划偏好", catalog?.presets[profile.preset]?.label);
  const namedDimensions = { ...weightLabels, culture: "文化类景点", first_day_sightseeing: "首日游览" };
  add("重点偏好", request.preference_dimensions?.map(key => namedDimensions[key] || key).join("、"));
  add("活动与餐饮类型", [...new Set([...(profile.interest_types || []), ...(profile.restaurant_types || [])])].join("、"), true);
  add("其他偏好", request.soft_preferences?.filter(x => typeof x === "string").join("、"), true);
  add("每日限制", Object.entries(profile.hard_limits || {}).map(([key, value]) =>
    limits[key] + " " + value + (key === "max_daily_attractions" ? " 个" : " 分钟")).join(" · "), true);
  for (const appointment of request.appointments || []) {
    add(appointment.confirmed ? "已确认预约" : "预约待确认", "第 " + appointment.day + " 天 · " + appointment.place + " · " +
      [appointment.start_time, appointment.end_time].filter(Boolean).join("–"), true);
  }
  add("原始描述", request.nature_language, true);
  return grid;
}

function showConfirmation(result) {
  const ready = result.status === "ready";
  stage(1, ready ? "确认这次出行" : (requestStatuses[result.status] || "需求检查未完成"),
    ready ? "核对下面的条件。确认后，规划器会按这份需求搜索。" :
      "请根据下面的提示补充资料，再次检查需求。", true);
  const panel = $("state-content");
  panel.replaceChildren(facts(result));
  const messages = [...new Set([...(result.issues || []).map(issue => issue.message), ...(result.request?.assumptions || [])])];
  if (messages.length) panel.append(notice(ready ? "需要留意" : "需要处理", messages));
  const pending = (result.request?.appointments || []).filter(item => !item.confirmed);
  if (pending.length && pending.every(item => item.day && item.start_time && item.end_time && item.place_options.length)) {
    const form = node("form", undefined, "appointment-form");
    const controls = pending.map(item => {
      const field = node("label", "预约馆区 · 第 " + item.day + " 天 " + item.start_time + "–" + item.end_time);
      const select = node("select");
      select.required = true;
      selectOptions(select, [["", "请选择凭证上的馆区"], ...item.place_options.map(place => [place, place])]);
      field.append(select);
      form.append(field);
      return [item.source, select];
    });
    const submit = node("button", "确认预约馆区", "button primary");
    submit.type = "submit";
    form.append(submit);
    form.addEventListener("submit", event => {
      event.preventDefault();
      interpret(Object.fromEntries(controls.map(([source, select]) => [source, select.value])));
    });
    panel.append(form);
  }
  panel.append(actions(...(ready ? [button("确认并开始规划", "button primary", beginJob)] : []),
    button("返回修改需求", "button secondary", backToForm)));
}

function showFailure(title, description, messages, retry, label = "重试") {
  $("stage-clock").hidden = true;
  stage(confirmation ? 2 : 1, title, description, true);
  const panel = $("state-content");
  panel.replaceChildren();
  if (messages.length) panel.append(notice("", messages));
  panel.append(actions(button(label, "button primary", retry), button("返回修改需求", "button secondary", backToForm)));
}

async function interpret(appointmentChoices = null) {
  if (busy || !catalogReady || !$("trip").reportValidity()) return;
  const query = collect();
  if (appointmentChoices) query.appointment_choices = appointmentChoices;
  confirmation = null;
  jobId = null;
  hideResults();
  flagBusy(true);
  stage(0, "正在检查需求", "正在识别必要条件、偏好和待确认事项。", true);
  $("state-content").replaceChildren(node("span", undefined, "loading-dot"));
  try {
    const result = await api("/interpret", query);
    confirmation = result.confirmation_id || null;
    if (result.status === "planning_error") {
      showFailure("需求检查未完成", "出行需求已保留。", result.issues.map(item => item.message), () => interpret(appointmentChoices));
    } else {
      showConfirmation(result);
    }
  } catch (error) {
    showFailure(requestStatuses[error.status] || "需求检查未完成", "出行需求已保留。", [error.message], () => interpret(appointmentChoices));
  } finally {
    flagBusy(false);
  }
}

function progress(job) {
  const complete = job.status !== "running";
  const usable = job.options.length > 0;
  const clock = $("stage-clock");
  clock.hidden = false;
  clock.textContent = "已用 " + number(job.elapsed_seconds) + " 秒 · 搜索上限 " + number(job.budget_seconds) + " 秒";
  if (!complete) {
    if (lastStage !== job.stage) {
      stage(2, stageNames[job.stage] || "正在规划",
        usable ? "你可以先查看已核验方案，最终比较将在搜索结束后更新。" :
          "方案只有通过本地时间、费用和条件核验后才会显示。");
    }
    lastStage = job.stage;
  }
  const panel = $("state-content");
  if (lastProgressEventCount !== job.events.length || complete) {
    const events = node("ol", undefined, "events");
    for (const event of job.events) {
      const item = node("li");
      item.append(node("time", number(event.seconds) + " 秒"), document.createTextNode(" · " + stageNames[event.stage]));
      events.append(item);
    }
    panel.replaceChildren(events);
    lastProgressEventCount = job.events.length;
  }
  if (usable) {
    const enableEditing = complete && job.status === "success";
    if (enableEditing) planningSource = job.id;
    const changed = renderOptions(job.options, job.comparison_note, !complete);
    if (enableEditing && !changed) drawResult();
  }
  if (!complete) return;
  if (usable) {
    const description = "首份已核验方案耗时 " + number(job.first_valid_seconds) + " 秒。";
    stage(3, job.options.length > 1 ? "两套可比较的行程" : "一套已核验的行程", description);
    if (job.status !== "success") panel.append(notice("搜索未正常结束", job.issues.map(item => item.message)));
    panel.append(actions(button("查看方案", "button primary", () => $("results-title").scrollIntoView({ block: "start" })),
      button("修改出行需求", "button secondary", backToForm)));
  } else {
    showFailure(tripStatuses[job.status] || "规划未完成",
      job.status === "budget_exhausted" ? "在 " + number(job.budget_seconds) + " 秒的搜索预算内，没有核验通过的方案。" :
        "你的已确认需求已保留。", job.issues.map(item => item.message), beginJob, "重新规划");
  }
}

async function followJob() {
  while (jobId) {
    const job = await api("/jobs/" + encodeURIComponent(jobId));
    progress(job);
    if (job.status !== "running") {
      jobId = null;
      return;
    }
    await new Promise(resolve => setTimeout(resolve, 220));
  }
}

async function beginJob() {
  if (busy || !confirmation) return;
  flagBusy(true);
  hideResults();
  lastProgressEventCount = -1;
  lastStage = "";
  stage(2, "正在启动规划", "已提交确认后的需求。", true);
  $("state-content").replaceChildren(node("span", undefined, "loading-dot"));
  try {
    const start = await api("/jobs", { confirmation_id: confirmation });
    jobId = start.id;
    await followJob();
  } catch (error) {
    jobError(error);
  } finally {
    flagBusy(false);
  }
}

function jobError(error) {
  if (error.status === "missing_job") jobId = null;
  if (error.status === "missing_confirmation") confirmation = null;
  showFailure(tripStatuses[error.status] || "规划进度暂时不可用",
    jobId ? "可以重新连接并继续查看这一轮规划。" : "出行需求已保留。", [error.message],
    jobId ? resumeJob : confirmation ? beginJob : () => interpret(),
    jobId ? "重新连接进度" : confirmation ? "重新规划" : "重新检查需求");
}

async function resumeJob() {
  if (busy || !jobId) return;
  flagBusy(true);
  lastStage = "";
  lastProgressEventCount = -1;
  try {
    await followJob();
  } catch (error) {
    jobError(error);
  } finally {
    flagBusy(false);
  }
}

const metricRows = [
  ["total_cost", "费用估算", "元"], ["walking_minutes", "步行", "分钟"],
  ["transport_minutes", "市内接驳", "分钟"], ["sightseeing_minutes", "实际游览", "分钟"]
];

function hideResults() {
  $("results").hidden = true;
  $("stage-clock").hidden = true;
  optionSignature = "";
  shownOptions = [];
  planningSource = null;
  tripRefs = new Map();
  editSummary("");
}

function renderOptions(options, note, partial) {
  $("results").hidden = false;
  $("comparison-note").textContent = partial ? "已收到首份核验结果，正在继续搜索。" :
    note || "选中任一方案，即可查看它的时间线和地点地图。";
  const signature = JSON.stringify(options);
  if (signature === optionSignature) return false;
  const previous = shownOptions[activeOption];
  const index = options.findIndex(item => JSON.stringify(item.plan) === JSON.stringify(previous?.plan));
  shownOptions = options;
  optionSignature = signature;
  activeOption = index < 0 ? 0 : index;
  if (index < 0) activeDay = 0;
  zoom = 1;
  drawResult();
  return true;
}

function drawResult(focusOption = false) {
  const table = node("table", undefined, "compare-table");
  const head = table.createTHead().insertRow();
  const first = node("th", "比较项");
  first.scope = "col";
  head.append(first);
  shownOptions.forEach((option, index) => {
    const cell = node("th", undefined, index === activeOption ? "active-col" : undefined);
    cell.scope = "col";
    const choose = button("方案 " + String.fromCharCode(65 + index), "option-button", () => {
      activeOption = index;
      activeDay = 0;
      zoom = 1;
      drawResult(true);
    });
    choose.setAttribute("aria-pressed", String(index === activeOption));
    cell.append(choose);
    const tags = node("div", undefined, "tags");
    for (const [key, label] of [["total_cost", "更省钱"], ["walking_minutes", "更少走路"]]) {
      const value = option.metrics[key];
      if (shownOptions.length > 1 && Number.isFinite(value) && shownOptions.every((other, j) =>
        j === index || Number.isFinite(other.metrics[key]) && value < other.metrics[key])) {
        tags.append(node("span", label, "tag"));
      }
    }
    if (tags.children.length) cell.append(tags);
    head.append(cell);
  });
  const body = table.createTBody();
  const rows = shownOptions.some(option => option.preferences.evidence.some(item => item.feature === "首日游览"))
    ? [...metricRows, ["first_day_sightseeing", "首日游览", "分钟"]] : metricRows;
  for (const [key, label, unit] of rows) {
    const row = body.insertRow();
    const title = node("th", label);
    title.scope = "row";
    row.append(title);
    shownOptions.forEach((option, index) => {
      const cell = row.insertCell();
      const value = key === "first_day_sightseeing" ? option.metrics.days[0]?.sightseeing_minutes : option.metrics[key];
      if (index === activeOption) cell.className = "active-col";
      cell.append(document.createTextNode(number(value)));
      if (Number.isFinite(value)) cell.append(node("span", unit, "option-unit"));
    });
  }
  $("compare").replaceChildren(table);
  if (focusOption) head.querySelector('[aria-pressed="true"]').focus();
  const current = shownOptions[activeOption];
  const preference = $("preference-evidence");
  preference.replaceChildren();
  const examples = new Map(current.preferences.evidence.map(item => [item.feature, item]));
  if (examples.size) {
    for (const [feature, item] of examples) {
      preference.append(node("p", feature + " · 第 " + item.day + " 天 " + item.position));
    }
  }
  if (current.preferences.missing.length) {
    preference.append(node("p", "本方案尚未安排 " + current.preferences.missing.join("、")));
  }
  preference.hidden = !preference.childElementCount;
  const days = current.plan.itinerary;
  $("day-tabs").replaceChildren(...days.map((day, index) => {
    const control = button("第 " + day.day + " 天", "", () => {
      activeDay = index;
      zoom = 1;
      drawResult();
      $("day-tabs").children[index].focus();
    });
    control.setAttribute("aria-pressed", String(index === activeDay));
    return control;
  }));
  renderTimeline(current, days[activeDay]);
  displayChecks(current);
  renderTripTools();
}

function renderTimeline(option, day) {
  stops = day.activities.map((activity, index) => {
    const isTicket = activity.type === "train" || activity.type === "airplane";
    const title = isTicket ? activity.start + " → " + activity.end : activity.position;
    const location = isTicket ? (option.locations[activity.end] ? activity.end : activity.start) : activity.position;
    return { activity, title, point: option.locations[location], location, index };
  });
  activeStop = stops.find(stop => stop.point)?.index ?? null;
  $("timeline").replaceChildren(...stops.map(({ activity, index, title, point }) => {
    const item = node("li");
    const time = activity.start_time + "–" + activity.end_time;
    const booking = option.bookings?.[day.day + ":" + index];
    const locked = option.reservations?.some(lock => lock.day === day.day && lock.place === activity.position &&
      lock.start_time === activity.start_time && lock.end_time === activity.end_time);
    const label = (types[activity.type] || "活动") +
      ((activity.TrainID || activity.FlightID) ? " · " + (activity.TrainID || activity.FlightID) : "") +
      (!booking && Number.isFinite(activity.cost) ? " · ¥" + number(activity.cost) : "") +
      (locked ? " · 已确认预约" : "") + (point ? "" : " · 无坐标");
    const row = point ? button("", "timeline-stop", () => selectStop(index, "timeline")) :
      node("div", undefined, "timeline-stop");
    if (point) row.dataset.stop = String(index);
    row.append(node("time", time, "timeline-time"), node("span", title, "timeline-name"),
      node("span", label, "timeline-meta"));
    const badge = node("span", String(index + 1), "timeline-number");
    badge.setAttribute("aria-hidden", "true");
    item.append(badge, row);
    if (booking || (activity.type === "attraction" && canEdit())) {
      const extras = node("div", undefined, "stop-actions");
      if (booking) extras.append(renderBooking(booking));
      if (activity.type === "attraction" && canEdit()) {
        const edit = button("编辑", "edit-stop", () => openEditor(day.day, index, activity, locked));
        edit.setAttribute("aria-label", "编辑景点 " + activity.position);
        edit.dataset.editIndex = String(index);
        extras.append(edit);
      }
      item.append(extras);
    }
    const legs = activity.transports || [];
    if (legs.length) {
      const detail = node("details", undefined, "leg-detail");
      const list = node("ul");
      for (const leg of legs) list.append(node("li", leg.start_time + "–" + leg.end_time + " · " +
        (modeNames[leg.mode] || leg.mode) + " · " + leg.start + " → " + leg.end));
      detail.append(node("summary", "查看接驳 · " + legs.length + " 段"), list);
      item.append(detail);
    }
    return item;
  }));
  paintMap();
  updateSelection();
}

function paintMap() {
  const locations = stops.filter(stop => stop.point);
  const map = $("map");
  const markerPanel = $("map-markers");
  const canvas = $("map-lines");
  const width = map.clientWidth;
  const height = map.clientHeight;
  if (!width || !height) return;
  const density = window.devicePixelRatio || 1;
  canvas.width = Math.round(width * density);
  canvas.height = Math.round(height * density);
  const context = canvas.getContext("2d");
  context.scale(density, density);
  $("map-empty").hidden = locations.length > 0;
  $("map-scale").hidden = locations.length === 0;
  $("zoom-in").disabled = !locations.length || zoom >= 2;
  $("zoom-out").disabled = !locations.length || zoom <= 1;
  markerPanel.replaceChildren();
  if (!locations.length) return;
  const lats = locations.map(stop => stop.point[0]);
  const lons = locations.map(stop => stop.point[1]);
  const bounds = values => [Math.min(...values), Math.max(...values)];
  const [south, north] = bounds(lats);
  const [west, east] = bounds(lons);
  const latitude = (south + north) / 2;
  const lonFactor = Math.cos(latitude * Math.PI / 180);
  const longitude = (west + east) / 2;
  const scale = zoom * Math.min((width - 70) / Math.max(.006, (east - west) * lonFactor),
    (height - 70) / Math.max(.006, north - south));
  const pixelsPerMeter = scale / 111195;
  const meters = [10000, 5000, 2000, 1000, 500, 200, 100, 50].find(distance => distance * pixelsPerMeter < 68) || 50;
  $("map-scale").style.width = Math.max(25, meters * pixelsPerMeter) + "px";
  $("map-scale").textContent = meters < 1000 ? meters + " 米" : meters / 1000 + " 公里";
  const selected = locations.find(stop => stop.index === activeStop);
  const center = zoom > 1 && selected ? selected.point : [latitude, longitude];
  const anchors = locations.map(stop => ({
    stop, x: width / 2 + (stop.point[1] - center[1]) * lonFactor * scale,
    y: height / 2 - (stop.point[0] - center[0]) * scale
  }));
  context.beginPath();
  anchors.forEach(({ x, y }, index) => index === 0 ? context.moveTo(x, y) : context.lineTo(x, y));
  context.strokeStyle = "#507c69";
  context.lineWidth = 2;
  context.setLineDash([5, 4]);
  context.stroke();
  const placed = [];
  for (const { stop, x, y } of anchors) {
    if (x < -10 || y < -10 || x > width + 10 || y > height + 10) continue;
    let markerX = x;
    let markerY = y;
    let attempt = 0;
    while (placed.some(([px, py]) => Math.hypot(markerX - px, markerY - py) < 30) && attempt < 18) {
      const angle = attempt * 2.4;
      const radius = 24 + Math.floor(attempt / 6) * 13;
      markerX = Math.min(width - 17, Math.max(17, x + Math.cos(angle) * radius));
      markerY = Math.min(height - 17, Math.max(17, y + Math.sin(angle) * radius));
      attempt++;
    }
    if (attempt) {
      context.beginPath();
      context.moveTo(x, y);
      context.lineTo(markerX, markerY);
      context.setLineDash([]);
      context.lineWidth = 1;
      context.stroke();
    }
    placed.push([markerX, markerY]);
    const marker = button(String(stop.index + 1), "map-marker", () => selectStop(stop.index, "map"));
    marker.dataset.stop = String(stop.index);
    marker.setAttribute("aria-label", "地图地点 " + (stop.index + 1) + " " + stop.location);
    marker.title = stop.location;
    marker.style.left = markerX + "px";
    marker.style.top = markerY + "px";
    markerPanel.append(marker);
  }
  updateSelection();
}

function updateSelection() {
  for (const id of ["map-markers", "timeline"]) {
    for (const control of $(id).querySelectorAll("[data-stop]")) {
      control.setAttribute("aria-pressed", String(Number(control.dataset.stop) === activeStop));
    }
  }
  const current = stops.find(stop => stop.index === activeStop);
  const label = current ? (current.index + 1) + " · " + current.location : "当天无可定位地点";
  if ($("map-selection").textContent !== label) $("map-selection").textContent = label;
}

function selectStop(index, source) {
  activeStop = index;
  if (zoom > 1) {
    paintMap();
    if (source === "map") $("map-markers").querySelector('[aria-pressed="true"]')?.focus({ preventScroll: true });
  }
  updateSelection();
  if (source === "map") $("timeline").querySelector('[aria-pressed="true"]')?.scrollIntoView({ block: "nearest" });
  if (source === "timeline") $("map").scrollIntoView({ block: "nearest" });
}

function displayChecks(option) {
  const activities = option.plan.itinerary.flatMap(day => day.activities);
  const pending = [];
  if (activities.some(a => a.type === "train" || a.type === "airplane")) {
    pending.push(["车票与航班", "核对出行当天的班次、票价和余票。"]);
  }
  if (activities.some(a => a.type === "accommodation")) {
    pending.push(["住宿", "核对入住日期的房价、房型和余房。"]);
  }
  if (activities.some(a => a.type === "attraction")) {
    pending.push(["景点", "核对当日开放时间、门票及预约要求。"]);
  }
  $("check-preview").textContent = [...pending.map(([label]) => label), "费用"].join("、");
  pending.push(["费用估算", "按本地数据计算已安排项目。早餐自理、购物和未安排项目需另计。"]);
  if (option.preferences.missing.length) pending.push(["未满足的偏好", option.preferences.missing.join("、")]);
  $("check-items").replaceChildren(...pending.map(([label, detail]) => {
    const row = node("div", undefined, "check-row");
    row.append(node("strong", label), node("p", detail));
    return row;
  }));
}

$("trip").addEventListener("submit", event => { event.preventDefault(); interpret(); });
$("try-example").addEventListener("click", () => {
  if (!["text", "from", "to", "days", "people", "budget"].some(id => $(id).value)) {
    $("text").value = "两个人从南京去上海玩两天，预算 3000 元，坐火车往返，想吃本帮菜。";
  }
  backToForm();
});
$("trip").addEventListener("input", event => {
  if (event.target.id === "preset" || event.target.id.startsWith("weight-") ||
    event.target.closest("#interests, #food")) profileEdited = true;
  if (!confirmation && !jobId && $("results").hidden && !$("state-content").children.length) return;
  confirmation = null;
  jobId = null;
  hideResults();
  stage(0, "需求已修改", "再次检查新需求后即可确认。");
  $("state-content").replaceChildren();
});
$("to").addEventListener("change", populateOptions);
$("preset").addEventListener("change", applyPreset);
$("zoom-in").addEventListener("click", () => { zoom = Math.min(2, zoom + .5); paintMap(); });
$("zoom-out").addEventListener("click", () => { zoom = Math.max(1, zoom - .5); paintMap(); });
new ResizeObserver(paintMap).observe($("map"));
populateOptions();


function canEdit() {
  return planningSource !== null || tripRefs.has(activeOption);
}

function renderBooking(booking) {
  const block = node("div", undefined, "booking-meta");
  const quote = booking.quote;
  const price = quote.status === "reference" ? "参考价 ¥" + number(quote.amount) : "价格待确认";
  const priceLabel = node("span", price);
  priceLabel.title = quote.date ? quote.date + " · " + quote.people + " 人 · 当日报价待确认" :
    "出行日期和当日报价待确认";
  const link = node("a", booking.entry);
  link.href = booking.url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.title = booking.venue_matched ? "已核对地点对应的入口" : "通用入口，请核对地点与当日信息";
  block.append(priceLabel, link);
  return block;
}

function editSummary(message, error = false) {
  const box = $("edit-summary");
  box.hidden = !message;
  box.classList.toggle("edit-error", error);
  box.textContent = message;
}

function renderTripTools() {
  const tools = $("trip-tools");
  tools.hidden = !canEdit();
  const quoteScope = $("quote-scope");
  quoteScope.hidden = tools.hidden;
  if (tools.hidden) return;
  const trip = tripRefs.get(activeOption);
  const people = shownOptions[activeOption].plan.people_number;
  quoteScope.textContent = trip?.departure_date ? trip.departure_date + " 出发 · " + people + " 人 · 当日报价待确认。" :
    "当前显示参考价。核价时请在入口核对实际日期和人数。";
  const dateKey = trip ? trip.id + ":" + trip.revision : planningSource + ":" + activeOption;
  if ($("trip-date").dataset.option !== dateKey) {
    $("trip-date").value = trip?.departure_date || "";
    $("trip-date").dataset.option = dateKey;
  }
  const save = button(trip ? "保存核价日期" : "保存此方案", "button secondary", () => writeTrip("save"));
  const controls = [save];
  if (trip) {
    controls.push(button("恢复原方案", "button secondary", () => {
      if (window.confirm("恢复原方案或最近可保留已确认预约的版本？")) writeTrip("restore");
    }));
  }
  $("trip-actions").replaceChildren(...controls);
}

function editFeedback(message) {
  const feedback = $("edit-feedback");
  feedback.hidden = !message;
  feedback.textContent = message;
}

async function openEditor(day, index, activity, locked) {
  visitBeingEdited = { day, index, activity };
  $("edit-place").value = activity.position;
  $("edit-start").value = activity.start_time;
  $("edit-end").value = activity.end_time;
  $("edit-context").textContent = locked ? "预约地点和时段已锁定。" :
    "第 " + day + " 天。可改景点或游览时段，系统会核验前后接驳与已确认的安排。";
  $("lock-visit").hidden = locked;
  $("edit-places").replaceChildren();
  editFeedback("");
  $("edit-dialog").showModal();
  const city = shownOptions[activeOption].plan.target_city;
  const selected = visitBeingEdited;
  try {
    const options = await api("/options?city=" + encodeURIComponent(city));
    if (visitBeingEdited !== selected) return;
    $("edit-places").replaceChildren(...options.attractions.map(name => {
      const option = node("option");
      option.value = name;
      return option;
    }));
  } catch (error) {
    if (visitBeingEdited === selected) editFeedback(error.message);
  }
}

function changeLine(before, after) {
  return [["total_cost", "总参考价", " 元"], ["transport_minutes", "接驳", " 分钟"],
    ["walking_minutes", "步行", " 分钟"], ["sightseeing_minutes", "游览", " 分钟"]]
    .filter(([key]) => before[key] !== after[key])
    .map(([key, label, unit]) => label + " " + number(before[key]) + " → " + number(after[key]) + unit)
    .join(" · ");
}

async function writeTrip(action, amendment = {}) {
  if (busy || !canEdit()) return;
  const index = activeOption;
  const before = shownOptions[index].metrics;
  const existing = tripRefs.get(index);
  const source = existing ? { trip_id: existing.id, revision: existing.revision } :
    { job_id: planningSource, option_index: index };
  const editing = $("edit-dialog").open;
  flagBusy(true);
  $("apply-edit").disabled = $("lock-visit").disabled = true;
  $("trip-date").disabled = true;
  if (editing) editFeedback("正在核验改动…");
  else editSummary("正在处理…");
  try {
    const trip = await api("/itineraries", { ...source, ...amendment, action,
      departure_date: $("trip-date").value || null });
    tripRefs.set(index, trip);
    shownOptions[index] = trip.option;
    if (editing) $("edit-dialog").close();
    drawResult();
    if (action === "edit") {
      const change = changeLine(before, trip.option.metrics);
      editSummary("改动已核验并保存。" + (change ? " " + change : " 分项指标无变化。"));
    } else if (action === "lock") {
      editSummary("已保存预约地点和时段。后续调整会保留这项预约。");
    } else if (action === "restore") {
      editSummary("已恢复可保留确认预约的版本。");
    } else {
      editSummary("已保存到本机，可从「已保存行程」重新打开。");
    }
    if (editing && amendment.index !== undefined) {
      $("timeline").querySelector('[data-edit-index="' + amendment.index + '"]')?.focus({ preventScroll: true });
    }
  } catch (error) {
    if (editing) editFeedback(error.message);
    else editSummary(error.message, true);
  } finally {
    flagBusy(false);
    $("apply-edit").disabled = $("lock-visit").disabled = false;
    $("trip-date").disabled = false;
  }
}

async function showSaved() {
  $("saved-list").replaceChildren();
  $("saved-status").textContent = "正在读取本机行程…";
  $("saved-dialog").showModal();
  try {
    const { trips } = await api("/itineraries");
    $("saved-status").textContent = trips.length ? "选择要重新打开的行程。" : "保存方案后会出现在这里。";
    for (const trip of trips) {
      const row = node("li");
      const open = button(trip.title, "button secondary", () => openSaved(trip.id));
      const updated = new Date(trip.updated).toLocaleString("zh-CN", { hour12: false });
      row.append(open, node("small", "更新于 " + updated));
      $("saved-list").append(row);
    }
  } catch (error) {
    $("saved-status").textContent = error.message;
  }
}

async function openSaved(id) {
  $("saved-status").textContent = "正在打开行程…";
  try {
    const trip = await api("/itineraries/" + encodeURIComponent(id));
    if (!$("saved-dialog").open) return;
    $("saved-dialog").close();
    hideResults();
    confirmation = null;
    jobId = null;
    tripRefs.set(0, trip);
    stage(3, trip.title, "已打开本机保存的行程。", true);
    $("state-content").replaceChildren();
    renderOptions([trip.option], "已打开一套保存的方案。", false);
  } catch (error) {
    $("saved-status").textContent = error.message;
  }
}

$("show-saved").addEventListener("click", showSaved);
$("close-saved").addEventListener("click", () => $("saved-dialog").close());
$("close-edit").addEventListener("click", () => { if (!busy) $("edit-dialog").close(); });
$("edit-dialog").addEventListener("cancel", event => { if (busy) event.preventDefault(); });
$("edit-dialog").addEventListener("close", () => { visitBeingEdited = null; });
$("edit-form").addEventListener("submit", event => {
  event.preventDefault();
  const { day, index } = visitBeingEdited;
  writeTrip("edit", { day, index, place: $("edit-place").value.trim(),
    start_time: $("edit-start").value, end_time: $("edit-end").value });
});
$("lock-visit").addEventListener("click", () => {
  const { day, index, activity } = visitBeingEdited;
  const same = activity.position === $("edit-place").value.trim() &&
    activity.start_time === $("edit-start").value && activity.end_time === $("edit-end").value;
  if (!same) {
    editFeedback("请先应用当前修改，再把计划中的地点和时段标记为已确认预约。");
    return;
  }
  if (window.confirm("你已核对预约凭证上的地点和时段，并希望锁定这项活动？")) {
    writeTrip("lock", { day, index });
  }
});

