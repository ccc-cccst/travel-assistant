// Set SHORTTRIP_PLAYWRIGHT when using playwright-core.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const {chromium} = require(process.env.SHORTTRIP_PLAYWRIGHT || "playwright");
const base = process.env.SHORTTRIP_WORKBENCH_URL || "http://127.0.0.1:8795";
const trip = "?job=01b135fda6c446b38440ab8716b0f4c2&candidate=4c98df9bbd58c313bcc80b88&day=3";
const rail = "?job=324279423e6d4db09aeaa60624b84dc6&candidate=7d2e9f0e14ae90ba92f7542f&day=1";
const live = process.argv.includes("--live");
const fromTimeline = process.argv.includes("--day-footer");
const report = {routeSource:live ? "amap_live" : "controlled_route_responses", errors:[]};

async function popVisit(page, name) {
  const filter = page.locator('[data-f="attraction"]');
  await filter.waitFor();
  if (!await page.locator('.mf.on [data-f="attraction"]').count()) await filter.click();
  await page.waitForFunction(() => document.querySelector("#poiStatus").textContent.includes("个地点"));
  const marker = page.locator(".rec .lb").getByText(name,{exact:true});
  if (await marker.count()) await marker.first().evaluate(el => el.click());
  else {
    for (const cluster of await page.locator(".gcluster").all()) {
      await cluster.evaluate(el => el.click());
      const row = page.locator(".plist .row").getByText(name,{exact:true});
      if (await row.count()) { await row.click(); break; }
    }
  }
  await page.getByRole("button",{name:"试加到行程"}).click({timeout:8000});
}

async function preview(page, click) {
  const created = page.waitForResponse(r => r.url().endsWith("/itinerary-edits/preview"));
  await click();
  const job = await (await created).json();
  assert.ok(job.id, job.message);
  const state = await page.waitForResponse(async r => r.url().endsWith("/state?id=" + job.id)
    && !["planning","starting"].includes((await r.json()).state), {timeout:75000});
  return state.json();
}

async function addFromTimeline(page, day, name) {
  const section = page.locator("#sec-day-" + day);
  await section.getByRole("button",{name:"添加地点",exact:true}).click();
  const form = page.locator("#editView");
  assert.equal(await form.getByLabel("安排日期").inputValue(),String(day));
  const search = form.getByRole("searchbox");
  await search.fill("不存在的测试地点");
  await form.getByText(/没有找到可添加的地点/).waitFor();
  assert.equal(await form.getByRole("button",{name:"试排并核验"}).isEnabled(),false);
  await search.fill(name);
  await form.getByLabel("选择地点").selectOption({label:name});
}

async function mapEdits(page) {
  await page.goto(base + "/" + trip);
  await page.locator("#sec-day-3 [data-plan-edit]").first().waitFor();
  const name = "上海自然博物馆";
  if (fromTimeline) {
    assert.equal(await page.locator(".day-sec [data-plan-insert]").count(),4);
    assert.equal(await page.locator("#sec-day-3 .tl > :last-child [data-plan-insert]").count(),1);
    await addFromTimeline(page,3,name);
  } else await popVisit(page,name);
  const form = page.locator("#editView");
  await form.getByLabel("安排日期").selectOption("3");
  await form.getByLabel("停留分钟").fill("45");
  const added = await preview(page,() => form.locator("#checkMapVisit").click());
  assert.equal(added.state,"complete",added.message);
  assert.equal(added.change.anchor.deadline,"15:00");
  assert.ok(added.change.anchor.after_minutes >= 0);
  report.insert = added.change;
  await form.getByRole("button",{name:"采用并保存"}).click();
  await page.waitForURL(u => u.searchParams.get("revision") === added.id);
  await page.reload();
  const addedCard = page.locator("#sec-day-3 .it").filter({hasText:name});
  await addedCard.waitFor();
  await addedCard.getByRole("button",{name:"编辑" + name,exact:true}).click();
  const removed = await preview(page,() => page.getByRole("button",{name:"试移除这一站",exact:true}).click());
  assert.equal(removed.state,"complete",removed.message);
  assert.equal(removed.change.after.visit_minutes,added.change.before.visit_minutes);
  report.remove = removed.change;
  await form.getByRole("button",{name:"返回原方案"}).click();
  assert.ok(await addedCard.isVisible());
  await page.locator("#moreBtn").click();
  await page.getByRole("button",{name:"修改记录",exact:true}).click();
  await form.getByRole("button",{name:"打开原方案"}).click();
  await addedCard.waitFor({state:"detached"});
  if (fromTimeline) await addFromTimeline(page,4,name);
  else await popVisit(page,name);
  await form.getByLabel("安排日期").selectOption("4");
  const refused = await preview(page,() => form.locator("#checkMapVisit").click());
  assert.equal(refused.state,"edit_conflict",refused.message);
  assert.equal(await form.getByRole("button",{name:"采用并保存"}).count(),0);
  report.fullDay = refused.message;
  const locked = page.locator("#sec-day-3 .it").filter({hasText:"预约已锁定"}).first();
  const lockedId = await locked.getAttribute("data-uid");
  const failed = await page.request.post(base + "/api/itinerary-edits/preview", {
    data:{job:"01b135fda6c446b38440ab8716b0f4c2",candidate:"4c98df9bbd58c313bcc80b88",action:"remove_visit",activity:lockedId}});
  assert.equal(failed.status(),409);
  report.removeReservation = failed.status();
}

async function mobileDayFooter(page) {
  const userTask = "/?job=c65c872e0b0e4120b8570a80e08e96a4&candidate=597355ee4d2364e93db90af5&day=1";
  await page.goto(base + userTask);
  await page.locator("#sec-day-1 .it").first().waitFor();
  const section = page.locator("#sec-day-1");
  await section.getByRole("button",{name:"添加地点",exact:true}).click();
  const form = page.locator("#editView");
  assert.equal(await form.getByLabel("安排日期").inputValue(),"1");
  await form.getByRole("searchbox").fill("上海");
  await form.getByLabel("选择地点").locator("option").nth(1).waitFor({state:"attached"});
  assert.equal(await page.locator("#layout").evaluate(el => el.classList.contains("show-map")),false);
  await form.getByRole("button",{name:"取消",exact:true}).click();
  assert.equal(await form.isVisible(),false);
  await page.goto(base + "/?trip=personalized");
  await page.locator("#planBody .it").first().waitFor();
  assert.equal(await page.locator("[data-plan-insert]").count(),0);
  report.dayFooter = {mobileWidth:390, initialDay:1, localSearch:true, readOnlyHidden:true};
}
async function transferNotice(page) {
  const previews = [];
  const datedRoutes = [];
  page.on("request", request => {
    if (request.url().endsWith("/itinerary-edits/preview")) previews.push(request.url());
    const url = new URL(request.url());
    if (url.pathname === "/api/route" && url.searchParams.has("departure")) datedRoutes.push(url.href);
  });
  await page.goto(base + "/" + rail);
  await page.locator("#sec-day-1 .it").first().waitFor({timeout:45000});
  await page.locator("#transferReview summary").click();
  await page.locator("#transferStart").fill("2026-11-23");
  await page.waitForFunction(() => document.querySelector("#transferState").textContent.includes("本次"));
  report.transferCheck = await page.locator("#transferRows").innerText();
  if (!live) assert.match(report.transferCheck, /接驳时间偏紧/);
  assert.equal(await page.locator("#transferRows button").count(),0);
  await page.reload();
  await page.locator("#transferReview summary").waitFor();
  assert.equal(await page.locator("#transferStart").inputValue(), "2026-11-23");
  assert.equal(await page.locator("#transferRows").innerText(), "");
  assert.equal(datedRoutes.length, 1);
  assert.deepEqual(previews,[]);
  report.transferPreviews = previews.length;
}
(async () => {
  const browser = await chromium.launch({channel:"chrome",headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:900}});
    const phone = await browser.newPage({viewport:{width:390,height:850},isMobile:true,hasTouch:true});
    for (const tab of [page,phone]) {
      tab.on("pageerror",e => report.errors.push(e.message));
      await tab.route("**/*",async route => {
        const u = new URL(route.request().url());
        if (u.origin !== base) return route.abort();
        if (u.pathname === "/api/routes") return route.fulfill({json:[]});
        if (u.pathname === "/api/route" && live && !u.searchParams.has("departure")) return route.fulfill({json:{unavailable:true}});
        if (u.pathname === "/api/route" && !live) {
          const departure = u.searchParams.get("departure");
          const minutes = departure?.endsWith("T16:37") ? 61 : 12;
          return route.fulfill({json:{source:"amap",requestedDeparture:departure,minutes,durationSeconds:minutes*60}});
        }
        return route.continue();
      });
    }
    if (!live) await mapEdits(page);
    if (fromTimeline) await mobileDayFooter(phone);
    try { await transferNotice(phone); }
    catch (e) {
      report.phoneAtFailure = (await phone.locator("body").innerText()).slice(0,1600);
      throw e;
    }
    assert.deepEqual(report.errors,[]);
  } catch (error) {
    report.errors.push(error.message);
    throw error;
  } finally {
    await browser.close();
    if (process.argv[2]) fs.writeFileSync(process.argv[2], JSON.stringify(report,null,2), {flag:"wx"});
  }
})().catch(error => {console.error(error);process.exitCode=1;});
