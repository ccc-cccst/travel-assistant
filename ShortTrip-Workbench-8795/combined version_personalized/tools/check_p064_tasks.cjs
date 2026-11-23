// Automated dry run of the four participant tasks. The edit itinerary is a project QA fixture.
const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs");
const {chromium} = require(process.env.SHORTTRIP_PLAYWRIGHT || "playwright");
const root = process.env.SHORTTRIP_WORKBENCH_URL || "http://127.0.0.1:8795";
const compare = "/?job=7cfccaa570c744818c257c37a4256826&candidate=6e08e84d0129a23b651bd0bc";
const original = "4c98df9bbd58c313bcc80b88";
const sample = "/?job=01b135fda6c446b38440ab8716b0f4c2&candidate=" + original +
  "&revision=55350b07bb5546b1a8fbe374c6af0bc5&day=3";
const report = {tasks:{}, browserErrors:[]};
const output = process.argv[2];
const capture = (page, name) => page.screenshot({path:path.join(path.dirname(output),name + ".png")});
const elapsed = start => Math.round(performance.now() - start);

async function run() {
  const browser = await chromium.launch({headless:true,channel:"chrome"});
  try {
    const context = await browser.newContext({viewport:{width:390,height:844},hasTouch:true,reducedMotion:"reduce"});
    const page = await context.newPage();
    page.on("pageerror", error => report.browserErrors.push(error.message));
    await page.goto(root + compare,{waitUntil:"domcontentloaded"});
    await page.locator("#archiveChoice").waitFor();
    let start = performance.now();
    await page.getByRole("button",{name:"比较方案",exact:true}).click();
    const comparison = await page.locator("#archiveTakeaways").innerText();
    assert.match(comparison,/414\.43/);
    assert.match(comparison,/接驳多 44 分钟/);
    await capture(page,"comparison");
    await page.getByRole("button",{name:"查看对比方案"}).click();
    assert.equal(new URL(page.url()).searchParams.get("candidate"),"96f8ca3a97f77dc5e924c802");
    report.tasks.compare = {milliseconds:elapsed(start), comparison};

    start = performance.now();
    await page.getByRole("button",{name:"出发前核对",exact:true}).click();
    const review = page.locator("#modalBox");
    assert.match(await review.locator(".fact-meaning").innerText(),/参考价/);
    assert.match(await review.getByText("出行日的余票和余房",{exact:true}).innerText(),/余房/);
    assert.match(await review.getByText("出行日的票价、房价与开放时间",{exact:true}).innerText(),/开放时间/);
    await capture(page,"review");
    await review.evaluate(node => { node.scrollTop = node.scrollHeight; });
    const closeReview = review.getByRole("button",{name:"关闭出发前核对",exact:true});
    const closeBounds = await closeReview.boundingBox();
    assert.ok(closeBounds && closeBounds.y >= 0 && closeBounds.y + closeBounds.height < 844);
    await closeReview.click();
    report.tasks.factReview = {milliseconds:elapsed(start), items:2};

    await page.goto(root + sample,{waitUntil:"domcontentloaded"});
    await page.locator(".current-revision").first().waitFor();
    await page.getByRole("button",{name:"AI助手",exact:true}).click();
    await page.locator("#askInput").fill("第3天下午轻松一点，把上海自然博物馆的游览从12:57–13:42改为12:57–13:27，晚饭换附近的本帮菜，保留15:00上海博物馆预约。");
    start = performance.now();
    const assist = page.waitForResponse(r => r.request().method() === "POST" && r.url().endsWith("/itinerary-edits/assist"),{timeout:60000});
    await page.locator("#sendBtn").click();
    const understood = await (await assist).json();
    report.tasks.edit = {understandingMilliseconds:elapsed(start), interpretation:understood};
    assert.equal(understood.state,"proposal",JSON.stringify(understood));
    assert.equal(understood.proposal.pace_period,"afternoon");
    await page.getByRole("button",{name:"查看修改清单"}).click();
    assert.match(await review.innerText(),/15:00/);
    assert.match(await review.innerText(),/本帮菜/);
    start = performance.now();
    await review.getByRole("button",{name:"一起试排"}).click();
    await page.getByRole("heading",{name:"修改预览已通过核验"}).waitFor({timeout:60000});
    report.tasks.edit.previewMilliseconds = elapsed(start);
    report.tasks.edit.preview = await review.innerText();
    assert.match(report.tasks.edit.preview,/游览和接驳合计少\s*15 分钟/);
    assert.match(report.tasks.edit.preview,/上海博物馆 15:00 保留/);
    await capture(page,"edit");
    await review.getByRole("button",{name:"采用并保存"}).click();
    await page.waitForURL(url => {
      const id = url.searchParams.get("revision");
      return id && id !== "55350b07bb5546b1a8fbe374c6af0bc5";
    });
    const revision = new URL(page.url()).searchParams.get("revision");
    report.tasks.edit.revision = revision;

    start = performance.now();
    await page.goto(root,{waitUntil:"domcontentloaded"});
    await page.locator("#menuBtn").click();
    const current = page.locator("#drawerList .recent-plan").filter({has:page.getByText("已修改",{exact:false})}).first();
    await current.waitFor();
    const href = new URL(await current.getAttribute("href"),root);
    assert.equal(href.searchParams.get("revision"),revision);
    await current.click();
    await page.locator(".current-revision").waitFor();
    const day = await page.locator("#sec-day-3").innerText();
    assert.match(day,/三玛璐酒楼/);
    assert.match(day,/15:00/);
    report.tasks.reopen = {milliseconds:elapsed(start),revision,day:3,appointment:"15:00"};
    assert.deepEqual(report.browserErrors,[]);
  } finally {
    fs.writeFileSync(output,JSON.stringify(report,null,2));
    await browser.close();
  }
}
run().catch(error => {console.error(error);process.exitCode = 1;});
