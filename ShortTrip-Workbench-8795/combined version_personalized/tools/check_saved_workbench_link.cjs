const assert = require("node:assert/strict");
const fs = require("node:fs");
const {chromium} = require(process.env.SHORTTRIP_PLAYWRIGHT || "playwright");
const root = process.env.SHORTTRIP_WORKBENCH_URL || "http://127.0.0.1:8795";
const job = "01b135fda6c446b38440ab8716b0f4c2";
const candidate = "4c98df9bbd58c313bcc80b88";
const revision = "1ddf6ebe388942b3898f9e4ee963c0d9";
const start = root + "/?" + new URLSearchParams({job,candidate,revision,day:3});
const report = {errors:[]};

async function run() {
  const browser = await chromium.launch({headless:true,channel:"chrome"});
  try {
    const context = await browser.newContext({viewport:{width:390,height:844},hasTouch:true,reducedMotion:"reduce"});
    const page = await context.newPage();
    page.on("pageerror", error => report.errors.push(error.message));
    const states = "**/api/itinerary-edits/state?id=" + revision;
    let release;
    const held = new Promise(resolve => { release = resolve; });
    let seen;
    const requested = new Promise(resolve => { seen = resolve; });
    await page.route(states, async route => {
      seen();
      await held;
      return route.continue();
    });
    await page.goto(start,{waitUntil:"domcontentloaded"});
    await requested;
    report.pendingRevision = new URL(page.url()).searchParams.get("revision");
    release();
    await page.locator(".current-revision").waitFor();
    assert.match(await page.locator("#sec-day-3").innerText(), /三玛璐酒楼/);
    await page.goto(root,{waitUntil:"domcontentloaded"});
    await page.locator("#menuBtn").click();
    const link = page.locator(".recent-plan").filter({has:page.getByText("已修改",{exact:false})}).first();
    report.drawerHasRevision = await link.count() === 1;
    if (report.drawerHasRevision) {
      const href = new URL(await link.getAttribute("href"),root);
      report.drawerRevision = href.searchParams.get("revision");
      report.drawerDay = href.searchParams.get("day");
      await link.click();
      await page.locator(".current-revision").waitFor();
      assert.match(await page.locator("#sec-day-3").innerText(), /三玛璐酒楼/);
      assert.match(await page.locator("#sec-day-3").innerText(), /15:00/);
    }
    assert.equal(report.pendingRevision,revision);
    assert.equal(report.drawerRevision,revision);
    assert.equal(report.drawerDay,"3");
    assert.deepEqual(report.errors,[]);
  } finally {
    fs.writeFileSync(process.argv[2],JSON.stringify(report,null,2));
    await browser.close();
  }
}
run().catch(error => {console.error(error);process.exitCode = 1;});
