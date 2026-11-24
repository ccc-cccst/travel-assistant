// Run with Playwright installed or SHORTTRIP_PLAYWRIGHT set to a playwright-core directory.
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const net = require('node:net');
const path = require('node:path');
const { setTimeout: delay } = require('node:timers/promises');
const { chromium } = require(process.env.SHORTTRIP_PLAYWRIGHT || 'playwright');
const root = path.resolve(__dirname, '../..');
const web = path.join(root, 'shorttrip-webui/shorttrip-webui/webui');
const archive = path.join(__dirname, '../result/manual/0241a805a917423ca6302303cdabc5fc');

async function localPort() {
  const listener = net.createServer();
  await new Promise(resolve => listener.listen(0, '127.0.0.1', resolve));
  const { port } = listener.address();
  await new Promise(resolve => listener.close(resolve));
  return port;
}

async function check(browser, base, width, knownDate) {
  const page = await browser.newPage({ viewport: { width, height: 900 } });
  const errors = [];
  let success = true;
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== base) return route.abort();
    if (knownDate && url.pathname === '/api/archived-trip') {
      const source = await route.fetch();
      const group = await source.json();
      for (const version of group.versions) version.startDate = '2026-11-22';
      return route.fulfill({ json: group });
    }
    const departure = url.searchParams.get('departure');
    if (url.pathname === '/api/route' && departure && success) {
      assert.equal(departure, '2026-11-23T12:25');
      assert.equal(url.searchParams.get('mode'), 'transit');
      // Synthetic display fixture. The API pilot queries the actual provider separately.
      return route.fulfill({ json: { source: 'amap', mode: 'transit', minutes: 50, durationSeconds: 2990, requestedDeparture: departure } });
    }
    return route.continue();
  });
  try {
    await page.goto(base + '/?trip=personalized&day=2');
    const leg = page.locator('#sec-day-2 .archive-leg [data-route-detail]').last();
    await leg.click({ timeout: 15000 });
    const note = page.locator('#rmNote');
    assert.match(await page.locator('#routeHeading').innerText(), /上海虹桥站/);
    await page.locator('#rmMode').selectOption('transit');
    const date = page.locator('#rmDepartureDate');
    assert.equal(await date.inputValue(), knownDate ? '2026-11-23' : '');
    if (!knownDate) await date.fill('2026-11-23');
    await page.waitForFunction(() => document.querySelector('#rmNote').textContent.includes('提前 54 分钟'));
    const confirmed = await note.innerText();
    assert.match(confirmed, /2026-11-23 12:25/);
    success = false;
    const missingResponse = page.waitForResponse(res => {
      const u = new URL(res.url());
      return u.pathname === '/api/route' && u.searchParams.get('departure') === '2026-11-23T12:25' && u.searchParams.get('refresh') === '1';
    });
    await page.locator('#rmRetry').click();
    const missing = await (await missingResponse).json();
    assert.equal(missing.source, 'unavailable');
    assert.equal(missing.requestedDeparture, '2026-11-23T12:25');
    await page.waitForFunction(() => document.querySelector('#rmNote').textContent.includes('待核实'));
    const unavailable = await note.innerText();
    assert.doesNotMatch(unavailable, /提前 54|约 50/);
    await page.locator('#rmMode').selectOption('walk');
    assert.equal(await date.isVisible(), false);
    await page.locator('#routeClose').click();
    assert.deepEqual(errors, []);
    return { width, knownDate, confirmed_fixture: confirmed, missing_key_http: unavailable };
  } finally { await page.close(); }
}

async function checkLive(base) {
  assert.match(base, /^http:\/\/127\.0\.0\.1:\d+$/);
  const url = base + '/?request=5a8851e43c6f420a89470acd19ec97f2&job=0241a805a917423ca6302303cdabc5fc&candidate=a87a6ee1fc131fec41bfeb4a&day=2';
  const browser = await chromium.launch({channel: 'chrome', headless: true});
  try {
    const page = await browser.newPage({viewport:{width:1440, height:900}});
    const departure = process.env.SHORTTRIP_TEST_DATE + 'T12:25';
    assert.match(departure, /^\d{4}-\d{2}-\d{2}T12:25$/);
    let queries = 0;
    await page.route('**/*', r => {
      const u = new URL(r.request().url());
      if (u.origin !== base) return r.abort();
      if (u.pathname === '/api/routes') return r.fulfill({json:[]});
      if (u.pathname === '/api/route' && (u.searchParams.get('departure') !== departure || ++queries > 1))
        return r.fulfill({json:{unavailable:true}});
      return r.continue();
    });
    await page.goto(url, {waitUntil:'domcontentloaded'});
    await page.locator('#sec-day-2 .archive-leg [data-route-detail]').last().click();
    assert.match(await page.locator('#routeHeading').innerText(), /上海虹桥站/);
    await page.locator('#rmMode').selectOption('transit');
    const pending = page.waitForResponse(r => new URL(r.url()).searchParams.get('departure') === departure);
    await page.locator('#rmDepartureDate').fill(process.env.SHORTTRIP_TEST_DATE);
    const result = await (await pending).json();
    await page.waitForFunction(stamp => document.querySelector('#rmNote').textContent.includes(stamp), departure.slice(0, 10));
    const summary = await page.locator('#rmNote').innerText();
    console.log(JSON.stringify({url, departure, queries, source:result.source, retrievedAt:result.retrievedAt, summary}, null, 2));
    assert.equal(result.requestedDeparture, departure);
    assert.equal(result.source, 'amap', result.reason);
    assert.ok(result.durationSeconds > 0);
  } finally { await browser.close(); }
}

(async () => {
  if (process.env.SHORTTRIP_WORKBENCH) return checkLive(process.env.SHORTTRIP_WORKBENCH);
  const port = await localPort();
  const base = 'http://127.0.0.1:' + port;
  const service = spawn(process.env.SHORTTRIP_PYTHON || 'python', [
    '-B', '-X', 'utf8', path.join(web, 'backend/server.py'), '--demo-routes', '--port', String(port),
    '--amap-key', ' ', '--personalized-archive', archive, '--snapshot-roots', path.join(root, 'ShortTrip/artifacts/data')
  ], { windowsHide: true, stdio: 'ignore' });
  let browser;
  try {
    let ready = false;
    for (let i = 0; i < 80; i++) {
      if (service.exitCode !== null) throw Error('test server exited ' + service.exitCode);
      try { ready = (await fetch(base + '/api/workbench-status')).ok; } catch {}
      if (ready) break;
      await delay(150);
    }
    assert.ok(ready, 'archive test server did not start');
    browser = await chromium.launch({channel: 'chrome', headless: true});
    const desktop = await check(browser, base, 1440, false);
    const mobile = await check(browser, base, 390, true);
    console.log(JSON.stringify({ desktop, mobile }, null, 2));
  } finally {
    if (browser) await browser.close();
    service.kill();
  }
})().catch(e => {console.error(e); process.exitCode = 1;});
