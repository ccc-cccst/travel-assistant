const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {chromium} = require(process.env.SHORTTRIP_PLAYWRIGHT || "playwright");
const root = process.env.SHORTTRIP_WORKBENCH_URL || "http://127.0.0.1:8795";
const output = process.argv[2];
const report = {pageErrors:[], writes:[], sessions:[], tables:{}, layouts:[]};
const savedKey = "shorttrip.study.comparison.v1";
const pairs = {
  chengdu:{choice:"alternative", benefit:"总价少 414.43 元", tradeoff:"接驳多 44 分钟"},
  beijing:{choice:"reference", benefit:"总价少 445.62 元", tradeoff:"接驳多 106 分钟"},
};
const assignments = [
  [["chengdu","table"],["beijing","summary"]],
  [["chengdu","summary"],["beijing","table"]],
  [["beijing","table"],["chengdu","summary"]],
  [["beijing","summary"],["chengdu","table"]],
];

async function openStudy(page, participant) {
  await page.goto(root + "/study.html", {waitUntil:"domcontentloaded"});
  await page.getByLabel("参与者编号").fill(participant);
  await page.getByRole("button",{name:"开始或继续测试"}).click();
}

async function task(page, scenario, display) {
  await page.getByRole("button",{name:"打开方案比较"}).click();
  const frame = page.frameLocator("#comparisonFrame iframe");
  await page.locator("#decision").waitFor({timeout:20000});
  await frame.locator("#archiveDelta .version-table").waitFor({state:"attached"});
  const data = await frame.locator("#archiveDelta .version-table").textContent();
  if (report.tables[scenario]) assert.equal(data,report.tables[scenario]);
  else report.tables[scenario] = data;
  assert.equal(await frame.locator("#archiveTakeaways .decision-summary").count(),display === "summary" ? 1 : 0);
  assert.equal(await frame.locator("#archiveDelta").isVisible(),display === "table");
  assert.equal(await frame.locator("#archiveCompare option").count(),1);
  const iframeUrl = new URL(await page.locator("#comparisonFrame iframe").getAttribute("src"));
  assert.equal(iframeUrl.searchParams.get("comparison_case"),scenario);
  assert.equal(iframeUrl.searchParams.get("comparison_display"),display);
  if (scenario === "chengdu") {
    const width = await page.evaluate(() => ({viewport:innerWidth, content:document.documentElement.scrollWidth}));
    assert.equal(width.viewport,width.content);
    report.layouts.push({display, width:width.viewport, content:width.content});
    await frame.locator("#modalBox").screenshot({path:path.join(path.dirname(output),display + "-" + width.viewport + ".png")});
  }
  const answer = pairs[scenario];
  await page.locator("input[name=choice][value=" + answer.choice + "]").check();
  await page.getByLabel("备选的主要好处").fill(answer.benefit);
  await page.getByLabel("备选的主要代价").fill(answer.tradeoff);
  await page.getByRole("button",{name:"记录选择"}).click();
}

async function run() {
  const browser = await chromium.launch({channel:"chrome",headless:true});
  try {
    const context = await browser.newContext({viewport:{width:1000,height:850},acceptDownloads:true,reducedMotion:"reduce"});
    await context.route("**/*", route => {
      const req = route.request();
      if (new URL(req.url()).origin !== root) return route.abort();
      if (req.method() !== "GET") { report.writes.push(new URL(req.url()).pathname); return route.abort(); }
      return route.continue();
    });
    const page = await context.newPage();
    page.on("pageerror", error => report.pageErrors.push(error.message));
    for (const [index, order] of assignments.entries()) {
      if (index === 2) await page.setViewportSize({width:390,height:844});
      const participant = "P0" + (index + 1);
      await openStudy(page, participant);
      await task(page, ...order[0]);
      if (index === 0) {
        await openStudy(page, participant);
        assert.match(await page.locator("#taskTitle").innerText(),/第 2 题/);
        report.resume = true;
      }
      await task(page, ...order[1]);
      await page.getByRole("heading",{name:"两道题已记录"}).waitFor();
      const rows = await page.evaluate(key => JSON.parse(localStorage.getItem(key)),savedKey);
      const run = rows.filter(row => row.participant === participant);
      assert.equal(run.length,2);
      for (const [position,row] of run.entries()) {
        assert.deepEqual([row.scenario,row.display],order[position]);
        assert.ok(row.correctChoice && row.loadMilliseconds >= 0 &&
          row.firstChoiceMilliseconds >= 0 && row.decisionMilliseconds >= row.firstChoiceMilliseconds);
        assert.equal(row.correctBenefit,null);
      }
      report.sessions.push({participant, order, records:run.length});
    }
    await page.locator("#facilitator > summary").click();
    const answer = page.locator("#recordList article").first();
    await answer.getByLabel("好处",{exact:true}).selectOption("yes");
    await answer.getByLabel("代价",{exact:true}).selectOption("no");
    const exportEvent = page.waitForEvent("download");
    await page.getByRole("button",{name:"导出记录"}).click();
    const download = await exportEvent;
    const exportPath = path.join(path.dirname(output),"browser-export.json");
    await download.saveAs(exportPath);
    const exported = JSON.parse(fs.readFileSync(exportPath,"utf8"));
    assert.equal(exported.records.length,8);
    assert.equal(exported.records[0].correctBenefit,true);
    assert.equal(exported.records[0].correctTradeoff,false);
    assert.equal(await page.evaluate(() => localStorage.getItem("st.ui.planningJobs")),null);
    report.exported = exported.records.length;
    report.equalData = true;
    await openStudy(page,"P05");
    await page.getByRole("button",{name:"打开方案比较"}).click();
    await page.locator("#decision").waitFor({timeout:20000});
    await page.getByRole("button",{name:"记录为未完成"}).click();
    const interrupted = await page.evaluate(key => JSON.parse(localStorage.getItem(key)).at(-1),savedKey);
    assert.equal(interrupted.status,"unfinished");
    assert.equal(interrupted.correctChoice,null);
    await openStudy(page,"P05");
    assert.match(await page.locator("#taskTitle").innerText(),/第 2 题/);
    report.unfinishedRetained = true;
    await page.goto(root + "/?job=7cfccaa570c744818c257c37a4256826&candidate=6e08e84d0129a23b651bd0bc",
      {waitUntil:"domcontentloaded"});
    await page.getByRole("button",{name:"比较方案",exact:true}).click();
    await page.locator("#archiveTakeaways .decision-summary").waitFor();
    assert.equal(await page.locator("#openCompared").isVisible(),true);
    report.normalComparison = true;
    assert.deepEqual(report.pageErrors,[]);
    assert.deepEqual(report.writes,[]);
  } finally {
    fs.writeFileSync(output,JSON.stringify(report,null,2));
    await browser.close();
  }
}
run().catch(error => {console.error(error);process.exitCode = 1;});
