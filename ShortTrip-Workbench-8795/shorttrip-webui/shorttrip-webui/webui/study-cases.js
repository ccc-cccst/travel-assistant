/* Frozen comparison pairs for the local usability study. */
(function () {
  "use strict";
  const cases = {
    chengdu: {
      title:"武汉到成都，两日游", job:"7cfccaa570c744818c257c37a4256826",
      reference:"6e08e84d0129a23b651bd0bc", alternative:"96f8ca3a97f77dc5e924c802",
      expectedChoice:"alternative",
      answer:"备选参考总价少 ¥414.43，市内接驳多 44 分钟，其中接驳步行多 18 分钟。",
    },
    beijing: {
      title:"成都到北京，两日游", job:"8982ab8ace9946f08a1b1461e667930c",
      reference:"84e08514374940d408336e0e", alternative:"8b21e7f618c82cb9cfa7c238",
      expectedChoice:"reference",
      answer:"备选参考总价少 ¥445.62，市内接驳多 106 分钟，其中接驳步行多 81 分钟。",
    },
  };
  const rounds = [
    [["chengdu","table"], ["beijing","summary"]],
    [["chengdu","summary"], ["beijing","table"]],
    [["beijing","table"], ["chengdu","summary"]],
    [["beijing","summary"], ["chengdu","table"]],
  ];
  function recognize(params) {
    if (window.parent === window) return null;
    const scenario = params.get("comparison_case");
    const token = params.get("comparison_token");
    const display = params.get("comparison_display");
    const pair = Object.hasOwn(cases, scenario) ? cases[scenario] : null;
    if (!pair || !/^[a-f0-9]{32}$/.test(token || "") || !["table","summary"].includes(display) ||
        params.get("job") !== pair.job || params.get("candidate") !== pair.reference) return null;
    return {pair, scenario, token, display};
  }
  window.ComparisonStudy = {cases, rounds, recognize};
})();
