function validFavoritePlace(value) {
  return typeof value?.name === "string" && value.name && typeof value.city === "string" &&
    Array.isArray(value.pos) && value.pos.length === 2 && value.pos.every(Number.isFinite);
}

function sameFavoritePlace(first, second) {
  return validFavoritePlace(first) && validFavoritePlace(second) &&
    first.city === second.city && first.name === second.name &&
    (!first.kind || !second.kind || first.kind === second.kind) &&
    Math.abs(first.pos[0] - second.pos[0]) < 0.00035 && Math.abs(first.pos[1] - second.pos[1]) < 0.00035;
}

function createBrowserHistory({panel, open, close, render, download, escape, toast, findJob}) {
  const sourcePorts = ["8791", "8797", "8805"];
  const keys = {favorites:"st.favs", jobs:"st.ui.planningJobs",
    links:"st.ui.importedLinks", copies:"st.ui.importedCopies", batch:"st.ui.historyImportBatch"};
  const idPattern = /^[a-f0-9]{32}$/;
  const candidatePattern = /^[a-f0-9]{24}$/;
  const parse = (raw, fallback) => {
    try { return JSON.parse(raw) ?? fallback; }
    catch { return fallback; }
  };
  const items = (value) => Array.isArray(value) ? value : [];
  const records = (value) => value && typeof value === "object" && !Array.isArray(value) ? Object.entries(value) : [];
  const same = (first, second) => JSON.stringify(first) === JSON.stringify(second);
  const readList = key => items(parse(localStorage.getItem(key), []));
  const readBatch = () => parse(localStorage.getItem(keys.batch), null);
  const undoButton = panel.querySelector("[data-history-undo]");
  const copiesButton = panel.querySelector("[data-history-copies]");
  let sourceWindow, sourceOrigin;

  const linkParams = entry => {
    const query = new URLSearchParams({job:entry.job, request:entry.request});
    for (const key of ["candidate", "revision", "day", "preview"]) {
      if (entry[key] != null) query.set(key, entry[key]);
    }
    return query;
  };
  const linkKey = entry => linkParams(entry).toString();

  function readLink(raw) {
    if (!idPattern.test(raw?.job || "") || !idPattern.test(raw?.request || "") ||
        typeof raw.title !== "string") return null;
    const entry = {job:raw.job, request:raw.request, title:raw.title.slice(0,180)};
    if (candidatePattern.test(raw.candidate || "")) entry.candidate = raw.candidate;
    if (entry.candidate && idPattern.test(raw.revision || "")) entry.revision = raw.revision;
    const day = Number(raw.day);
    if (Number.isInteger(day) && day > 0 && day <= 30) entry.day = day;
    if (/^attempt_[1-9][0-9]*$/.test(raw.preview || "")) entry.preview = raw.preview;
    return entry;
  }

  function bookingInfo(key) {
    if (typeof key !== "string" || !key.startsWith("st.booking:") || key.length > 1600) return null;
    const scope = parse(key.slice("st.booking:".length), null);
    if (!Array.isArray(scope) || scope.length !== 12 ||
        !scope.slice(0,4).every(value => typeof value === "string" && value.length)) return null;
    const [id, city, name, kind, code, start] = scope;
    return {key, label:[city,name,code,start].filter(Boolean).join(" · "), id, kind};
  }

  function refresh() {
    undoButton.hidden = !readBatch();
    copiesButton.hidden = !readList(keys.copies).length;
    render(readList(keys.favorites));
  }

  // Prepare the undo record together with the data. On a storage error restore completed writes.
  function write(changes) {
    const completed = [];
    try {
      for (const [key, value] of changes) {
        const before = localStorage.getItem(key);
        if (value === null) localStorage.removeItem(key);
        else localStorage.setItem(key, value);
        completed.push([key, before]);
      }
    } catch (error) {
      for (const [key, before] of completed.reverse()) {
        if (before === null) localStorage.removeItem(key);
        else localStorage.setItem(key, before);
      }
      throw error;
    }
  }
  const lock = task => navigator.locks ? navigator.locks.request("shorttrip-history-import", task) : Promise.resolve().then(task);
  const additions = (before, incoming, exists) => {
    const next = [...before];
    for (const item of incoming) if (!next.some(saved => exists(saved,item))) next.push(item);
    return next.slice(before.length);
  };

  function plan(data) {
    const previous = Object.fromEntries(Object.entries(keys).filter(([name]) => name !== "batch")
      .map(([name,key]) => [name,readList(key)]));
    const favorites = additions(previous.favorites, data.favorites, sameFavoritePlace);
    const jobs = [];
    const links = [];
    const current = new Map(previous.jobs.map(entry => [entry.job,entry]));
    const knownLinks = new Set([...previous.jobs, ...previous.links].map(linkKey));
    for (const entry of data.jobs) {
      const key = linkKey(entry);
      if (knownLinks.has(key)) continue;
      knownLinks.add(key);
      if (current.has(entry.job)) {
        if (entry.candidate || entry.revision || entry.day || entry.preview) links.push(entry);
      } else {
        jobs.push(entry);
        current.set(entry.job,entry);
      }
    }
    const copies = additions(previous.copies, data.copies, same);
    const chat = additions([...previous.copies, ...copies], data.chat, same);
    const booking = data.booking.filter(({key}) => localStorage.getItem(key) === null);
    return {previous, favorites, jobs, links, copies, chat, booking};
  }

  async function preview(data, port) {
    const source = {favorites:items(data.favorites).filter(validFavoritePlace), jobs:items(data.jobs).map(readLink).filter(Boolean),
      booking:[...new Set(items(data.booking))].map(bookingInfo).filter(Boolean), copies:[], chat:[]};
    for (const [kind, entries] of [["草稿", items(data.drafts).map(value => [value?.id,value])],
      ["示例修改",records(data.edits)]]) {
      for (const [id,value] of entries) {
        if (typeof id === "string" && value && typeof value === "object") {
          source.copies.push({port, kind, id, title:value.title || value.versions?.[0]?.title || id, value});
        }
      }
    }
    const chat = new Map(records(data.chat).filter(([,messages]) => items(messages).length > 0));
    const related = source.jobs.filter(entry => chat.has("personalized-" + entry.job));
    for (const entry of new Map(related.map(item => [item.job,item])).values()) {
      const group = await findJob(entry.job);
      if (group?.id === "personalized-" + entry.job) {
        const value = chat.get(group.id).filter(message => typeof message?.text === "string" && !message.pending)
          .map(({role,text}) => ({role,text}));
        if (value.length) source.chat.push({port, kind:"旧对话", id:group.id, title:group.title, value});
      }
    }
    const batch = plan(source);
    const bookingText = batch.booking.length ? '<fieldset><legend>预订标记（请按原日期核对）</legend>' +
      batch.booking.map((entry,index) => `<label><input type="checkbox" name="booking" value="${index}"> ${escape(entry.label)}</label>`).join("") + "</fieldset>" : "";
    open(`<h3>预览从 ${port} 迁入</h3>
      <p>新增 ${batch.favorites.length} 个收藏、${batch.jobs.length} 条最近行程、${batch.links.length} 条旧版本链接、${batch.copies.length} 份草稿或示例修改。</p>
      ${batch.links.length ? '<ul>' + batch.links.map(entry => `<li><a href="?${escape(linkKey(entry))}" target="_blank" rel="noopener">${escape(entry.title)}${entry.day ? " · 第 " + entry.day + " 天" : ""}</a></li>`).join("") + '</ul>' : ""}
      <form id="historyImportForm" class="history-import">
      ${bookingText}
      ${batch.chat.length ? `<label><input type="checkbox" name="chat"> 另存 ${batch.chat.length} 份旧对话供下载</label>` : ""}
      <p id="historyImportStatus" role="status"></p>
      <div class="btns"><button type="button" data-close>取消</button><button class="pri">确认迁入</button></div></form>`);
    const form = document.querySelector("#historyImportForm");
    form.onsubmit = async event => {
      event.preventDefault();
      const submit = form.querySelector("button.pri");
      submit.disabled = true;
      const chosen = new FormData(form);
      const approved = new Set(chosen.getAll("booking").map(index => batch.booking[index].key));
      try {
        const count = await lock(() => {
          const latest = plan(source);
          const incoming = {favorites:latest.favorites, jobs:latest.jobs, links:latest.links,
            copies:latest.copies.concat(chosen.has("chat") ? latest.chat : [])};
          const booking = latest.booking.map(entry => entry.key).filter(key => approved.has(key));
          const count = Object.values(incoming).reduce((n, rows) => n + rows.length, booking.length);
          if (count === 0) return 0;
          const changes = Object.entries(incoming).filter(([,rows]) => rows.length)
            .map(([name,rows]) => [keys[name],JSON.stringify(latest.previous[name].concat(rows))]);
          write([[keys.batch,JSON.stringify({port,incoming,booking})], ...changes, ...booking.map(key => [key,"yes"])]);
          return count;
        });
        close();
        refresh();
        toast(count ? `已迁入 ${count} 项旧版浏览器记录。` : "没有新增记录。");
      } catch {
        form.querySelector("#historyImportStatus").textContent = "迁入未完成。请检查浏览器存储空间后重试。";
      } finally { submit.disabled = false; }
    };
  }

  window.addEventListener("message", event => {
    if (!sourceWindow || event.source !== sourceWindow || event.origin !== sourceOrigin ||
        event.data?.type !== "shorttrip-history") return;
    const port = new URL(event.origin).port;
    sourceWindow.close();
    sourceWindow = null;
    void preview(event.data,port).catch(() => toast("无法读取迁移预览，请重试。"));
  });
  panel.onclick = event => {
    const port = event.target.closest("[data-history-port]")?.dataset.historyPort;
    if (!sourcePorts.includes(port)) return;
    sourceOrigin = "http://127.0.0.1:" + port;
    sourceWindow = window.open(sourceOrigin + "/transfer-shorttrip.html", "shorttrip-history");
    if (!sourceWindow) toast("请允许此站点打开迁移窗口。");
  };
  undoButton.onclick = () => {
    const expected = localStorage.getItem(keys.batch);
    open(`<h3>撤销最近一次迁入</h3><p>移除本次新增且此后未改动的记录。</p>
      <div class="btns"><button data-close>取消</button><button id="historyUndo" class="pri">撤销迁入</button></div>`);
    document.querySelector("#historyUndo").onclick = async () => {
      try {
        await lock(() => {
          const batch = readBatch();
          if (!batch) return;
          if (localStorage.getItem(keys.batch) !== expected) throw new Error("batch changed");
          const changes = Object.entries(batch.incoming).filter(([,rows]) => rows.length)
            .map(([name,rows]) => [keys[name],JSON.stringify(readList(keys[name]).filter(item =>
              !rows.some(imported => same(imported,item))))]);
          const booking = batch.booking.filter(key => localStorage.getItem(key) === "yes");
          write([...changes, ...booking.map(key => [key,null]), [keys.batch,null]]);
        });
        close();
        refresh();
        toast("已撤销最近一次迁入。");
      } catch { toast("撤销未完成，请重试。"); }
    };
  };
  document.addEventListener("shorttrip-booking-changed", event => {
    const batch = readBatch();
    if (batch?.booking.includes(event.detail)) {
      batch.booking = batch.booking.filter(key => key !== event.detail);
      localStorage.setItem(keys.batch, JSON.stringify(batch));
    }
  });
  copiesButton.onclick = () => {
    const copies = readList(keys.copies);
    open(`<h3 id="historyCopies">旧版草稿与对话</h3><p>这些副本可下载，供你核对当时的安排。</p><ul>${copies.map((item,index) =>
      `<li><button type="button" class="edit-link" data-history-copy="${index}">${escape(item.title)} · ${escape(item.kind)} · ${escape(item.port)} ↓</button></li>`).join("")}</ul>
      <div class="btns"><button data-close>关闭</button></div>`);
    for (const button of document.querySelectorAll("[data-history-copy]")) {
      button.onclick = () => download("shorttrip-旧版记录.json",copies[button.dataset.historyCopy]);
    }
  };
  undoButton.hidden = !readBatch();
  copiesButton.hidden = !readList(keys.copies).length;
  return {links:() => readList(keys.links), linkParams};
}
