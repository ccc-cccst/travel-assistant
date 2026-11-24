(function () {
  "use strict";
  const {cases, rounds} = window.ComparisonStudy;
  const byId = id => document.getElementById(id);
  const elements = Object.fromEntries(["setup", "enroll", "studyStatus", "task", "taskTitle", "taskPrompt",
    "showComparison", "comparisonFrame", "decision", "stopTask", "finished", "nextParticipant",
    "facilitator", "recordList", "downloadStudy"].map(id => [id, byId(id)]));
  const storageKey = "shorttrip.study.comparison.v1";
  let records = [];
  let participant = "";
  let position = 0;
  let active = null;
  let loadDeadline = null;
  let storageUnavailable = false;
  try {
    const cached = JSON.parse(localStorage.getItem(storageKey) || "[]");
    if (Array.isArray(cached)) records = cached.filter(row => row && Object.hasOwn(cases, row.scenario));
  } catch {
    storageUnavailable = true;
  }

  function status(message = "") {
    elements.studyStatus.textContent = message || (storageUnavailable ? "浏览器未能保存记录，请在关闭前导出。" : "");
  }
  function save() {
    try { localStorage.setItem(storageKey, JSON.stringify(records)); }
    catch { storageUnavailable = true; }
  }
  function assignment() {
    return rounds[(Number(participant.slice(1)) - 1) % rounds.length][position];
  }
  function setupTask() {
    const [scenario] = assignment();
    elements.setup.hidden = elements.finished.hidden = elements.facilitator.hidden = true;
    elements.task.hidden = false;
    elements.taskTitle.textContent = `第 ${position + 1} 题 · ${cases[scenario].title}`;
    elements.taskPrompt.textContent = "你想尽量省钱，但比另一方案多花在市内接驳的时间最多能接受 60 分钟。你会选哪一份？";
    elements.showComparison.hidden = false;
    elements.showComparison.disabled = false;
    elements.showComparison.focus();
    elements.decision.hidden = elements.stopTask.hidden = true;
    elements.decision.reset();
    elements.comparisonFrame.replaceChildren();
    status();
  }
  function finishSession() {
    status();
    elements.setup.hidden = elements.task.hidden = true;
    elements.finished.hidden = elements.facilitator.hidden = false;
    elements.nextParticipant.focus();
    renderRecords();
  }
  elements.enroll.onsubmit = event => {
    event.preventDefault();
    const number = String(new FormData(elements.enroll).get("participant") || "").toUpperCase();
    if (!/^P(0[1-9]|[1-9][0-9])$/.test(number)) {
      status("请输入 P01 至 P99 之间的参与者编号。");
      return;
    }
    participant = number;
    position = [0,1].find(index => !records.some(row => row.participant === participant && row.position === index));
    if (position === undefined) return finishSession();
    setupTask();
  };
  elements.showComparison.onclick = () => {
    if (active) return;
    const [scenario, display] = assignment();
    const pair = cases[scenario];
    const token = crypto.randomUUID().replaceAll("-", "");
    const path = new URL("/", location.origin);
    for (const [key, value] of Object.entries({job:pair.job,candidate:pair.reference,
      comparison_case:scenario,comparison_display:display,comparison_token:token})) {
      path.searchParams.set(key, value);
    }
    const frame = document.createElement("iframe");
    frame.title = pair.title + "方案比较";
    frame.src = path.href;
    frame.style.visibility = "hidden";
    active = {scenario, display, token, frame, launched:performance.now(), ready:null, loadMilliseconds:null,
      firstChoiceMilliseconds:null};
    elements.showComparison.hidden = true;
    elements.stopTask.hidden = true;
    elements.comparisonFrame.replaceChildren(frame);
    status("正在打开比较页面…");
    loadDeadline = setTimeout(() => {
      if (!active || active.ready != null) return;
      status("比较页面加载时间较长。可以继续等待，或将此题记录为未完成。");
      elements.stopTask.hidden = false;
    }, 15000);
  };
  window.addEventListener("message", event => {
    if (!active || active.ready != null || event.origin !== location.origin ||
        event.source !== active.frame.contentWindow || event.data?.type !== "shorttrip-comparison-ready" ||
        event.data?.token !== active.token || event.data?.scenario !== active.scenario) return;
    clearTimeout(loadDeadline);
    active.ready = Math.round(performance.now());
    active.loadMilliseconds = Math.round(active.ready - active.launched);
    active.frame.style.visibility = "";
    elements.decision.hidden = elements.stopTask.hidden = false;
    status();
  });
  function finishTask(answer) {
    if (!active) return;
    const {scenario, display, ready, loadMilliseconds, firstChoiceMilliseconds} = active;
    clearTimeout(loadDeadline);
    const complete = answer !== null && ready != null;
    const row = {participant, position, scenario, display, status:complete ? "complete" : (ready == null ? "not_loaded" : "unfinished"),
      decisionMilliseconds:ready == null ? null : Math.round(performance.now() - ready),
      loadMilliseconds, firstChoiceMilliseconds, choice:answer?.choice ?? null, benefit:answer?.benefit ?? "",
      tradeoff:answer?.tradeoff ?? "", correctChoice:complete ? answer.choice === cases[scenario].expectedChoice : null,
      correctBenefit:null, correctTradeoff:null, help:0, manualChecks:0};
    records.push(row);
    save();
    active = null;
    elements.comparisonFrame.replaceChildren();
    if (++position >= 2) finishSession();
    else setupTask();
  }
  elements.decision.onsubmit = event => {
    event.preventDefault();
    if (!active || active.ready == null) return;
    const form = new FormData(elements.decision);
    const answer = Object.fromEntries([...form].map(([key, value]) => [key, String(value).trim()]));
    if (!answer.benefit || !answer.tradeoff) {
      status("请分别填写主要好处和主要代价。");
      return;
    }
    finishTask(answer);
  };
  elements.decision.onchange = event => {
    if (event.target.name === "choice" && active?.ready != null && active.firstChoiceMilliseconds == null) {
      active.firstChoiceMilliseconds = Math.max(0, Math.round(performance.now() - active.ready));
    }
  };
  elements.stopTask.onclick = () => finishTask(null);
  elements.nextParticipant.onclick = () => {
    elements.finished.hidden = true;
    elements.setup.hidden = false;
    elements.enroll.reset();
    elements.enroll.elements.participant.focus();
  };

  function labelled(text, control) {
    const label = document.createElement("label");
    label.append(text, control);
    return label;
  }
  function grade(row, key, caption) {
    const select = document.createElement("select");
    select.setAttribute("aria-label", caption);
    for (const [value, label] of [["","未核对"],["yes","正确"],["no","有误"]]) select.add(new Option(label, value));
    select.value = row[key] == null ? "" : row[key] ? "yes" : "no";
    select.onchange = () => { row[key] = select.value ? select.value === "yes" : null; save(); status(); };
    return labelled(caption, select);
  }
  function count(row, key, caption) {
    const input = document.createElement("input");
    input.type = "number"; input.min = "0"; input.step = "1";
    input.value = row[key] || 0;
    input.onchange = () => {
      row[key] = Math.max(0, Math.trunc(Number(input.value) || 0));
      input.value = row[key];
      save();
      status();
    };
    return labelled(caption, input);
  }
  function renderRecords() {
    elements.recordList.replaceChildren();
    elements.downloadStudy.disabled = records.length === 0;
    if (!records.length) {
      elements.recordList.textContent = "暂无参与者记录。";
      return;
    }
    const statuses = {complete:"已回答", unfinished:"未完成", not_loaded:"页面未加载"};
    const choice = {reference:"首推",alternative:"备选 1",undecided:"无法决定"};
    for (const row of records) {
      const article = document.createElement("article");
      const title = document.createElement("strong");
      title.textContent = `${row.participant} · 第 ${row.position + 1} 题 · ${cases[row.scenario].title} · ${row.display === "table" ? "分项表" : "取舍说明"}`;
      const results = document.createElement("dl");
      for (const [label, value] of Object.entries({
        状态:statuses[row.status], 选择:choice[row.choice] || "未选择",
        首次选择:row.firstChoiceMilliseconds == null ? "无" : `${(row.firstChoiceMilliseconds / 1000).toFixed(2)} 秒`,
        提交作答:row.decisionMilliseconds == null ? "无" : `${(row.decisionMilliseconds / 1000).toFixed(2)} 秒`,
        页面加载:row.loadMilliseconds == null ? "无" : `${(row.loadMilliseconds / 1000).toFixed(2)} 秒`,
        好处:row.benefit || "未填写", 代价:row.tradeoff || "未填写", 核对依据:cases[row.scenario].answer,
      })) {
        const term = document.createElement("dt"), definition = document.createElement("dd");
        term.textContent = label;
        definition.textContent = value;
        results.append(term, definition);
      }
      const review = document.createElement("div");
      review.className = "review-inputs";
      if (row.status === "complete") review.append(grade(row,"correctBenefit","好处"), grade(row,"correctTradeoff","代价"));
      review.append(count(row,"help","求助次数"), count(row,"manualChecks","手工核对次数"));
      article.append(title, results, review);
      elements.recordList.append(article);
    }
  }
  elements.downloadStudy.onclick = () => {
    const content = JSON.stringify({schema:"shorttrip_comparison_study_v1", records}, null, 2);
    const url = URL.createObjectURL(new Blob([content], {type:"application/json"}));
    const link = document.createElement("a");
    link.href = url; link.download = "shorttrip-方案比较记录.json";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  renderRecords();
  status();
})();
