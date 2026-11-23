// The optional transfer check shows a notice for the selected date.
const assert = require('node:assert/strict');
const {chromium} = require(process.env.SHORTTRIP_PLAYWRIGHT || 'playwright');
const base = process.env.SHORTTRIP_WORKBENCH || 'http://127.0.0.1:8791';
const url = base + '/?request=9e623f23bb944cb3963ae75e43351fa7&job=324279423e6d4db09aeaa60624b84dc6&candidate=7d2e9f0e14ae90ba92f7542f&day=1';

async function check(browser, live) {
  const page = await browser.newPage({viewport:{width:live ? 1440 : 390,height:900}});
  const errors = [], times = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', async route => {
    const u = new URL(route.request().url());
    if (u.origin !== base) return route.abort();
    assert.ok(!u.pathname.endsWith('/itinerary-edits/preview'), '核对接驳应只读取路线');
    if (u.pathname === '/api/routes') return route.fulfill({json:[]});
    if (u.pathname === '/api/route') {
      const dep = u.searchParams.get('departure');
      if (!dep) return route.fulfill({json:{unavailable:true}});
      times.push(dep);
      if (!live) return route.fulfill({json:{source:'unavailable',unavailable:true,reason:'CUQPS_HAS_EXCEEDED_THE_LIMIT(10021)',requestedDeparture:dep}});
      assert.ok(times.length <= 1);
    }
    return route.continue();
  });
  try {
    await page.goto(url,{waitUntil:'domcontentloaded'});
    await page.locator('#transferReview summary').click();
    assert.deepEqual(times, []);
    await page.locator('#transferStart').fill('2026-11-23');
    await page.waitForFunction(()=>document.querySelector('#transferState').textContent.includes('本次'));
    const summary = await page.locator('#transferRows').innerText();
    if (live) assert.match(summary,/高德估时|待核实/);
    else assert.match(summary,/频率受限/);
    assert.equal(await page.locator('#transferRows button').count(),0);
    assert.equal(times.length,1);
    assert.deepEqual(errors,[]);
    return {url, times, fixture:!live, summary};
  } finally {await page.close();}
}
(async () => {
  const browser = await chromium.launch({channel:'chrome',headless:true});
  try {console.log(JSON.stringify({live:await check(browser,true),mobile:await check(browser,false)},null,2));}
  finally {await browser.close();}
})().catch(error=>{console.error(error); process.exitCode=1;});
