// The forecast substitution is only for browser tests. Edits still use the real planner.
const assert = require('node:assert/strict');
const {chromium} = require(process.env.SHORTTRIP_PLAYWRIGHT || 'playwright');
const base = 'http://127.0.0.1:8795';
const cases = [
  {city:'成都', job:'478845b168844b27b4d100f9060ddc97', candidate:'628943ed84dde8f9332b8f2d',
   request:'e8372f10835b43cf88c93207af1252ab', day:1, date:'2026-11-22', visit:'井巷子', alternative:'成都博物馆', width:1440},
  {city:'杭州', job:'2a67a92984d3488b8bae2d15a4512659', candidate:'66f368f5cb2ca2dbd3981702',
   request:'8142b378b2df460aad9b77b715c70af0', day:2, date:'2026-11-23', visit:'雷峰塔', alternative:'中国丝绸博物馆', width:390},
  {city:'上海', job:'0241a805a917423ca6302303cdabc5fc', candidate:'a87a6ee1fc131fec41bfeb4a',
   request:'5a8851e43c6f420a89470acd19ec97f2',day:2,date:'2026-11-22', width:1440},
];

async function verify(browser, fixture) {
  const {width, day, job, request, candidate}=fixture;
  const page = await browser.newPage({viewport:{width,height:900}});
  const errors = [], weatherRequests = [];
  let dateForRain;
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== base) return route.abort();
    if (url.pathname === '/api/routes') return route.fulfill({json:[]});
    if (url.pathname === '/api/route') return route.fulfill({json:{unavailable:true}});
    if (url.pathname === '/api/day-facts/weather') {
      weatherRequests.push(url.searchParams.get('start'));
      if (!fixture.visit) return route.continue();
      const original = await route.fetch();
      const body = await original.json();
      assert.equal(body.status,'forecast',JSON.stringify(body));
      dateForRain = body.days[day - 1].date;
      const cast = body.days[day - 1];
      Object.assign(cast,{day:'小雨（测试模拟）',dayRain:true});
      return route.fulfill({response:original,json:body});
    }
    return route.continue();
  });
  try {
    const url = base + '/?' + new URLSearchParams({job, request, candidate, day});
    await page.goto(url,{waitUntil:'domcontentloaded'});
    await page.getByRole('button',{name:'出发前核对',exact:true}).click();
    await page.locator('.facts-date').fill(fixture.date);
    await page.locator('.facts-day').selectOption(String(day));
    await page.locator('.facts-weather').getByText(/白天/).first().waitFor();
    const text = await page.locator('.facts-weather').innerText();
    let detail;
    if (fixture.visit) {
      assert.match(text,/测试模拟/);
      const row = page.locator('.facts-weather p').filter({hasText:fixture.visit});
      await row.getByRole('button',{name:'试排 '+fixture.alternative}).click();
      await page.getByText('修改预览已通过核验',{exact:true}).waitFor({timeout:65000});
      detail=await page.locator('#editView').innerText();
      assert.ok(detail.includes(fixture.alternative));
      await page.getByRole('button',{name:'返回原方案',exact:true}).click();
    } else {
      await page.getByRole('button',{name:'查营业 上海博物馆',exact:true}).click();
      await page.getByText(/常规时段当天闭馆/).waitFor();
      detail = await page.locator('.facts-places li').filter({hasText:'上海博物馆'}).first().innerText();
      assert.match(detail,/停止入场 15:00/);
      assert.match(detail,/人民广场馆/);
      assert.doesNotMatch(detail,/东馆/);
      await page.locator('.facts-date').fill('2026-12-15');
      await page.getByText(/暂无可用预报/).waitFor();
    }
    assert.deepEqual(errors,[]);
    return {city:fixture.city,width,url,weatherRequests,dateForRain,simulatedRain:Boolean(fixture.visit),text,detail};
  } finally { await page.close(); }
}

(async () => {
  const browser=await chromium.launch({channel:'chrome',headless:true});
  try {
    const rows=[];
    for (const fixture of cases) rows.push(await verify(browser,fixture));
    console.log(JSON.stringify(rows,null,2));
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
