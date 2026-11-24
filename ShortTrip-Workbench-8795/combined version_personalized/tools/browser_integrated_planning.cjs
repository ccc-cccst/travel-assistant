// Exercise the actual local product page and isolated planning workers.
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const fs = require('node:fs/promises');
const net = require('node:net');
const path = require('node:path');
const {chromium} = require(process.env.SHORTTRIP_PLAYWRIGHT_MODULE || 'playwright');

const root = path.resolve(__dirname, '..');
const python = path.resolve(root, '../ShortTrip/.venv/Scripts/python.exe');
const output = path.resolve(process.argv[2] || 'result/experiments/P014_integrated_planning/browser_01');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

async function ephemeralPort() {
  const listener = net.createServer();
  await new Promise((resolve, reject) => {
    listener.on('error', reject);
    listener.listen(0, '127.0.0.1', resolve);
  });
  const {port} = listener.address();
  await new Promise(resolve => listener.close(resolve));
  return port;
}

async function fillTrip(page, origin, {days, people = 2, preference = false, appointment = false, roomType}) {
  await page.goto(origin + '/tripspec');
  await page.locator('select[name=start_city] option[value=南京]').waitFor({state: 'attached'});
  await page.getByLabel('出发地', {exact: true}).selectOption('南京');
  await page.getByLabel('目的地', {exact: true}).selectOption('上海');
  await page.getByLabel('旅行天数').fill(String(days));
  if (people !== null) await page.getByLabel('同行人数').fill(String(people));
  await page.getByLabel('城际交通').selectOption('train');
  if (roomType) {
    await page.getByText('住宿意向', {exact: true}).click();
    await page.getByLabel('房间数').fill('1');
    await page.getByLabel('房型意向').selectOption(roomType);
  }
  if (preference || appointment) {
    await page.getByText('喜欢的体验', {exact: true}).click();
    await page.getByLabel('博物馆', {exact: true}).check();
    await page.getByLabel('节奏').selectOption('relaxed');
  }
  if (appointment) {
    await page.getByLabel('市内交通').selectOption('taxi');
    await page.getByText('已确认的预约', {exact: true}).click();
    await page.getByLabel('预约地点').fill('上海博物馆');
    await page.getByLabel('第几天').fill('3');
    await page.getByLabel('开始时间').fill('15:00');
    await page.getByLabel('时长（分钟）').fill('60');
  }
  await page.getByRole('button', {name: '核对我的需求'}).click();
  if (appointment) {
    await page.locator('#items .item').filter({hasText: '市内交通方式 · 出租车'}).waitFor();
  }
}

async function planFromPage(page, days) {
  const button = page.getByRole('button', {name: '按确认后的需求规划'});
  await button.waitFor();
  const started = performance.now();
  await button.click();
  try {
    await page.waitForFunction(() => document.querySelector('.plan-day') ||
      document.getElementById('plan-status').textContent.includes('TRIP-'), null, {timeout: 100000});
  } catch (error) {
    const state = await page.locator('#plan-status').textContent();
    throw new Error(`No first plan at ${page.url()}. Page status: ${state}`, {cause: error});
  }
  assert(await page.locator('.plan-day').count(), await page.locator('#plan-status').innerText());
  const first = (performance.now() - started) / 1000;
  await page.getByRole('button', {name: '推荐方案', exact: true}).waitFor({timeout: 100000});
  assert.equal(await page.locator('.plan-day').count(), days);
  assert(await page.getByText(/出行前待核实/).first().isVisible());
  return {first_visible_seconds: first, completion_seconds: (performance.now() - started) / 1000};
}

async function checkScenarios(page, origin, device, names = ['ordinary', 'preference', 'appointment']) {
  const measurements = [];
  for (const name of names) {
    const appointment = name === 'appointment';
    const preference = name === 'preference';
    const days = appointment ? 4 : 2;
    await fillTrip(page, origin, {days, preference, appointment, roomType: name === 'ordinary' ? '单床房' : undefined});
    const timing = await planFromPage(page, days);
    if (preference) {
      assert.match(await page.locator('#items').innerText(), /博物馆/);
      assert.match(await page.locator('#plan-result').innerText(), /你的偏好.*博物馆/s);
    }
    if (appointment) {
      assert.match(await page.locator('.plan-day').nth(2).innerText(), /已预约.*上海博物馆.*15:00–16:00/);
    }
    const first = page.locator('.plan-day').first();
    const routeButton = first.getByRole('button', {name: '查看走法'});
    assert(await routeButton.isVisible());
    assert(await first.getByRole('button', {name: '顺路优化'}).isVisible());
    if (name === 'ordinary') {
      assert.match(await page.locator('#items').innerText(), /房型意向 · 单床房/);
      assert.match(await page.locator('#plan-result').innerText(), /房型意向 · 单床房 · 预订时核实/);
      assert.match(await first.innerText(), /住宿.*· 1 间/);
      await routeButton.click();
      const segment = first.locator('.route-options').first();
      await segment.locator('summary').waitFor({timeout: 70000});
      await segment.locator('summary').click();
      const options = await segment.innerText();
      for (const mode of ['公共交通', '驾车', '步行']) assert(options.includes(mode), options);
    }
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    measurements.push({scenario: name, device, ...timing});
  }
  return measurements;
}

async function main() {
  await fs.mkdir(output);
  const port = await ephemeralPort();
  const origin = 'http://127.0.0.1:' + port;
  const server = spawn(python, ['-B', '-X', 'utf8', '-m', 'chinatravel.personalization.web', '--port', String(port)],
    {cwd: root, windowsHide: true, env: {...process.env, DEEPSEEK_API_KEY: ''}});
  let serverLog = '';
  server.stdout.on('data', data => { serverLog += data; });
  server.stderr.on('data', data => { serverLog += data; });
  let browser;
  const errors = [];
  const stages = new Set();
  let early = null;
  let route = null;
  try {
    for (let tryCount = 0; !serverLog.includes(origin) && tryCount < 100; tryCount++) await pause(250);
    assert(serverLog.includes(origin), 'Backend startup timed out');
    browser = await chromium.launch({channel: 'msedge', headless: true});
    const page = await browser.newPage({viewport: {width: 1366, height: 960}});
    page.on('pageerror', e => errors.push(e.message));
    if (process.argv.includes('--lodging-only')) {
      const mobile = await browser.newPage({viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true});
      mobile.on('pageerror', e => errors.push(e.message));
      const scenarios = [
        ...await checkScenarios(page, origin, 'desktop', ['ordinary']),
        ...await checkScenarios(mobile, origin, 'mobile', ['ordinary']),
      ];
      assert.deepEqual(errors, []);
      const result = {room_preference_and_explicit_count: true, browser_errors: errors, scenarios};
      await fs.writeFile(path.join(output, 'RESULT.json'), JSON.stringify(result, null, 2));
      return;
    }
    page.on('response', async response => {
      if (!response.ok() || !response.url().includes('/trip-planning/job?')) return;
      const result = await response.json().catch(() => null);
      if (!result) return;
      if (result.stage) stages.add(result.stage);
      if (result.first && result.state === 'planning') early = result.first;
      if (result.route) route = result.route;
    });
    await fillTrip(page, origin, {days: 4, appointment: true});
    const desktopAppointment = await planFromPage(page, 4);
    assert(await page.getByRole('button', {name: '推荐方案', exact: true}).isVisible(),
      await page.locator('#plan-status').innerText());
    assert(early?.candidate.days.length === 4, 'First checked result was never visible during search');
    assert.match(await page.locator('.plan-day').nth(2).innerText(), /已预约.*上海博物馆.*15:00–16:00/);
    await page.getByText('参考费用明细', {exact: true}).click();
    assert.match(await page.locator('#plan-result details').innerText(), /餐费.*元/);
    const stableUrl = page.url();
    const original = page.locator('.plan-day').first();
    await original.getByRole('button', {name:'查看走法'}).click();
    await original.locator('.route-options summary').first().waitFor({timeout:70000});
    await original.locator('.route-options summary').first().click();
    assert.match(await original.locator('.route-detail').innerText(), /按原顺序.*直线距离/);
    assert.match(await original.locator('.route-options').first().innerText(), /公共交通.*受已确认的交通要求限制/);
    await page.locator('#planning').screenshot({path: path.join(output, 'desktop.png')});
    const backup = page.getByRole('button', {name: /^备选/}).first();
    await backup.click();
    assert.match(await page.locator('#plan-result').innerText(), /对比推荐方案/);

    const mobile = await browser.newPage({viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true});
    mobile.on('pageerror', e => errors.push(e.message));
    mobile.on('response', async response => {
      if (!response.url().includes('/trip-planning/job?') || !response.ok()) return;
      const result = await response.json().catch(() => null);
      if (result?.route) route = result.route;
    });
    await mobile.goto(stableUrl);
    await mobile.getByRole('button', {name: '推荐方案', exact: true}).waitFor();
    assert.equal(await mobile.getByLabel('市内交通').inputValue(), 'taxi');
    const third = mobile.locator('.plan-day').nth(2);
    await third.getByRole('button', {name: '顺路优化'}).click();
    await third.locator('.route-detail ol').waitFor({timeout: 70000});
    await third.locator('.route-options summary').first().click();
    assert.match(await third.locator('.route-detail').innerText(), /上海博物馆.*15:00–16:00/);
    assert(route?.day === 3);
    const incomingModes = route.activities.flatMap(item => item.incoming_modes);
    assert(incomingModes.length > 0 && incomingModes.every(mode => mode === 'taxi'));
    assert.match(await third.locator('.route-detail').innerText(), /驾车.*分钟.*按出租车估算/);
    assert.match(await third.locator('.route-detail').innerText(), /直线距离合计/);
    assert(route.distance.after_km <= route.distance.before_km);
    const testedRoute = route;
    const testedEarly = early;
    assert(await mobile.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    await mobile.locator('#planning').screenshot({path: path.join(output, 'mobile.png')});

    const scenarios = [
      {scenario: 'appointment', device: 'desktop', ...desktopAppointment},
      ...await checkScenarios(page, origin, 'desktop', ['ordinary', 'preference']),
      ...await checkScenarios(mobile, origin, 'mobile'),
    ];
    await fillTrip(page, origin, {days: 2, people: null});
    await page.locator('#questions .issue').filter({hasText: '人数'}).waitFor();
    assert(!await page.getByRole('button', {name: '按确认后的需求规划'}).isVisible());
    await page.goto(origin + '/tripspec');
    await page.locator('select[name=start_city] option[value=成都]').waitFor({state: 'attached'});
    await page.getByLabel('出发地', {exact: true}).selectOption('成都');
    await page.getByLabel('目的地', {exact: true}).selectOption('苏州');
    await page.getByLabel('旅行天数').fill('4');
    await page.getByLabel('同行人数').fill('2');
    await page.getByLabel('城际交通').selectOption('airplane');
    await page.getByRole('button', {name: '核对我的需求'}).click();
    await page.getByRole('button', {name: '按确认后的需求规划'}).click();
    await page.getByText(/本地旅行快照中没有符合要求的.*班次/).waitFor({timeout: 60000});
    assert.match(await page.locator('#plan-status').innerText(), /TRIP-TRANSPORT-001/);
    assert.deepEqual(errors, []);
    const result = {request_url: stableUrl, first_verified_seconds: testedEarly.elapsed_seconds, stages: [...stages],
      first_visible_while_searching: true, compared_alternative: true, reloaded_on_mobile: true,
      appointment_preserved: true, viewed_existing_order: true,
      sorted_day: testedRoute.day, route_outcome: testedRoute.result, distance: testedRoute.distance,
      chosen_local_modes: [...new Set(incomingModes)],
      no_air_service_code: 'TRIP-TRANSPORT-001', missing_people_requires_confirmation: true,
      ordinary_three_transport_options: true,
      room_preference_and_explicit_count: true,
      browser_errors: errors, scenarios};
    await fs.writeFile(path.join(output, 'RESULT.json'), JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result));
  } finally {
    if (browser) await browser.close();
    server.kill();
    await fs.writeFile(path.join(output, 'server.log'), serverLog);
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
