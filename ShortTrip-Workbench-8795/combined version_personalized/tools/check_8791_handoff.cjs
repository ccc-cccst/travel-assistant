const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {chromium} = require(process.env.SHORTTRIP_PLAYWRIGHT || "playwright");
const output = process.argv[2];
const stage = process.argv[3];
const primary = "http://127.0.0.1:8795";
const shanghai = "01b135fda6c446b38440ab8716b0f4c2";
const shanghaiRequest = "8514da04947849c9abaaa057b619e8e6";
const original = "4c98df9bbd58c313bcc80b88";
const revision = "f8678c9a77524ebeb46a0cd4b1dfa736";
const suzhou = "82cf14eb022143728d7c093b8539ecab";
const suzhouRequest = "6417f4f1fe864e9a9e29d4d41b2651bd";
const shanghaiUrl = `/?job=${shanghai}&candidate=${original}&day=3`;
const restaurant = {id:"restaurant:测试餐馆",city:"上海",kind:"restaurant",name:"测试餐馆",pos:[31.23,121.47]};
const otherBranch = {...restaurant,pos:[31.25,121.49]};
const booking = "st.booking:" + JSON.stringify(["personalized-" + shanghai,"上海","上海博物馆","attraction",null,
  "2026-11-23",null,"15:00","16:30",2,null,null]);
const report = {stage:!!stage, errors:[], writes:[]};
const candidate = fs.existsSync(stage || "") ? stage : null;
const stageFiles = candidate ? new Set(fs.readdirSync(candidate)) : new Set();

async function context(browser, viewport) {
  const ctx = await browser.newContext({viewport, isMobile:viewport.width < 700, hasTouch:viewport.width < 700});
  await ctx.route("**/*", route => {
    const req = route.request();
    const url = new URL(req.url());
    if (url.hostname !== "127.0.0.1") return route.abort();
    if (req.method() !== "GET") {
      report.writes.push(url.pathname);
      return route.abort();
    }
    const file = url.pathname === "/" ? "index.html" : url.pathname.slice(1);
    if (stageFiles.has(file) && (url.origin === primary || url.port === "8791")) {
      return route.fulfill({path:path.join(candidate,file),
        contentType:file.endsWith(".js") ? "application/javascript; charset=utf-8" :
          file.endsWith(".css") ? "text/css; charset=utf-8" : "text/html; charset=utf-8"});
    }
    if (url.pathname === "/api/route") return route.fulfill({json:{unavailable:true}});
    if (url.pathname === "/api/routes") return route.fulfill({json:[]});
    if (url.pathname === "/api/day-facts/weather") return route.fulfill({json:{status:"outside_forecast",days:[]}});
    return route.continue();
  });
  ctx.on("page", page => page.on("pageerror", error => report.errors.push(error.message)));
  return ctx;
}

async function migrate(page, port = "8791") {
  if (!await page.locator("#drawer").isVisible()) await page.locator("#menuBtn").click();
  const panel = page.locator("#legacyHistory");
  if (!await panel.evaluate(el => el.open)) await panel.locator("summary").click();
  const [popup] = await Promise.all([page.waitForEvent("popup"),panel.getByRole("button",{name:"从 " + port + " 迁入"}).click()]);
  await popup.getByRole("button",{name:"复制到 8795"}).click();
  await page.getByRole("heading",{name:"预览从 " + port + " 迁入"}).waitFor();
  return page.locator("#modalBox");
}

async function main() {
  const browser = await chromium.launch({channel:"chrome",headless:true});
  try {
    const desktop = await context(browser,{width:1440,height:900});
    const page = await desktop.newPage();
    await page.goto("http://127.0.0.1:8791" + shanghaiUrl + "&revision=" + revision + "&api=http://example.invalid");
    const link = page.getByRole("link",{name:"在统一版继续"});
    const target = new URL(await link.getAttribute("href"));
    assert.equal(target.origin,primary);
    assert.deepEqual(Object.fromEntries(target.searchParams),{job:shanghai,candidate:original,revision,day:"3"});
    await link.click();
    await page.locator(".current-revision").getByText("已采用版本",{exact:true}).waitFor();
    const day = page.locator("#sec-day-3");
    assert.match(await day.innerText(),/上海自然博物馆/);
    assert.match(await day.innerText(),/15:00/);
    await page.locator("#shareBtn").click();
    assert.equal(new URL(await page.locator("#shareLocalLink").inputValue()).searchParams.get("revision"),revision);
    await page.locator("#modalBox").getByRole("button",{name:"关闭",exact:true}).click();
    report.handoff = {revision, day:3, lockedAppointment:"15:00", share:true};
    await page.goto(primary + shanghaiUrl);
    await page.locator("#sec-day-3 .it").first().waitFor();
    assert.doesNotMatch(await day.innerText(),/上海自然博物馆/);
    await page.goto(primary + shanghaiUrl.replace(original,"0".repeat(24)));
    await page.locator("#archiveError").waitFor();
    assert.match(await page.locator("#archiveError").innerText(),/候选已找不到/);
    await page.goto(primary + shanghaiUrl + "&revision=" + "0".repeat(32));
    await page.locator("#archiveError").waitFor();
    assert.match(await page.locator("#archiveError").innerText(),/当前显示原方案/);
    report.invalidLinks = true;

    await page.goto("http://127.0.0.1:8791/transfer-shorttrip.html");
    await page.evaluate(({restaurant,otherBranch,booking,shanghai,shanghaiRequest,original,revision}) => {
      localStorage.setItem("st.favs", JSON.stringify([restaurant,otherBranch]));
      localStorage.setItem("st.booking:malformed","yes");
      localStorage.setItem(booking,"yes");
      localStorage.setItem("st.ui.planningJobs",JSON.stringify([
        {job:shanghai,request:shanghaiRequest,title:"上海已采用修订",candidate:original,revision,day:3}
      ]));
      localStorage.setItem("st.edits",JSON.stringify({draft:{title:"旧草稿甲"}}));
      localStorage.setItem("st.created",JSON.stringify([{id:"draft",title:"旧草稿乙"}]));
      localStorage.setItem("st.ui.chat",JSON.stringify({["personalized-" + shanghai]:[{role:"user",text:"测试旧对话"}]}));
    },{restaurant,otherBranch,booking,shanghai,shanghaiRequest,original,revision});
    await page.goto(primary + shanghaiUrl);
    await page.locator("#sec-day-3 .it").first().waitFor();
    await page.evaluate(({restaurant,shanghai,shanghaiRequest,original}) => {
      localStorage.setItem("st.favs",JSON.stringify([restaurant]));
      localStorage.setItem("st.ui.planningJobs",JSON.stringify([
        {job:shanghai,request:shanghaiRequest,title:"当前上海",candidate:original,day:3}
      ]));
    },{restaurant,shanghai,shanghaiRequest,original});
    await page.reload();
    const snapshot = () => page.evaluate(booking => ({
      favorite:JSON.parse(localStorage.getItem("st.favs") || "[]"),
      job:JSON.parse(localStorage.getItem("st.ui.planningJobs") || "[]"),
      links:JSON.parse(localStorage.getItem("st.ui.importedLinks") || "[]"),
      copies:JSON.parse(localStorage.getItem("st.ui.importedCopies") || "[]"),
      booking:localStorage.getItem(booking), malformed:localStorage.getItem("st.booking:malformed"),
      batch:localStorage.getItem("st.ui.historyImportBatch"),
    }),booking);
    const before = await snapshot();
    let modal = await migrate(page);
    assert.match(await modal.innerText(),/新增 1 个收藏、0 条最近行程、1 条旧版本链接、2 份草稿/);
    await modal.getByRole("button",{name:"取消",exact:true}).click();
    assert.deepEqual(await snapshot(),before);

    modal = await migrate(page);
    await modal.getByRole("checkbox",{name:/上海博物馆/}).check();
    await modal.getByRole("checkbox",{name:/旧对话/}).check();
    await page.evaluate(() => {
      const original = Storage.prototype.setItem;
      Storage.prototype.setItem = function (key,value) {
        if (key === "st.ui.importedCopies") {
          Storage.prototype.setItem = original;
          throw new DOMException("simulated quota","QuotaExceededError");
        }
        return original.call(this,key,value);
      };
    });
    await modal.getByRole("button",{name:"确认迁入"}).click();
    await modal.getByText(/迁入未完成/).waitFor();
    assert.deepEqual(await snapshot(),before);
    await modal.getByRole("button",{name:"确认迁入"}).click();
    await page.getByText("已迁入 6 项旧版浏览器记录。",{exact:true}).waitFor();
    const imported = await snapshot();
    assert.equal(imported.favorite.length,2);
    assert.deepEqual(imported.job,before.job);
    assert.equal(imported.links[0].revision,revision);
    assert.equal(imported.copies.length,3);
    assert.equal(imported.booking,"yes");
    assert.equal(imported.malformed,null);
    await page.reload();
    modal = await migrate(page);
    assert.match(await modal.innerText(),/新增 0 个收藏、0 条最近行程、0 条旧版本链接、0 份草稿/);
    await modal.getByRole("button",{name:"确认迁入"}).click();
    await page.getByText("没有新增记录。",{exact:true}).waitFor();
    assert.equal((await snapshot()).batch, imported.batch);
    await page.getByRole("button",{name:"查看旧版草稿与对话"}).click();
    await page.getByRole("heading",{name:"旧版草稿与对话"}).waitFor();
    const [download] = await Promise.all([page.waitForEvent("download"),page.getByRole("button",{name:/旧草稿甲/}).click()]);
    assert.equal(JSON.parse(fs.readFileSync(await download.path(),"utf8")).value.title,"旧草稿甲");
    await page.locator("#modalBox").getByRole("button",{name:"关闭",exact:true}).click();
    const alternate = page.locator("#drawerList").getByRole("link",{name:/上海已采用修订/});
    assert.match(await alternate.getAttribute("href"),new RegExp(revision));
    await alternate.click();
    await page.locator(".current-revision").getByText("已采用版本",{exact:true}).waitFor();
    await page.evaluate(booking => {
      const saved = JSON.parse(localStorage.getItem("st.favs"));
      saved[1].note = "迁入后改动";
      localStorage.setItem("st.favs",JSON.stringify(saved));
      document.dispatchEvent(new CustomEvent("shorttrip-booking-changed",{detail:booking}));
    },booking);
    await page.locator("#menuBtn").click();
    await page.locator("#legacyHistory summary").click();
    await page.getByRole("button",{name:"撤销最近一次迁入"}).click();
    await page.getByRole("button",{name:"撤销迁入",exact:true}).click();
    await page.getByText("已撤销最近一次迁入。",{exact:true}).waitFor();
    const afterUndo = await snapshot();
    assert.equal(afterUndo.favorite[1].note,"迁入后改动");
    assert.equal(afterUndo.booking,"yes");
    assert.equal(afterUndo.links.length,0);
    assert.equal(afterUndo.copies.length,0);
    assert.equal(afterUndo.batch,null);
    report.migration = {cancel:true, rollbackAfterQuota:true, retry:true, duplicateZero:true, differentBranches:true,
      currentJobPreserved:true, alternativeOpened:true, bookingOptIn:true, draftDownload:true, editedRecordsSurviveUndo:true};
    const protectedTrips = [
      {job:"c65c872e0b0e4120b8570a80e08e96a4", candidate:"597355ee4d2364e93db90af5", day:1},
      {job:"0f69e8f2045145df876c1b681e40c504", candidate:"2588d3ad9de691e60b7ffe90",
        revision:"9d9d3264a38c4127bf644a65d4f35cd7", day:7},
    ];
    for (const target of protectedTrips) {
      await page.goto(primary + "/?" + new URLSearchParams(target));
      await page.locator(`#sec-day-${target.day} .it`).first().waitFor();
      if (target.revision) await page.locator(".current-revision").getByText("已采用版本",{exact:true}).waitFor();
      const url = new URL(page.url());
      assert.equal(url.searchParams.get("candidate"),target.candidate);
      assert.equal(url.searchParams.get("day"),String(target.day));
    }
    report.protectedTrips = protectedTrips.length;
    await desktop.close();

    const phone = await context(browser,{width:390,height:844});
    const mobile = await phone.newPage();
    for (const port of ["8797","8805"]) {
      await mobile.goto("http://127.0.0.1:" + port + "/transfer-shorttrip.html");
      await mobile.evaluate(({port,suzhou,suzhouRequest}) => {
        localStorage.setItem("st.favs",JSON.stringify([{city:"苏州",name:"测试 " + port,pos:[31.3,120.6]}]));
        localStorage.setItem("st.ui.planningJobs",JSON.stringify([{job:suzhou,request:suzhouRequest,title:"苏州五日游",day:5}]));
      },{port,suzhou,suzhouRequest});
      await mobile.goto(primary + "/?new=1");
      await mobile.locator("#planningRequest").waitFor();
      const preview = await migrate(mobile,port);
      await preview.getByRole("button",{name:"确认迁入"}).click();
      await mobile.getByText(/已迁入 \d 项旧版浏览器记录/).waitFor();
      report["legacy" + port] = true;
    }
    await mobile.goto(primary + "/?new=1");
    const input = mobile.locator("#planningRequest");
    await input.waitFor();
    assert.equal(await input.locator("[name=model_provider] option:enabled").count(),3);
    for (const provider of ["deepseek","qwen","program"]) {
      await input.locator("[name=model_provider]").selectOption(provider);
      assert.equal(await input.locator("[name=raw_text]").isVisible(),provider !== "program");
    }
    assert.equal(await input.locator("[name=start_date]").getAttribute("required"),"");
    await mobile.goto(primary + "/?job=180b91ef3acc4560a10a97f9b04821a9");
    await mobile.locator("#planBody .it").first().waitFor();
    await mobile.locator("#mapFab").click();
    assert.match(await mobile.locator("#layout").getAttribute("class"),/show-map/);
    await mobile.locator("#mapFab").click();
    report.mobile = {width:390, providerChoices:3, departureRequired:true, maps:true};
    assert.deepEqual(report.errors,[]);
    assert.deepEqual(report.writes,[]);
  } finally { await browser.close(); }
}
main().catch(error => {
  report.failure = error.stack;
  console.error(error);
  process.exitCode = 1;
}).finally(() => {
  if (output) fs.writeFileSync(output,JSON.stringify(report,null,2),{flag:"wx"});
  console.log(JSON.stringify(report));
});
