/* Presentation for the separately addressable city-atlas workbench. */
(function () {
  "use strict";
  const esc = value => String(value ?? "").replace(/[&<>"']/g, ch =>
    ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[ch]));
  const money = value => "¥" + Math.round(Math.abs(value)).toLocaleString("zh-CN");
  const duration = value => {
    const minutes = Math.round(Math.abs(value));
    return minutes >= 60 ? Math.floor(minutes / 60) + " 小时" + (minutes % 60 ? " " + minutes % 60 + " 分" : "") : minutes + " 分钟";
  };
  let modalTrigger = null;
  let modalInert = [];
  const byId = id => document.getElementById(id);

  function dayTitle(trip, day) {
    const names = day.items.filter(item => item.kind === "attraction").map(item => item.name);
    const featured = names.slice(0, 2);
    return "第 " + day.day + " 天 · " + (featured.length ? featured.join("、") : day.title || trip.city);
  }
  function time(item) {
    if (!item.start) return "";
    return '<div class="atlas-time"><time>' + esc(item.start) + '</time>' +
      (item.end ? '<span>— ' + esc(item.end) + '</span>' : "") + '</div>';
  }
  function comparisonHero(comparison, before, after) {
    const primary = comparison.changes.filter(row => ["total_cost_cny", "city_transit_minutes"].includes(row.key));
    const metrics = primary.map(row => {
      const cost = row.key === "total_cost_cny";
      const comparable = row.change !== null;
      const direction = comparable ? row.change > 0 ? "多" : row.change < 0 ? "少" : "相同" : "待核实";
      return '<div class="atlas-delta"><span>' + esc(row.label) + '</span><strong>' +
        (comparable && row.change !== 0 ? '<small>' + direction + '</small>' + (cost ? money(row.change) : duration(row.change)) : direction) +
        '</strong><span>' + (Number.isFinite(row.before) && Number.isFinite(row.after) ?
          (cost ? money(row.before) : duration(row.before)) + ' → ' + (cost ? money(row.after) : duration(row.after)) : "资料不足") + '</span></div>';
    }).join("");
    const locations = plan => {
      const places = new Map();
      for (const day of plan.days) for (const item of day.items) {
        if (item.kind !== "attraction") continue;
        if (!places.has(item.name)) places.set(item.name, new Set());
        places.get(item.name).add(day.day);
      }
      return places;
    };
    const oldDays = locations(before), newDays = locations(after);
    const moved = [...oldDays].flatMap(([name, days]) => {
      const next = newDays.get(name);
      if (!next || [...days].join() === [...next].join()) return [];
      return ['<li><strong>' + esc(name) + '</strong><span>第 ' + esc([...days].join("、")) +
        ' 天 → 第 ' + esc([...next].join("、")) + ' 天</span></li>'];
    });
    return '<div class="atlas-deltas">' + metrics + '</div>' +
      (moved.length ? '<details class="atlas-rescheduled"><summary>' + moved.length +
        ' 个景点调整了日期</summary><ul>' + moved.join("") + '</ul></details>' : "");
  }
  function featuredAlternative(versions, referenceIndex) {
    const reference = versions[referenceIndex];
    const metrics = reference.archive.metrics;
    if (!metrics) return null;
    const choices = versions.flatMap((version, index) => {
      if (index === referenceIndex || !version.archive?.metrics || version.archive.label?.includes("首份")) return [];
      const comparison = PlanDifferences.compare(reference, version);
      if (!comparison) return [];
      const delta = Object.fromEntries(comparison.changes.map(row => [row.key, row.change]));
      return [{index, delta}];
    });
    const measures = [
      ["total_cost_cny", "预算优先", Math.max(50, (metrics.total_cost_cny || 0) * .05), -1],
      ["city_transit_minutes", "少花时间在路上", Math.max(15, (metrics.city_transit_minutes || 0) * .05), -1],
      ["access_walk_minutes", "少走接驳路", 15, -1],
      ["visit_minutes", "多留游览时间", 30, 1],
    ];
    for (const [key, label, minimum, direction] of measures) {
      const distinct = choices.filter(choice => choice.delta[key] != null && direction * choice.delta[key] >= minimum);
      distinct.sort((a,b) => direction * (b.delta[key] - a.delta[key]) || a.index - b.index);
      if (distinct.length) return {...distinct[0], label};
    }
    return null;
  }

  function priceText(version) {
    const price = version.archive?.metrics?.total_cost_cny;
    return Number.isFinite(price) ? "约 " + money(price) : "费用待核实";
  }

  function tradeoffText(delta) {
    const {total_cost_cny:cost, city_transit_minutes:travel, access_walk_minutes:walk, visit_minutes:visit,
      distinct_attractions:count} = delta;
    const facts = [];
    if (cost) facts.push((cost < 0 ? "约省 " : "约多花 ") + money(cost));
    if (travel) facts.push("市内接驳" + (travel < 0 ? "少 " : "多 ") + duration(travel));
    if (Math.abs(walk) >= 15) facts.push("其中步行" + (walk < 0 ? "少 " : "多 ") + duration(walk));
    if (visit) facts.push("游览" + (visit < 0 ? "少 " : "多 ") + duration(visit));
    if (count) facts.push("景点" + (count < 0 ? "少 " : "多 ") + Math.abs(count) + " 处");
    return facts;
  }

  function renderPlans(trip, group, current, {select, compare, labelFor}) {
    const panel = byId("archiveOptions");
    panel.hidden = !trip?.archive;
    if (panel.hidden) return;
    const versions = group.versions;
    const recommended = versions.findIndex(version => version.archive?.recommended);
    const reference = Math.max(0, recommended);
    const featured = !trip.revision && featuredAlternative(versions, reference);
    const featuredIndices = trip.revision ? [] : [reference, featured?.index].filter(Number.isInteger);
    const labels = new Map(versions.map((version,index) => [version,
      index === recommended ? "推荐方案" : index === featured?.index ? featured.label : labelFor(version)]));
    const getLabel = version => labels.get(version) || labelFor(version);
    const currentLabel = trip.revision ? labelFor(trip) : getLabel(versions[current]);
    byId("atlasCurrentPlan").textContent = currentLabel + " · " + priceText(trip);
    const selectedCard = featuredIndices.includes(current) && !trip.revision;
    const choices = byId("atlasPlanChoices");
    choices.hidden = !featuredIndices.length;
    choices.innerHTML = featuredIndices.map(index => {
      const version = versions[index];
      const selected = current === index;
      return `<button type="button" class="atlas-plan-card" data-atlas-plan="${index}" aria-pressed="${selected}">
        <span class="atlas-plan-name">${esc(getLabel(version))}</span>
        <strong>${esc(priceText(version))}</strong>
        <small>${selected ? "正在查看 · 整团参考" : "整团参考"}</small>
      </button>`;
    }).join("");
    const delta = byId("atlasPlanDelta");
    delta.hidden = !featured;
    if (featured) {
      const referenceLabel = recommended < 0 ? "第一套方案" : "推荐方案";
      delta.innerHTML = `<span>${esc(featured.label)}相比${referenceLabel}</span>
        <strong>${esc(tradeoffText(featured.delta).join(" · "))}</strong>`;
    }
    const more = byId("atlasMorePlans");
    byId("atlasOtherPlans").innerHTML = versions.map((version,index) =>
      `<li${featuredIndices.includes(index) ? ' class="atlas-featured-menu-item"' : ""}><button type="button" data-atlas-plan="${index}" aria-pressed="${!trip.revision && current === index}">
        <span>${esc(getLabel(version))}</span><span>${esc(priceText(version))}</span></button></li>`).join("");
    more.hidden = versions.length < 2 && !trip.revision;
    more.open = false;
    panel.classList.toggle("atlas-show-current", !selectedCard);
    panel.onclick = event => {
      const button = event.target.closest("[data-atlas-plan]");
      if (!button) return;
      const index = Number(button.dataset.atlasPlan);
      if (index === current && !trip.revision) return;
      select(index);
      const next = choices.querySelector(`[data-atlas-plan="${index}"]`);
      (next?.getClientRects().length ? next : byId("atlasMorePlansSummary"))?.focus({preventScroll:true});
    };
    byId("atlasReview").onclick = () => byId("reviewBtn").click();
    const compareButton = byId("atlasCompare");
    compareButton.hidden = versions.length < 2 || Boolean(trip.revision);
    compareButton.onclick = () => {
      if (featured && selectedCard) compare(reference, featured.index, getLabel);
      else compare(current, current === reference ? undefined : reference, getLabel);
    };
  }

  function setPlanContext(section, tab) {
    byId("archiveOptions").classList.toggle("atlas-plans-compact", section !== "overview" || tab !== "detail");
  }

  function render(trip, group, current, actions) {
    byId("atlasOriginal").href = "./" + location.search;
    byId("atlasOriginal").onclick = () => {byId("atlasOriginal").href = "./" + location.search;};
    byId("atlasCaption").textContent = trip ?
      [trip.startCity && "从" + trip.startCity + "出发", trip.people && trip.people + " 人同行", trip.startDate].filter(Boolean).join(" · ") : "写下旅行愿望，从这里出发";
    byId("title").textContent = trip ? trip.city + " " + trip.days.length + " 日行程" : "下一站，想去哪里";
    renderPlans(trip, group, current, actions);
  }

  function openModal() {
    if (!modalInert.length) {
      modalTrigger = document.activeElement;
      modalInert = [document.querySelector(".topbar"), byId("layout"), byId("drawer")]
        .filter(Boolean).map(element => [element, element.inert]);
      for (const [element] of modalInert) element.inert = true;
    }
    const modal = byId("modalBox");
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    modal.setAttribute("tabindex", "-1");
    const heading = modal.querySelector("h3, h2");
    if (heading) {heading.id = "atlasModalTitle"; modal.setAttribute("aria-labelledby", heading.id);}
    else modal.setAttribute("aria-label", "行程操作");
    modal.focus({preventScroll:true});
  }
  function closeModal() {
    for (const [element, inert] of modalInert) element.inert = inert;
    modalInert = [];
    if (modalTrigger?.isConnected && !modalTrigger.closest("[hidden]")) modalTrigger.focus({preventScroll:true});
    else {
      byId("planBody").tabIndex = -1;
      byId("planBody").focus({preventScroll:true});
    }
  }
  function finishComparison() {
    const changes = byId("archiveDayDelta");
    const heading = changes.querySelector("h4");
    if (!heading) return;
    const list = heading.nextElementSibling;
    const details = document.createElement("details");
    details.className = "compare-metrics";
    const summary = document.createElement("summary");
    summary.textContent = "逐日查看地点与路线变化";
    heading.replaceWith(details);
    details.append(summary);
    if (list) details.append(list);
  }
  document.addEventListener("keydown", event => {
    if (byId("modal").hidden || byId("routeDialog").open) return;
    if (event.key === "Escape") {
      event.preventDefault();
      byId("modal").click();
    }
    if (event.key === "Tab") {
      const fields = [...byId("modalBox").querySelectorAll('button, a[href], input, select, textarea, [tabindex="0"]')]
        .filter(element => !element.disabled && element.getClientRects().length);
      const first = fields[0], last = fields.at(-1);
      if (!first) {event.preventDefault();return;}
      if (event.shiftKey && [first, byId("modalBox")].includes(document.activeElement)) {last.focus();event.preventDefault();}
      else if (!event.shiftKey && document.activeElement === last) {first.focus();event.preventDefault();}
    }
  });
  document.addEventListener("click", event => {
    const action = event.target.closest("[data-atlas-action]");
    if (!action) return;
    byId("atlasMore").open = false;
    byId(action.dataset.atlasAction)?.click();
  });
  window.AtlasView = {dayTitle, time, comparisonHero, finishComparison, render, setPlanContext, openModal, closeModal};
})();
