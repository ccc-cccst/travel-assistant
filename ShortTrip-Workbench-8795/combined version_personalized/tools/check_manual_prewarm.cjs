// Exercise time editing from a local QA itinerary without adopting the preview.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const {chromium} = require(process.env.SHORTTRIP_PLAYWRIGHT || "playwright");
const origin = "http://127.0.0.1:8795";
const source = "/?job=01b135fda6c446b38440ab8716b0f4c2&candidate=4c98df9bbd58c313bcc80b88" +
  "&revision=55350b07bb5546b1a8fbe374c6af0bc5&day=3";
const activity = "943d6f07725c3c87313e8f57-d3-a2";

async function run() {
  const browser = await chromium.launch({headless: true, channel: "chrome"});
  try {
    const page = await browser.newPage({viewport: {width: 390, height: 844}, hasTouch: true, reducedMotion: "reduce"});
    await page.route("**/*", route => route.request().url().startsWith(origin + "/") ? route.continue() : route.abort());
    await page.goto(origin + source, {waitUntil: "domcontentloaded"});
    if (process.argv.includes("--from-map")) {
      await page.locator(".current-revision").waitFor();
      await page.locator("#mapFab").click();
      await page.locator('#map .leaflet-marker-icon[title="上海自然博物馆"] .b').click();
      await page.locator("#mapStop").getByRole("button", {name: "在行程中查看"}).click();
      assert.equal(await page.locator(`.it.hl[data-uid="${activity}"]`).isVisible(), true);
    }
    const edit = page.locator(`[data-plan-edit="${activity}"]`);
    await edit.click();
    const request = page.waitForResponse(response => response.url() === origin + "/api/itinerary-edits/prewarm");
    const start = performance.now();
    await page.getByRole("button", {name: "调整时间", exact: true}).click();
    assert.equal((await request).status(), 202);
    const editor = page.locator("#editView");
    await editor.locator('input[name="start"]').fill("12:57");
    await editor.locator('input[name="end"]').fill("13:27");
    // Keep the simulated time available for reviewing and editing the form fixed.
    const fillSeconds = Number(process.argv[3] || 8);
    await new Promise(resolve => setTimeout(resolve, Math.max(0, 1000 * fillSeconds - (performance.now() - start))));
    const clicked = performance.now();
    const response = page.waitForResponse(result => result.url() === origin + "/api/itinerary-edits/preview");
    await editor.getByRole("button", {name: "试排并核验"}).click();
    assert.equal((await response).status(), 202);
    const submitted = await (await response).json();
    await editor.getByRole("heading", {name: "修改预览已通过核验"}).waitFor();
    const end = performance.now();
    assert.match(await editor.innerText(), /12:57–13:27/);
    assert.equal(new URL(page.url()).searchParams.get("revision"), "55350b07bb5546b1a8fbe374c6af0bc5");
    fs.writeFileSync(process.argv[2], JSON.stringify({
      id: submitted.id, prewarm: "accepted", form_seconds: (clicked - start) / 1000,
      post_click_seconds: (end - clicked) / 1000, total_seconds: (end - start) / 1000
    }, null, 2));
  } finally {
    await browser.close();
  }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
