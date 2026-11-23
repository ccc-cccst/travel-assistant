// Run with SHORTTRIP_PLAYWRIGHT set to the local playwright-core path.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {chromium} = require(process.env.SHORTTRIP_PLAYWRIGHT || "playwright");
const root = process.env.SHORTTRIP_WORKBENCH_URL || "http://127.0.0.1:8795";
const compare = "/?job=c65c872e0b0e4120b8570a80e08e96a4&candidate=597355ee4d2364e93db90af5&day=1";
const base = {job:"01b135fda6c446b38440ab8716b0f4c2", candidate:"4c98df9bbd58c313bcc80b88"};
const sample = "/?" + new URLSearchParams({...base, day:3});
const id = slot => base.candidate + "-d3-a" + slot;
const batch = {action:"batch", schema_version:1, day:3, operations:[
  {action:"time", activity:id(1), start:"12:05", end:"13:05"},
  {action:"meal_options", activity:id(3)},
]};
const output = process.argv[2];
const report = {desktop:{}, phone:{}, endpoint:{}, errors:[]};
const endpoint = (name, query) => root + "/api/itinerary-edits/" + name + (query ? "?" + new URLSearchParams(query) : "");
async function settle(request, trial) {
  const started = await request.post(endpoint("preview"), {data:{...base, ...trial}});
  const initial = await started.json();
  if (started.status() !== 202) return {code:started.status(), ...initial};
  const deadline = Date.now() + 65000;
  while (Date.now() < deadline) {
    const response = await request.get(endpoint("state", {id:initial.id}));
    const value = await response.json();
    if (!["planning", "starting"].includes(value.state)) return value;
    await new Promise(resolve => setTimeout(resolve, 400));
  }
  throw new Error("试排没有在等待时限内结束");
}
async function run() {
  const browser = await chromium.launch({channel:"chrome", headless:true});
  try {
    const desktop = await browser.newContext({viewport:{width:1250,height:880},reducedMotion:"reduce"});
    const page = await desktop.newPage();
    page.on("pageerror", error => report.errors.push(error.message));
    await page.goto(root + compare, {waitUntil:"domcontentloaded"});
    await page.locator("#compareBtn").click();
    const summary = page.locator("#archiveTakeaways");
    await summary.locator(".decision-summary").waitFor();
    report.desktop.comparison = await summary.innerText();
    assert.match(report.desktop.comparison, /相比/);
    await page.getByText("查看完整数据",{exact:true}).click();
    assert.match(await page.locator("#archiveDelta").innerText(), /整团参考费用/);
    await page.screenshot({path:path.join(path.dirname(output),"desktop.png")});
    const preferred = await page.locator("#archiveCompare").inputValue();
    await page.locator("#openCompared").click();
    assert.equal(await page.locator("#archiveChoice").inputValue(), preferred);

    const phone = await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,
      deviceScaleFactor:2,reducedMotion:"reduce"});
    const mobile = await phone.newPage();
    mobile.on("pageerror", error => report.errors.push(error.message));
    // The sample is an existing program-only test itinerary. Supply a repeatable model response
    // here; every subsequent preview, check, save and reopen uses the real backend.
    await mobile.route("**/api/itinerary-edits/assist", route => route.fulfill({json:{
      state:"proposal", message:"我会一起试排第 3 天的这些调整。",
      proposal:{schema_version:1, day:3, operations:batch.operations, pace:null,
        checklist:["午餐调整到 12:05–13:05。","晚饭换一家顺路的餐馆。"],
        locks:[{day:3,name:"上海博物馆",start:"15:00"}]},
    }}));
    await mobile.goto(root + sample,{waitUntil:"domcontentloaded"});
    await mobile.locator(".archive-view").waitFor();
    const chat = mobile.getByRole("button",{name:"AI助手",exact:true});
    if (await chat.isVisible()) await chat.click();
    await mobile.locator("#askInput").fill("第三天午餐改成12:05到13:05，晚饭换一家顺路的，保留15:00预约。");
    await mobile.locator("#sendBtn").click();
    await mobile.getByRole("button",{name:"查看修改清单"}).click();
    assert.match(await mobile.locator("#editView").innerText(), /上海博物馆/);
    await mobile.getByRole("button",{name:"一起试排"}).click();
    await mobile.waitForFunction(() =>
      document.querySelector("#editView h3")?.textContent !== "组合修改", {timeout:65000});
    assert.match(await mobile.locator("#editView").innerText(), /修改预览已通过核验/);
    report.phone.preview = await mobile.locator(".edit-changes").innerText();
    assert.match(report.phone.preview, /12:05/);
    assert.match(report.phone.preview, /更换地点/);
    await mobile.screenshot({path:path.join(path.dirname(output),"phone.png")});
    const width = await mobile.locator("#modalBox").evaluate(node => ({scroll:node.scrollWidth,client:node.clientWidth}));
    assert.ok(width.scroll <= width.client + 1, JSON.stringify(width));
    await mobile.getByRole("button",{name:"采用并保存"}).click();
    await mobile.waitForURL(url => url.searchParams.has("revision"));
    const saved = new URL(mobile.url()).searchParams.get("revision");
    for (let i = 0; i < 2; i++) {
      const state = await (await phone.request.get(endpoint("state", {id:saved}))).json();
      assert.equal(state.state, "complete");
      const plan = state.group.versions[0];
      assert.ok(plan.revision.adopted);
      const entries = plan.plan.itinerary[2].activities;
      assert.equal(entries[1]._lock, "reservation");
      assert.equal(entries[1].start_time, "15:00");
      assert.equal(entries[0].end_time, "13:05");
      assert.notEqual(entries[2].position, "沪西老弄堂面馆(广东路店)");
      await mobile.reload({waitUntil:"domcontentloaded"});
      await mobile.waitForURL(url => url.searchParams.get("revision") === saved && url.searchParams.get("day") === "3");
    }
    report.phone.reopened = saved;
    const conflicted = await settle(desktop.request, {...batch,operations:[
      batch.operations[0], {action:"time",activity:id(2),start:"16:00",end:"17:00"},
    ]});
    assert.equal(conflicted.state,"edit_conflict", JSON.stringify(conflicted));
    const adopt = await desktop.request.post(endpoint("adopt"), {data:{id:conflicted.id}});
    assert.equal(adopt.status(),409);
    report.endpoint.lockedConflict = conflicted.message;
    const tooShort = await settle(desktop.request, {...batch, operations:[
      {action:"time", activity:id(1), start:"11:53", end:"12:42"}, batch.operations[1],
    ]});
    assert.equal(tooShort.state, "edit_conflict", JSON.stringify(tooShort));
    assert.match(tooShort.message, /午晚餐|一小时/);
    report.endpoint.shortMealConflict = tooShort.message;
    assert.deepEqual(report.errors, []);
  } finally {
    fs.writeFileSync(output, JSON.stringify(report,null,2));
    await browser.close();
  }
}
run().catch(error => {console.error(error);process.exitCode = 1;});
