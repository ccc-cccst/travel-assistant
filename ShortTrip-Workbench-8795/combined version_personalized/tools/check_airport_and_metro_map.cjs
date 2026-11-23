// Check the city-side terminal labels and the Shanghai subway transfer on 8795.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {chromium} = require(process.env.SHORTTRIP_PLAYWRIGHT || "playwright");
const origin = "http://127.0.0.1:8795";
const trip = origin + "/?job=c65c872e0b0e4120b8570a80e08e96a4&candidate=597355ee4d2364e93db90af5";
const endpoint = url => url.pathname === "/api/route" && url.searchParams.get("mode") === "subway" &&
  url.searchParams.get("from") === "31.141263,121.667959" && url.searchParams.get("to") === "31.238532,121.512412";

async function settled(page) {
  await page.locator("#routeMapDrawn").waitFor();
  await page.waitForFunction(() => document.querySelector("#routeMapLoading").hidden);
}

async function run() {
  const out = process.argv[2];
  fs.mkdirSync(path.dirname(out), {recursive: true});
  const browser = await chromium.launch({headless: true, channel: "chrome"});
  const results = {};
  const knownRoutes = new Map();
  const errors = [];
  async function open(alter) {
    const page = await browser.newPage({viewport: {width: 1066, height: 773}, reducedMotion: "reduce"});
    page.on("pageerror", error => errors.push(error.message));
    await page.route("**/*", async route => {
      const url = new URL(route.request().url());
      if (url.origin === origin) {
        if (url.pathname !== "/api/route") return route.continue();
        if (alter && knownRoutes.has(url.href)) {
          const body = structuredClone(knownRoutes.get(url.href));
          if (endpoint(url)) alter(body);
          return route.fulfill({json: body});
        }
        const response = await route.fetch({timeout: 26000});
        if (response.ok()) knownRoutes.set(url.href, await response.json());
        return route.fulfill({response});
      }
      if (/^webrd0[1-4]\.is\.autonavi\.com$/.test(url.hostname)) return route.continue();
      return route.abort();
    });
    await page.goto(trip + "&day=1", {waitUntil: "domcontentloaded"});
    return page;
  }
  try {
    const page = await open();
    const airport = page.locator('#map .leaflet-marker-icon[title="上海浦东国际机场"]:has(.pin:not(.dim))');
    await airport.waitFor();
    assert.equal(await airport.locator(".plabel").innerText(), "上海浦东国际机场");
    assert.match(await page.locator("#sec-day-1").innerText(), /北京首都国际机场\s*→\s*上海浦东国际机场/);
    await settled(page);
    const actual = [...knownRoutes.entries()].find(([url]) => endpoint(new URL(url)))?.[1];
    assert.equal(actual?.source, "amap");
    assert.ok(actual.segments.filter(s => s.type === "subway").length > 1);
    assert.equal(await page.locator("#routeMapMissing").isHidden(), true);
    assert.equal(await page.locator("#map .unverified-route-line").count(), 0);
    results.day1 = {
      terminal: await airport.getAttribute("title"), summary: await page.locator("#routeKey").innerText(),
      referenceParts: actual.segments.map(({type,name,from,to}) => ({type,name,from,to}))
    };
    await page.screenshot({path:path.join(path.dirname(out), "day-1.png")});
    await airport.locator(".b").click();
    const popup = page.locator(".leaflet-popup .pp");
    assert.equal(await popup.locator("h4").innerText(), "上海浦东国际机场");
    assert.match(await popup.innerText(), /抵达机场/);
    await popup.getByRole("button", {name:"在行程中查看"}).click();
    await page.locator("#sec-day-1 .it.hl").waitFor();
    await page.goto(trip + "&day=4", {waitUntil: "domcontentloaded"});
    const returnAirport = page.locator('#map .leaflet-marker-icon[title="上海虹桥国际机场"]:has(.pin:not(.dim))');
    await returnAirport.waitFor();
    results.day4 = {terminal: await returnAirport.getAttribute("title")};
    assert.match(await page.locator("#sec-day-4").innerText(), /上海虹桥国际机场\s*→\s*北京首都国际机场/);
    await page.unrouteAll({behavior: "ignoreErrors"});
    await page.close();
    for (const [name, alter] of Object.entries({
      differentStation(body) {
        body.segments.at(-1).to = "其他车站";
      },
      missingTransfer(body) {
        body.segments.find(s => s.type === "walk").path = [];
      }
    })) {
      const attempt = await open(alter);
      await settled(attempt);
      const gap = attempt.locator("#routeMapMissing");
      assert.equal(await gap.innerText(), "1 段缺少线路图");
      assert.match(await gap.getAttribute("title"), /迪士尼-地铁站 → 浦东南路-地铁站/);
      assert.equal(await attempt.locator("#map .unverified-route-line").count(), 1);
      results[name] = {summary: await gap.innerText(), detail: await gap.getAttribute("title")};
      await attempt.unrouteAll({behavior: "ignoreErrors"});
      await attempt.close();
    }
    assert.deepEqual(errors, []);
    fs.writeFileSync(out, JSON.stringify(results, null, 2));
  } finally {
    await browser.close();
  }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
