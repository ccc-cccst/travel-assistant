/*
 * ShortTrip 前端 ⇄ 后端 适配层（唯一的“坑位”文件）。
 *
 * 页面（app.js）只调用这里的 window.API.xxx()，只认这里返回的“视图模型”（字段说明见 docs/webui-api.md）。
 * 两种模式：
 *   - mock（默认）：数据来自 data/trips.js（export_plan.py 生成），编辑存在浏览器 localStorage，规划/对话是假的
 *   - http：网址加 ?api=http://127.0.0.1:8000 ，每个函数改为请求下面注释里的接口
 * 接后端时：要么按 docs/webui-api.md 实现这些 HTTP 接口（推荐，前端零改动），要么只改本文件。
 */
(function () {
  "use strict";
  const params = new URLSearchParams(location.search);
  // ?api=地址 指定后端；由后端（webui/backend/server.py）直接提供网页时，config.js 会设置 SHORTTRIP_API="" 表示同源
  const RAW_BASE = params.get("api") != null ? params.get("api") : (window.SHORTTRIP_API ?? null);
  const BASE = (RAW_BASE || "").replace(/\/$/, "");
  const MODE = RAW_BASE !== null ? "http" : "mock";
  const LLM = { url: (params.get("llm") || "http://127.0.0.1:18080") + "/v1/chat/completions", model: params.get("model") || "qwen3.5-4b-q8" };

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  let uidSeq = 0;
  const uid = (p = "i") => `${p}${Date.now().toString(36)}${(uidSeq++).toString(36)}`;

  async function http(method, path, body, signal) {
    const r = await fetch(BASE + path, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined, signal });
    if (!r.ok) {
      const detail = await r.json().catch(() => ({}));
      const error = new Error(detail.message || detail.error || `请求未完成（HTTP ${r.status}）`);
      error.status = r.status;
      throw error;
    }
    return r.status === 204 ? null : r.json();
  }

  // ---------------------------------------------------------------------------
  // ChinaTravel / ShortTrip 行程 JSON（经 export_plan.py 加坐标后）→ 视图模型 Trip
  // 后端如果直接返回视图模型，就不需要这段；如果返回原始 plan，可在后端或这里调用 fromChinaTravel。
  // ---------------------------------------------------------------------------
  const KIND_OF = { attraction: "attraction", breakfast: "restaurant", lunch: "restaurant", dinner: "restaurant", accommodation: "hotel", train: "station", airplane: "airport" };

  function parseRange(s) {
    if (!s) return null;
    const m = String(s).match(/([\d.]+)\s*-\s*([\d.]+)/);
    return m ? { min: +m[1], max: +m[2] } : null;
  }
  const toMin = (t) => { const [h, m] = String(t || "0:0").split(":").map(Number); return h * 60 + (m || 0); };

  function legFrom(transports) {
    if (!transports || !transports.length) return null;
    const segments = transports.map((s) => ({
      id: s._id || null, mode: s.mode, from: s.start, to: s.end, fromPos: s._from || null, toPos: s._to || null,
      start: s.start_time, end: s.end_time,
      minutes: Math.max(0, toMin(s.end_time) - toMin(s.start_time)), km: s.distance ?? null, cost: s.cost ?? 0,
      path: s._path || null, // 沿路折线 [[lat,lng],...]，后端接了高德/百度路径规划后填；没有就画直线
    }));
    const main = segments.reduce((a, b) => ((b.km || 0) > (a.km || 0) ? b : a), segments[0]);
    return {
      mode: main.mode,
      minutes: segments.reduce((s, x) => s + x.minutes, 0),
      km: segments.reduce((s, x) => s + (x.km || 0), 0),
      cost: segments.reduce((s, x) => s + (x.cost || 0), 0),
      segments,
    };
  }

  function itemFrom(a) {
    const info = a._info || {};
    const kind = a._kind === "station" && a.type === "airplane" ? "airport" : (KIND_OF[a.type] || a._kind || "other");
    const isMove = a.type === "train" || a.type === "airplane";
    return {
      uid: a._uid || uid(),
      poiId: a._poi_id || null,
      lock: a._lock || null,
      terminals: a._terminals || null,
      kind,
      type: a.type,                         // 原始活动类型：attraction/breakfast/lunch/dinner/accommodation/train/airplane
      name: a._title || a.position || "",   // 显示名
      place: a._place || a.position || "",  // 数据库里的地点名
      pos: a._pos || null,                  // [lat, lng]
      start: a.start_time || null, end: a.end_time || null,
      price: a.price ?? null,               // 单价（门票/人均/房价/票价）
      cost: a.cost ?? null,                 // 该活动总花费（不含交通）
      tickets: a.tickets ?? null,
      code: isMove ? (a.TrainID || a.FlightID || "") : null,
      duration: parseRange(info["建议游玩(小时)"]),            // {min,max} 小时
      hours: info["开放"] || info["营业"] || null,             // "07:30-17:00"
      category: info["类型"] || info["菜系"] || null,
      dishes: String(info["推荐菜"] || "").split(/[,，]/).map(s => s.trim()).filter(s => s && !/^(unknown|nan)$/i.test(s)),
      image: a._image || null,              // 坑：图片 URL
      rating: a._rating ?? null,            // 坑：评分
      note: a._note || "",
      leg: legFrom(a.transports),           // 到达这一项的交通
      raw: a,                               // 原始活动，保存/回传后端时用
    };
  }

  function guideMarkdown(plan) {
    // 坑：真正的“旅行导览”应由后端（LLM）生成，返回 markdown，地名用 [[名称]] 标出即可变成可点击链接
    const spots = [], food = [];
    plan.itinerary.forEach((d) => d.activities.forEach((a) => {
      if (a.type === "attraction" && !spots.includes(a._title)) spots.push(a._title);
      if (["breakfast", "lunch", "dinner"].includes(a.type) && !food.includes(a._title)) food.push(a._title);
    }));
    const L = [];
    L.push(`# ✈️ 旅行导览`);
    L.push(`## 📍 目的地印象`);
    L.push(`**${plan.target_city}** 这次安排了 ${plan.itinerary.length} 天、${plan.people_number || 1} 人，预计总花费约 ¥${Math.round(plan._total_cost || 0)}。（这里是示例文字，接后端后由模型生成目的地介绍。）`);
    if (spots.length) {
      L.push(`## 🎯 必玩体验`);
      spots.slice(0, 5).forEach((s) => L.push(`- 去 [[${s}]] 走走，建议预留充足时间。`));
    }
    if (food.length) {
      L.push(`## 🍜 味蕾印象`);
      food.slice(0, 4).forEach((s) => L.push(`- [[${s}]]，行程里已安排。`));
    }
    L.push(`## 🗓️ 行程安排`);
    plan.itinerary.forEach((d) => L.push(`- **第${d.day}天**：${d.activities.filter((a) => a.type === "attraction").map((a) => `[[${a._title}]]`).join("、") || "以交通为主"}`));
    return L.join("\n");
  }

  function fromChinaTravel(t) {
    const p = t.plan;
    return {
      id: t.id || uid("t"),
      title: t.title || `${p.target_city}${p.itinerary.length}日游`,
      query: t.query || "",
      startCity: p.start_city || "",
      city: p.target_city || "",
      people: p.people_number || 1,
      startDate: t.startDate || null,     // "2026-12-01"，用户在顶栏设置
      totalCost: p._total_cost ?? null,
      missing: p._missing || [],
      days: p.itinerary.map((d) => ({ day: d.day, title: null, note: "", items: d.activities.map(itemFrom) })),
      pending: [],                        // 待安排
      note: "",                           // 备注
      archive: t.archive || null,
      revision: t.revision || null,
      guide: t.archive ? "" : t.guide || guideMarkdown(p), // 左栏 AI 回复（markdown，[[地名]] 可点）
      thinking: t.thinking || (MODE === "mock" ? "这份行程来自随包样例。价格、路线和开放时间均为参考。" : ""),
      raw: p,
    };
  }

  // 同标题的多个 plan = 同一段对话的多个版本
  function groupTrips(list) {
    const groups = [];
    list.forEach((t) => {
      let g = groups.find((x) => x.title === t.title);
      if (!g) groups.push((g = { id: t.id, title: t.title, versions: [] }));
      g.versions.push(t);
    });
    return groups;
  }
  function verifiedGroup(group) {
    let alternative = 0;
    const versions = group.versions.map(entry => {
      const trip = fromChinaTravel(entry);
      trip.archive.label = trip.archive.alternative ? "备选 " + ++alternative : "首份可用";
      return trip;
    });
    return {...group, versions};
  }

  // ---------------------------------------------------------------------------
  // mock 存储
  // ---------------------------------------------------------------------------
  const LS = {
    get(k, d) { try { const v = localStorage.getItem("st." + k); return v ? JSON.parse(v) : d; } catch { return d; } },
    set(k, v) { try { localStorage.setItem("st." + k, JSON.stringify(v)); } catch { /* 无痕模式等 */ } },
  };
  const jobs = {};

  function haversine(a, b) {
    const R = 6371, r = Math.PI / 180;
    const dLat = (b[0] - a[0]) * r, dLon = (b[1] - a[1]) * r;
    const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[0] * r) * Math.cos(b[0] * r) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(h));
  }

  // ---------------------------------------------------------------------------
  // 对外接口
  // ---------------------------------------------------------------------------
  const API = {
    mode: MODE,
    canLookupRoute: MODE === "http" || window.SHORTTRIP_ROUTE_LOOKUP === true,
    haversine,
    fromChinaTravel,
    hasArchive: window.SHORTTRIP_ARCHIVE === true,
    hasPersonalized: window.SHORTTRIP_PERSONALIZED === true,
    personalized(name, payload, query, signal) {
      return http(payload ? "POST" : "GET", "/api/personalized/" + name +
        (query ? "?" + new URLSearchParams(query) : ""), payload, signal);
    },
    itineraryEdit(name, payload, query, signal) {
      return http(payload ? "POST" : "GET", "/api/itinerary-edits/" + name +
        (query ? "?" + new URLSearchParams(query) : ""), payload, signal);
    },
    dayFacts(kind, query, signal) {
      return http("GET", "/api/day-facts/" + kind + "?" + new URLSearchParams(query), null, signal);
    },
    verifiedGroup,

    async getArchivedTrip() {
      return verifiedGroup(await http("GET", "/api/archived-trip"));
    },
    async getArchivedTrips() {
      return (await http("GET", "/api/archived-trips")).map(verifiedGroup);
    },

    /** 行程列表（含各版本）。GET /api/trips → TripGroup[] */
    async listTrips() {
      if (MODE === "http") {
        // 后端可以存原始行程（export_plan.load_trip 的结构）或视图模型，这里统一成视图模型
        const groups = await http("GET", "/api/trips");
        return groups.map((g) => ({ ...g, versions: g.versions.map((v) => (v.days ? v : fromChinaTravel(v))) }));
      }
      const base = groupTrips((window.TRIPS || []).map(fromChinaTravel));
      const saved = LS.get("edits", {});
      base.forEach((g) => g.versions.forEach((v, i) => { if (saved[v.id]) g.versions[i] = saved[v.id]; }));
      return base.concat(LS.get("created", []));
    },

    /** 保存用户在页面上的编辑（排序/删除/备注/日期/标题…）。PUT /api/trips/:id → Trip（后端可重算时间、费用后返回） */
    async saveTrip(trip) {
      if (MODE === "http") return http("PUT", `/api/trips/${encodeURIComponent(trip.id)}`, trip);
      const saved = LS.get("edits", {}); saved[trip.id] = trip; LS.set("edits", saved);
      return trip;
    },

    /** 新建规划（异步）。POST /api/plan {text, startCity?, city?, days?, people?} → {jobId} */
    async createPlan(req) {
      if (MODE === "http") return http("POST", "/api/plan", req);
      const id = uid("job"); jobs[id] = { t0: Date.now(), req, kind: "create" }; return { jobId: id };
    },

    /** 在当前行程基础上按一句话修改（异步）。POST /api/trips/:id/revise {instruction} → {jobId} */
    async revisePlan(trip, instruction) {
      if (MODE === "http") return http("POST", `/api/trips/${encodeURIComponent(trip.id)}/revise`, { instruction });
      const id = uid("job"); jobs[id] = { t0: Date.now(), trip, instruction, kind: "revise" }; return { jobId: id };
    },

    /**
     * 查询任务。GET /api/jobs/:id →
     *   {status: "queued"|"running"|"done"|"failed", progress: {step, steps:[...], message}, result?: Trip, error?}
     */
    async getJob(jobId) {
      if (MODE === "http") {
        const j = await http("GET", `/api/jobs/${encodeURIComponent(jobId)}`);
        if (j.result && !j.result.days) j.result = fromChinaTravel(j.result);
        return j;
      }
      const j = jobs[jobId];
      const steps = ["读取随包行程", "显示时间线和地图"];
      if (Date.now() - j.t0 < 400) return { status: "running", progress: {step: 0, steps, message: steps[0]} };
      // mock：没有真的规划器，返回一份现成行程的副本
      const src = j.trip || (window.TRIPS && window.TRIPS.length ? fromChinaTravel(window.TRIPS[0]) : null);
      if (!src) return { status: "failed", error: "mock 模式下没有样例数据" };
      const copy = JSON.parse(JSON.stringify(src));
      copy.id = uid("t");
      copy.query = j.kind === "revise" ? j.instruction : j.req.text;
      copy.guide = (j.kind === "revise" ? `已按「${j.instruction}」调整（mock：内容未真正变化，接入规划后端后生效）。\n\n` : "") + copy.guide;
      if (j.kind === "create") { const c = LS.get("created", []); c.push({ id: copy.id, title: copy.title + "（新）", versions: [copy] }); LS.set("created", c); copy.title += "（新）"; }
      return { status: "done", progress: { step: steps.length, steps }, result: copy };
    },

    /**
     * 对话（流式）。POST /api/chat {tripId, messages:[{role,content}]} → text/event-stream（data: {"delta":"..."}）或 JSON {text, intent?}
     * intent === "revise" 时前端会转去 revisePlan。mock 模式直连 llama-server。
     */
    async chat({ trip, messages }, onDelta) {
      if (MODE === "http") {
        const r = await fetch(BASE + "/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ tripId: trip && trip.id, messages }) });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        if ((r.headers.get("content-type") || "").includes("event-stream")) return readSSE(r, onDelta);
        const j = await r.json(); onDelta && onDelta(j.text); return j.text;
      }
      const sys = trip ? `你是旅行规划助手。用户当前行程（${trip.startCity}→${trip.city}，${trip.people}人）：\n` +
        trip.days.map((d) => `第${d.day}天：` + d.items.map((x) => `${x.start || ""} ${x.name}`).join("；")).join("\n") +
        `\n回答简短具体；提到行程里的地点时用 [[地点原名]] 标出。` : "你是旅行规划助手。";
      const r = await fetch(LLM.url, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: LLM.model, temperature: 0.3, max_tokens: 700, stream: true, messages: [{ role: "system", content: sys }, ...messages] }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return readSSE(r, onDelta, (j) => j.choices?.[0]?.delta?.content || "");
    },

    /** 顺路优化某一天。POST /api/optimize {items} → Item[]（新顺序，可带重算后的时间/交通） */
    async optimizeDay(trip, dayIdx) {
      if (MODE === "http") return http("POST", "/api/optimize", { items: trip.days[dayIdx].items });
      // mock：首尾（车站/酒店）不动，中间按最近邻排序
      const items = trip.days[dayIdx].items.slice();
      const fixed = (x) => ["station", "airport", "hotel"].includes(x.kind);
      const head = [], tail = [];
      while (items.length && fixed(items[0])) head.push(items.shift());
      while (items.length && fixed(items[items.length - 1])) tail.unshift(items.pop());
      const out = []; let cur = head.length ? head[head.length - 1].pos : null;
      const rest = items.filter((x) => x.pos), nopos = items.filter((x) => !x.pos);
      while (rest.length) {
        let bi = 0;
        if (cur) rest.forEach((x, i) => { if (haversine(cur, x.pos) < haversine(cur, rest[bi].pos)) bi = i; });
        const [x] = rest.splice(bi, 1); out.push(x); cur = x.pos;
      }
      await sleep(400);
      return head.concat(out, nopos, tail);
    },

    /** 某一项的替换候选。POST /api/alternatives {city, item} → Poi[] */
    async getAlternatives(trip, item) {
      if (MODE === "http") return http("POST", "/api/alternatives", { city: trip.city, item: { kind: item.kind, pos: item.pos, place: item.place, name: item.name } });
      const pool = await API.getPois({ city: trip.city, kind: item.kind === "restaurant" ? "restaurant" : item.kind === "hotel" ? "hotel" : "attraction" });
      if (!item.pos) return pool.slice(0, 6);
      return pool.filter((p) => p.name !== item.place).sort((a, b) => haversine(item.pos, a.pos) - haversine(item.pos, b.pos)).slice(0, 6);
    },

    /** 地图筛选：某城市某类地点。GET /api/pois?city=&kind=&bbox= → Poi[]（kind: attraction/hotel/restaurant/shopping/airport/station） */
    async getPois({ city, kind, bbox, snapshot }) {
      if (MODE === "http" || snapshot) {
        const query = new URLSearchParams({city, kind});
        if (bbox) query.set("bbox", bbox.join(","));
        if (snapshot) { query.set("commit", snapshot.commit); query.set("profile", snapshot.profile); }
        return http("GET", "/api/pois?" + query);
      }
      return ((window.POIS || {})[city] || {})[kind] || [];
    },

    /**
     * 两点之间的真实路线（高德路径规划）。GET /api/route?from=lat,lng&to=lat,lng&mode=car|transit|walk&city=
     * → Route（见 docs/webui-api.md）；mock 模式返回 null，页面用估算
     */
    async getRoute({ from, to, mode, city, signal, refresh = false, departure }) {
      if (!API.canLookupRoute) return null;
      const query = new URLSearchParams({from: from.join(","), to: to.join(","), mode, city: city || "", reference: "1"});
      if (refresh) query.set("refresh", "1");
      if (departure) query.set("departure", departure);
      return http("GET", "/api/route?" + query, undefined, signal);
    },
    /** 批量路线。POST /api/routes {city, legs:[{from,to,mode}]} → Route[] */
    async getRoutes(city, legs) {
      if (MODE !== "http" || !legs.length) return legs.map(() => null);
      return http("POST", "/api/routes", { city, legs });
    },

    /** 收藏。GET/PUT /api/favorites → Poi[] */
    async getFavorites() { return MODE === "http" ? http("GET", "/api/favorites") : LS.get("favs", []); },
    async setFavorites(list) { return MODE === "http" ? http("PUT", "/api/favorites", list) : LS.set("favs", list); },

    /** 预订管理。GET /api/trips/:id/bookings → [{itemUid, name, kind, status:"unbooked"|"booked", url?}] */
    async getBookings(trip) {
      // 预订系统还没有，后端实现 GET /api/trips/:id/bookings 后把下一行打开
      // if (MODE === "http") return http("GET", `/api/trips/${encodeURIComponent(trip.id)}/bookings`);
      const out = [];
      trip.days.forEach((d) => d.items.forEach((x) => {
        if (["hotel", "station", "airport"].includes(x.kind) || (x.kind === "attraction" && x.price > 0))
          out.push({ itemUid: x.uid, day: d.day, name: x.name, kind: x.kind, code: x.code, start: x.start, price: x.price, status: "unbooked", url: null });
      }));
      return out;
    },

    /** 满意度调研。POST /api/feedback {score, text, tripId} */
    async submitFeedback(fb) { if (MODE === "http") return http("POST", "/api/feedback", fb); console.info("[feedback]", fb); await sleep(200); return { ok: true }; },
  };

  async function readSSE(r, onDelta, pick = (j) => j.delta || "") {
    const reader = r.body.getReader(), dec = new TextDecoder();
    let buf = "", text = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const lines = buf.split("\n"); buf = lines.pop();
      for (const line of lines) {
        const s = line.trim();
        if (!s.startsWith("data:")) continue;
        const data = s.slice(5).trim();
        if (data === "[DONE]") continue;
        try { const d = pick(JSON.parse(data)); if (d) { text += d; onDelta && onDelta(text); } } catch { /* 忽略半行 */ }
      }
    }
    return text;
  }

  window.API = API;
})();
