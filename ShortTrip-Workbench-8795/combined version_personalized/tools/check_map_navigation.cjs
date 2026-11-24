// Verify how the map and timeline hand off the day and a selected stop.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {chromium} = require(process.env.SHORTTRIP_PLAYWRIGHT || "playwright");
const origin = "http://127.0.0.1:8795";
const longTrip = "/?job=0f69e8f2045145df876c1b681e40c504&candidate=2588d3ad9de691e60b7ffe90" +
  "&revision=9d9d3264a38c4127bf644a65d4f35cd7&day=7";
const baseline = process.argv.includes("--baseline");
const out = process.argv[2];

async function mobile(page, width) {
  await page.setViewportSize({width, height: 844});
  await page.goto(origin + longTrip, {waitUntil: "domcontentloaded"});
  const timelineDay = page.locator("#dayJumpSelect");
  await page.locator(".current-revision").waitFor();
  await page.waitForFunction(() => document.querySelector("#dayJumpSelect").value === "day-7");
  await timelineDay.selectOption("day-4");
  await page.waitForURL(url => url.searchParams.get("day") === "4");
  await page.locator("#mapFab").click();
  const mapDay = page.locator("#mapDaySelect");
  const daySwitchOnMap = await mapDay.isVisible();
  let weatherChanges = null, pinCount = null;
  if (!baseline) {
    assert.equal(daySwitchOnMap, true);
    assert.equal(await mapDay.inputValue(), "day-4");
    await mapDay.selectOption("day-5");
    await page.waitForURL(url => url.searchParams.get("day") === "5");
    await page.locator('#mapPane[data-weather-theme="rain"]').waitFor();
    assert.match(await page.locator("#mapWeather").innerText(), /小雨/);
    assert.equal(await page.locator("#map .pin.dim").count(), 0);
    await mapDay.selectOption("day-4");
    await page.waitForURL(url => url.searchParams.get("day") === "4");
    await page.locator('#mapPane[data-weather-theme="cloud"]').waitFor();
    assert.match(await page.locator("#mapWeather").innerText(), /多云/);
    weatherChanges = ["rain", "cloud"];
    pinCount = await page.locator("#map .pin").count();
  }
  await page.locator("#map .leaflet-marker-icon:has(.pin:not(.dim))").first().waitFor();
  const markers = await page.locator("#map .leaflet-marker-icon:has(.pin:not(.dim))").all();
  let selected;
  for (const marker of markers) {
    const pin = marker.locator(".b");
    if (await pin.click({trial: true, timeout: 500}).then(() => true, () => false)) {
      await pin.click();
      selected = await marker.getAttribute("title");
      break;
    }
  }
  assert.ok(selected, "The selected day should have an accessible pin");
  const popup = page.locator(baseline ? ".leaflet-popup .pp" : "#mapStop .pp");
  assert.equal(await popup.locator("h4").innerText(), selected);
  const focus = popup.getByRole("button", {name: "在行程中查看"});
  const itemId = await focus.getAttribute("data-pp-focus");
  if (baseline) {
    await page.waitForFunction(() => {
      const tip = document.querySelector(".leaflet-popup");
      return tip && getComputedStyle(tip).opacity === "1";
    });
  }
  const sampledTileCoverage = () => {
    const map = document.querySelector("#map").getBoundingClientRect();
    const tiles = Array.from(document.querySelectorAll("#map .leaflet-tile-loaded")).flatMap(tile => {
      const box = tile.getBoundingClientRect();
      return tile.naturalWidth && box.width > 0 && box.width <= tile.naturalWidth * 1.01 ? [box] : [];
    });
    let covered = 0;
    for (const x of [.1, .35, .65, .9]) {
      for (const y of [.1, .35, .65, .9]) {
        const px = map.left + x * map.width, py = map.top + y * map.height;
        if (tiles.some(box => box.left <= px && px < box.right && box.top <= py && py < box.bottom)) covered++;
      }
    }
    return covered;
  };
  if (!baseline) await page.waitForFunction(() => document.querySelector(".leaflet-tile-loaded"),
    null, {timeout: 4500}).catch(() => {});
  const baseMapCoverageSamples = await page.evaluate(sampledTileCoverage);
  const overlaps = await page.evaluate(() => {
    const rect = selector => document.querySelector(selector)?.getBoundingClientRect();
    const attribution = rect("#map .leaflet-control-attribution");
    const picker = rect("#mapDaySelect");
    const back = rect("#mapFab");
    const popupAction = rect("#mapStop .pp [data-pp-focus], .leaflet-popup .pp [data-pp-focus]");
    const weather = rect("#mapWeather");
    const toolbar = rect(".map-top");
    const crossed = (a,b) => Boolean(a?.height && b?.height && a.left < b.right && a.right > b.left &&
      a.top < b.bottom && a.bottom > b.top);
    return {credits: crossed(attribution, picker) || crossed(attribution, back),
            popupAction: crossed(popupAction, weather) || crossed(popupAction, toolbar),
            actionOnScreen: popupAction && popupAction.top >= 0 && popupAction.bottom <= innerHeight};
  });
  await page.screenshot({path: path.join(path.dirname(out), `map-${width}.png`)});
  await focus.click();
  const timelineVisible = await page.locator("#planBody").isVisible();
  const stop = page.locator(`.it[data-uid="${itemId}"]`);
  const signedDay = await page.locator("#dayJumpSelect").inputValue();
  const highlighted = await stop.evaluate(node => node.classList.contains("hl"));
  const stopVisible = timelineVisible && await stop.evaluate(node => {
      const body = document.querySelector("#planBody").getBoundingClientRect();
      const item = node.getBoundingClientRect();
      return item.top < body.bottom && item.bottom > body.top;
  });
  if (!baseline) {
    assert.equal(timelineVisible, true);
    assert.equal(highlighted && stopVisible, true);
    assert.equal(signedDay, "day-4");
    assert.equal(await stop.evaluate(node => document.activeElement === node), true);
    assert.deepEqual(overlaps, {credits: false, popupAction: false, actionOnScreen: true});
    await stop.locator("[data-plan-edit]").click();
    await page.getByRole("button", {name: "调整时间", exact: true}).click();
    await page.locator("#editView").getByRole("button", {name: "取消", exact: true}).click();
    assert.equal(await stop.evaluate(node => node.classList.contains("hl")), true);
    await page.locator("#mapFab").click();
    await mapDay.selectOption("overview");
    await page.waitForURL(url => !url.searchParams.has("day"));
    assert.ok((await page.locator("#map .pin").count()) > pinCount);
    await mapDay.selectOption("day-4");
    await page.locator("#mapFab").click();
    assert.equal(await timelineDay.inputValue(), "day-4");
  }
  return {width, daySwitchOnMap, selected, itemId, timelineVisible, highlighted, stopVisible, baseMapCoverageSamples,
          signedDay, weatherChanges, pinCount, overlaps};
}

async function desktop(page) {
  await page.setViewportSize({width: 1005, height: 773});
  await page.goto(origin + longTrip, {waitUntil: "domcontentloaded"});
  await page.locator(".current-revision").waitFor();
  await page.locator('#dayChips .on[data-sec="day-7"]').waitFor();
  assert.equal(await page.locator("#mapDaySelect").isVisible(), false);
  const marker = page.locator("#map .leaflet-marker-icon:has(.pin:not(.dim))").first();
  await marker.locator(".b").click();
  const focus = page.locator(".leaflet-popup .pp").getByRole("button", {name: "在行程中查看"});
  const id = await focus.getAttribute("data-pp-focus");
  await focus.click();
  const selected = page.locator(`.it.hl[data-uid="${id}"]`);
  await selected.waitFor();
  assert.equal(await page.locator("#mapPane").isVisible(), true);
  return {width: 1005, mapAndTimelineVisible: true, selectedStop: id};
}

async function run() {
  fs.mkdirSync(path.dirname(out), {recursive: true});
  const browser = await chromium.launch({headless: true, channel: "chrome"});
  const report = {mobile: [], errors: []};
  try {
    const page = await browser.newPage({
      viewport: {width: 390, height: 844}, deviceScaleFactor: 2, hasTouch: true, reducedMotion: "reduce"
    });
    page.on("pageerror", error => report.errors.push(error.message));
    await page.route("**/*", route => {
      const url = route.request().url();
      if (url.startsWith(origin + "/api/day-facts/weather?")) {
        const start = new URL(url).searchParams.get("start");
        const days = [[3,"多云",18],[4,"小雨",16]].map(([offset, day, dayTemp]) => ({
          date: new Date(Date.parse(start) + 86400000 * offset).toISOString().slice(0,10), day, dayTemp
        }));
        return route.fulfill({json:{status:"available", days}});
      }
      if (/^https:\/\/webrd0[1-4]\.is\.autonavi\.com\/appmaptile\?/.test(url)) return route.continue();
      if (url.startsWith(origin + "/")) return route.continue();
      return route.abort();
    });
    for (const width of baseline ? [390] : [360, 390, 430]) {
      report.mobile.push(await mobile(page, width));
    }
    if (!baseline) report.desktop = await desktop(page);
    assert.deepEqual(report.errors, []);
  } finally {
    fs.writeFileSync(out, JSON.stringify(report, null, 2));
    await browser.close();
  }
}
run().catch(error => { console.error(error); process.exitCode = 1; });
