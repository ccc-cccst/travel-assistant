// Exercise recovery through the TripSpec page with real local planning workers.
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const {randomUUID} = require('node:crypto');
const fs = require('node:fs/promises');
const net = require('node:net');
const path = require('node:path');
const {chromium} = require(process.env.SHORTTRIP_PLAYWRIGHT_MODULE || 'playwright');

const root = path.resolve(__dirname, '..');
const output = path.resolve(process.argv[2] || 'result/experiments/P033_recovery/browser_01');
const python = path.resolve(root, '../ShortTrip/.venv/Scripts/python.exe');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

async function openRequest(page, origin, name, tryBudget = false) {
  const source = path.join(root, 'result/experiments/P031_first_plan/after_02', name, 'request.json');
  return openSavedRequest(page, origin, source, tryBudget);
}

async function openSavedRequest(page, origin, source, tryBudget = false) {
  const spec = JSON.parse(await fs.readFile(source, 'utf8'));
  spec.request_id = randomUUID().replaceAll('-', '');
  const folder = path.join(root, 'result/tripspec', spec.request_id);
  await fs.mkdir(folder, {recursive: true});
  const body = JSON.stringify(spec);
  await fs.writeFile(path.join(folder, 'latest.json'), body);
  await fs.writeFile(path.join(folder, 'revision_' + spec.request_revision + '.json'), body);
  await page.goto(origin + '/tripspec?id=' + spec.request_id);
  if (tryBudget) await page.getByLabel('原预算排不成时，也看看更高预算的方案').check();
  await page.locator('#start-plan').click();
  return spec;
}

async function main() {
  await fs.mkdir(output, {recursive: true});
  const listener = net.createServer();
  await new Promise((resolve, reject) => {
    listener.once('error', reject);
    listener.listen(0, '127.0.0.1', resolve);
  });
  const {port} = listener.address();
  await new Promise(resolve => listener.close(resolve));
  const origin = 'http://127.0.0.1:' + port;
  const env = {...process.env};
  delete env.DEEPSEEK_API_KEY;
  const server = spawn(python, ['-B', '-X', 'utf8', '-m', 'chinatravel.personalization.web', '--port', String(port)],
                       {cwd: root, env, windowsHide: true, stdio: ['ignore', 'ignore', 'inherit']});
  let browser;
  try {
    for (let i = 0; ; i++) {
      try {
        if ((await fetch(origin + '/options')).ok) break;
      } catch (error) {
        if (i === 120 || server.exitCode !== null) throw error;
      }
      await pause(250);
    }
    browser = await chromium.launch({channel: 'msedge', headless: true});
    const page = await browser.newPage({viewport: {width: 1220, height: 920}});
    const spec = await openRequest(page, origin, 'human1000_666');
    const accept = page.getByRole('button', {name: '接受这项调整'});
    await accept.waitFor({timeout: 65000});
    assert.match(await page.locator('#plan-result').innerText(), /去程交通.*飞机.*火车/);
    assert.equal(await page.locator('.plan-day').count(), 5);
    await page.locator('#planning').screenshot({path: path.join(output, 'train-preview.png')});
    await accept.click();
    await page.locator('#recovery-adopted').waitFor();
    await page.reload();
    await page.locator('#recovery-adopted').waitFor();
    assert.equal(await page.locator('.plan-day').count(), 5);
    const resumed = await (await fetch(origin + '/tripspec/state?id=' + spec.request_id)).json();
    assert.equal(resumed.request_revision, spec.request_revision + 1);

    const mobile = await browser.newPage({viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true});
    await openRequest(mobile, origin, 'easy_24');
    await mobile.getByRole('heading', {name: '苏州当地游览建议'}).waitFor({timeout: 65000});
    const draft = await mobile.locator('#plan-result').innerText();
    assert.match(draft, /15:46.*13:18/);
    assert.match(draft, /往返交通与衔接/);
    assert.equal(await mobile.getByRole('button', {name: '接受这项调整'}).count(), 0);
    assert(await mobile.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    await mobile.locator('#planning').screenshot({path: path.join(output, 'suzhou-draft-mobile.png')});

    const budget = await openRequest(page, origin, 'easy_24', true);
    await accept.waitFor({timeout: 65000});
    const budgetCard = await page.locator('#plan-result').innerText();
    assert.match(budgetCard, /旅行天数.*1.*2/);
    assert.match(budgetCard, /整团预算上限.*1600.*2000/);
    assert.match(budgetCard, /参考费用比原预算多/);
    await page.locator('#planning').screenshot({path: path.join(output, 'budget-preview.png')});
    await accept.click();
    await page.locator('#recovery-adopted').waitFor();
    const adjusted = await (await fetch(origin + '/tripspec/state?id=' + budget.request_id)).json();
    assert.equal(adjusted.request_revision, budget.request_revision + 1);
    assert.equal(adjusted.items.find(item => item.field === 'days').value, 2);
    assert.equal(adjusted.items.find(item => item.field === 'budget').value, 2000);
    const scoped = path.join(root, 'result/experiments/P034_delivery_and_quality/web_live_01/human_104/TRIPSPEC.json');
    await openSavedRequest(page, origin, scoped);
    const budgetLine = page.locator('#plan-result').getByText(/预算上限（不含城际车票和住宿）/);
    await budgetLine.waitFor({timeout: 65000});
    assert.match(await budgetLine.innerText(), /1,500 元（整团）/);
    assert.match(await page.locator('#plan-result').innerText(), /整团参考费用/);
    assert.equal(await page.locator('#budget-alternative').isVisible(), false);
    await page.locator('#planning').screenshot({path: path.join(output, 'scoped-budget.png')});
    const result = {desktop_preview: true, accepted_and_reloaded: true, mobile_draft: true,
                    budget_preview_and_accept: true, scoped_budget_visible: true};
    await fs.writeFile(path.join(output, 'RESULT.json'), JSON.stringify(result, null, 2) + '\n');
    console.log(JSON.stringify(result));
  } finally {
    if (browser) await browser.close();
    server.kill();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
