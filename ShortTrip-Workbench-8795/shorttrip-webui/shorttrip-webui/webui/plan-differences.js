/* Shared comparison of archived metrics for alternatives and edit previews. */
(function () {
  "use strict";
  const money = value => "¥" + value.toLocaleString("zh-CN", {maximumFractionDigits: 2});
  const minutes = value => value + " 分钟";
  const rows = [
    ["整团参考费用", "total_cost_cny", money, "参考总价"],
    ["市内接驳", "city_transit_minutes", minutes, "市内接驳"],
    ["其中接驳步行", "access_walk_minutes", minutes, "接驳步行"],
    ["景点游览", "visit_minutes", minutes, "游览时间"],
    ["景点数量", "distinct_attractions", value => value + " 处", "景点"],
  ];
  const costs = {intercity:"往返票", admission:"门票", meals:"用餐", lodging:"住宿", city_transit:"市内交通"};
  function delta(before, after) {
    return Number.isFinite(before) && Number.isFinite(after) ? Math.round((after - before) * 100) / 100 : null;
  }
  function metricRows(before, after) {
    return rows.map(([label, key, format, short]) => ({
      label, key, format, short, before:before[key], after:after[key], change:delta(before[key], after[key]),
    }));
  }
  function compare(before, after) {
    const a = before.archive, b = after.archive;
    if (a.requestId !== b.requestId || a.requestRevision !== b.requestRevision ||
        a.snapshotId !== b.snapshotId || before.startDate !== after.startDate) return null;
    const changes = metricRows(a.metrics, b.metrics);
    const costChanges = Object.entries(costs).flatMap(([key, label]) => {
      const change = delta(a.metrics.cost_breakdown_cny?.[key], b.metrics.cost_breakdown_cny?.[key]);
      return change ? [{label, change}] : [];
    }).sort((x, y) => Math.abs(y.change) - Math.abs(x.change));
    const benefits = [], tradeoffs = [];
    const priorities = {
      total_cost_cny:"优先省钱", city_transit_minutes:"想少花时间在路上",
      access_walk_minutes:"想少走接驳路段", visit_minutes:"想留更多游览时间", distinct_attractions:"想多看几个景点",
    };
    for (const {key, change, short, format} of changes) {
      if (!change) continue;
      const moreIsGain = key === "visit_minutes" || key === "distinct_attractions";
      let line = short + (change > 0 ? "多 " : "少 ") + format(Math.abs(change));
      if (key === "total_cost_cny") {
        const sameDirection = costChanges.filter(cost => Math.sign(cost.change) === Math.sign(change));
        const leading = sameDirection[0];
        if (leading && Math.abs(leading.change) * 2 > sameDirection.reduce((sum, cost) => sum + Math.abs(cost.change), 0)) {
          line += "，主要因为" + leading.label + (change > 0 ? "多 " : "少 ") + money(Math.abs(leading.change));
        }
      }
      (moreIsGain === (change > 0) ? benefits : tradeoffs).push({line, priority:priorities[key]});
    }
    return {changes, benefits, tradeoffs, costChanges, money};
  }
  window.PlanDifferences = {rows, metricRows, compare};
})();
