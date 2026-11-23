const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {chromium} = require(process.env.SHORTTRIP_PLAYWRIGHT || "playwright");
const root = process.env.SHORTTRIP_WORKBENCH_URL || "http://127.0.0.1:8795";
const out = process.argv[2];
const report = {preview:{}, adoption:{}, phone:[], errors:[]};
const base = {job:"01b135fda6c446b38440ab8716b0f4c2",
  original:"4c98df9bbd58c313bcc80b88", candidate:"943d6f07725c3c87313e8f57", parent:"55350b07bb5546b1a8fbe374c6af0bc5"};
const edits = [
  {action:"time", activity:base.candidate + "-d3-a2", start:"12:57", end:"13:27"},
  {action:"meal_options", activity:base.candidate + "-d3-a4", cuisine:"当地特色菜"},
];
const rootTrip = root + "/?" + new URLSearchParams({job:base.job,candidate:base.original});
const editApi = (op, query) => root + "/api/itinerary-edits/" + op + (query ? "?" + new URLSearchParams(query) : "");
async function preview(request, period) {
  const result = await request.post(editApi("preview"),{data:{...base,action:"batch",day:3,
    schema_version:1,operations:edits,pace:"lighter",pace_period:period}});
  const started = await result.json();
  assert.equal(result.status(),202,JSON.stringify(started));
  for (let i = 0; i < 125; i++) {
    const response = await (await request.get(editApi("state",{id:started.id}))).json();
    if (response.state !== "planning" && response.state !== "starting") return response;
    await new Promise(resolve => setTimeout(resolve,400));
  }
  throw new Error("编辑预览未完成");
}

async function run() {
  const browser = await chromium.launch({channel:"chrome",headless:true});
  try {
    const context = await browser.newContext({viewport:{width:390,height:844},hasTouch:true,reducedMotion:"reduce"});
    const request = context.request;
    const arranged = await preview(request,"afternoon");
    assert.equal(arranged.state,"complete",JSON.stringify(arranged));
    const version = arranged.group.versions[0];
    const day = version.plan.itinerary[2].activities;
    assert.equal(day[1].end_time,"13:27");
    assert.equal(day[2]._lock,"reservation");
    assert.equal(day[2].start_time,"15:00");
    assert.match(day[3]._info["菜系"],/本帮菜/);
    assert.notEqual(day[3].position,"沪西老弄堂面馆(广东路店)");
    assert.ok(arranged.change.pace.after < arranged.change.pace.before);
    report.preview = {changes:arranged.change.changes,pace:arranged.change.pace,meal:day[3].position,
      reservation:day[2].start_time,mealCuisine:day[3]._info["菜系"]};

    const wrongScope = await preview(request,"morning");
    assert.equal(wrongScope.state,"edit_conflict",JSON.stringify(wrongScope));
    assert.match(wrongScope.message,/上午/);
    const failedAdoption = await request.post(editApi("adopt"),{data:{id:wrongScope.id}});
    assert.equal(failedAdoption.status(),409);
    report.rejected = wrongScope.message;

    const apply = () => request.post(editApi("adopt"),{data:{id:arranged.id}});
    const responses = await Promise.all([apply(),apply()]);
    for (const response of responses) {
      assert.equal(response.status(),200);
      assert.equal((await response.json()).group.versions[0].revision.id,arranged.id);
    }
    const history = await (await request.get(editApi("history",{job:base.job, original:base.original}))).json();
    assert.equal(history.filter(item => item.id === arranged.id).length,1);
    const stale = await request.post(editApi("preview"),{data:{...base,candidate:base.original,
      action:"batch",day:3,schema_version:1,operations:edits}});
    assert.equal(stale.status(),409);
    report.adoption = {revision:arranged.id,doubleSubmissionCount:1,staleCandidate:stale.status()};

    const page = await context.newPage();
    page.on("pageerror", error => report.errors.push(error.message));
    await page.goto(rootTrip + "&revision=" + arranged.id + "&day=3",{waitUntil:"domcontentloaded"});
    await page.locator(".current-revision").waitFor();
    assert.match(await page.locator("#sec-day-3").innerText(),/13:27/);

    const longTrip = root + "/?job=0f69e8f2045145df876c1b681e40c504&candidate=2588d3ad9de691e60b7ffe90" +
      "&revision=9d9d3264a38c4127bf644a65d4f35cd7&day=7";
    for (const width of [360,390,430]) {
      await page.setViewportSize({width,height:844});
      await page.goto(longTrip,{waitUntil:"domcontentloaded"});
      const picker = page.locator("#dayJumpSelect");
      await picker.waitFor();
      await page.waitForFunction(() => document.querySelector("#dayJumpSelect").value === "day-7");
      assert.equal(await picker.locator("option").count(),8);
      assert.equal(await page.locator("#dayChips").isVisible(),false);
      await picker.selectOption("day-4");
      await page.waitForURL(url => url.searchParams.get("day") === "4");
      await page.locator("#mapFab").click();
      await page.locator("#mapPane").waitFor();
      await page.locator("#mapFab").click();
      await picker.waitFor();
      assert.equal(await picker.inputValue(),"day-4");
      const size = await page.evaluate(() => [document.documentElement.scrollWidth,innerWidth]);
      assert.equal(size[0],size[1]);
      report.phone.push({width, days:7, restored:"day-7", switched:"day-4", returnedFromMap:true});
    }
    await page.screenshot({path:path.join(path.dirname(out),"phone-430.png")});
    await page.setViewportSize({width:844,height:430});
    await page.locator("#dayChips").waitFor();
    assert.equal(await page.locator("#dayChips .on").getAttribute("data-sec"),"day-4");
    assert.deepEqual(report.errors,[]);
  } finally {
    fs.writeFileSync(out,JSON.stringify(report,null,2));
    await browser.close();
  }
}
run().catch(error => {console.error(error);process.exitCode = 1;});
