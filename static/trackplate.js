"use strict";

const get = (id) => document.getElementById(id);
const state = {card: null, result: null, photo: null, probe: null, mode: "probe", busy: false,
  cards: [], compare: null, compareOtherId: "", oldPhoto: null, compareFilter: "change", selectedIdx: null};
const settings = [...document.querySelectorAll("[data-setting]")];

const COMPARE_META = [
  {key: "new", zh: "新印", color: "#2ea043"},
  {key: "gone", zh: "消失", color: "#e03131"},
  {key: "both", zh: "都在", color: "#2f4bdd"},
  {key: "blank", zh: "都空", color: "#a7b0bd"},
  {key: "outside", zh: "缺拍", color: "#cfc4aa"},
];
const compareColor = Object.fromEntries(COMPARE_META.map((item) => [item.key, item.color]));
const compareZh = Object.fromEntries(COMPARE_META.map((item) => [item.key, item.zh]));

async function api(path, options) {
  const response = await fetch("/trackplate" + path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "请求失败，请检查输入后重试。");
  return body;
}

function route(tail) {
  return "/cards/" + state.card.id + tail;
}

function savedQuery() {
  return "?revision=" + state.card.revision;
}

function notice(message, target = "notice") {
  const element = get(target);
  element.textContent = message;
  element.hidden = !message;
}

async function action(work, target = "notice") {
  if (state.busy) return;
  state.busy = true;
  document.querySelectorAll("button, select").forEach((node) => { node.disabled = true; });
  notice("", target);
  try {
    await work();
  } catch (error) {
    notice(error.message, target);
  } finally {
    state.busy = false;
    document.querySelectorAll("button, select").forEach((node) => { node.disabled = false; });
  }
}

async function library() {
  const {cards} = await api("/cards");
  state.cards = cards;
  const select = get("library");
  select.replaceChildren(new Option("选取已存卡片", ""));
  for (const card of cards) select.append(new Option(card.name, card.id));
  select.value = state.card?.id || "";
  syncCompareOptions();
}

function syncCompareOptions() {
  const select = get("compare-other");
  if (!select) return;
  const previous = state.compareOtherId || select.value;
  select.replaceChildren(new Option("挑选旧照…", ""));
  for (const row of state.cards) {
    if (state.card && row.id === state.card.id) continue;
    select.append(new Option(row.name, row.id));
  }
  const stillExists = [...select.options].some((option) => option.value === previous);
  select.value = stillExists ? previous : "";
  state.compareOtherId = select.value;
}

function paintSource() {
  const canvas = get("capture");
  const ctx = canvas.getContext("2d");
  const {photo, card, probe} = state;
  if (!photo) return;
  canvas.width = card.width;
  canvas.height = card.height;
  ctx.drawImage(photo, 0, 0);
  const cfg = card.settings;
  ctx.lineWidth = Math.max(1, card.width / 550);
  const {result} = state;
  const radians = result.angle * Math.PI / 180;
  ctx.strokeStyle = "#317778";
  ctx.lineWidth = 2;
  ctx.setLineDash([7, 5]);
  ctx.beginPath();
  ctx.moveTo(cfg.cx - cfg.half_span * Math.cos(radians), cfg.cy - cfg.half_span * Math.sin(radians));
  ctx.lineTo(cfg.cx + cfg.half_span * Math.cos(radians), cfg.cy + cfg.half_span * Math.sin(radians));
  ctx.stroke();
  ctx.setLineDash([]);
  const center = result.center;
  const [tx, ty] = result.across;
  ctx.strokeStyle = "#b15e34";
  ctx.beginPath();
  ctx.moveTo(center.x - tx * cfg.half_span, center.y - ty * cfg.half_span);
  ctx.lineTo(center.x + tx * cfg.half_span, center.y + ty * cfg.half_span);
  ctx.stroke();
  ctx.fillStyle = "#236960";
  if (result.reading) {
    for (const pt of result.reading.ends) {
      ctx.beginPath();
      ctx.arc(pt.x, pt.y, 4, 0, 2 * Math.PI);
      ctx.fill();
    }
  }
  if (probe) {
    ctx.strokeStyle = "#132637";
    ctx.strokeRect(probe.x - 5, probe.y - 5, 10, 10);
  }
}

function paintChart() {
  const canvas = get("chart");
  const ctx = canvas.getContext("2d");
  const {width, height} = canvas;
  const {result, card} = state;
  const cfg = card.settings;
  const xAt = (x) => 40 + (width - 66) * (x + cfg.half_span) / (2 * cfg.half_span);
  const yAt = (y) => height - 25 - (height - 42) * y / 255;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#fdfaf6";
  ctx.fillRect(0, 0, width, height);
  if (result.reading) {
    const [left, right] = result.reading.ends.map((point) => xAt(point.offset));
    ctx.fillStyle = "#e5ece3";
    ctx.fillRect(left, 15, Math.max(2, right - left), height - 40);
  }
  ctx.font = "12px monospace";
  ctx.fillStyle = "#695749";
  ctx.strokeStyle = "#dfd1c5";
  for (const level of [0,128,255]) {
    ctx.fillText(level, 5, yAt(level) + 3);
    ctx.beginPath();
    ctx.moveTo(xAt(-cfg.half_span), yAt(level));
    ctx.lineTo(xAt(cfg.half_span), yAt(level));
    ctx.stroke();
  }
  ctx.fillText(String(-cfg.half_span), xAt(-cfg.half_span), height - 6);
  ctx.fillText("0", xAt(0), height - 6);
  ctx.fillText(String(cfg.half_span), width - 35, height - 6);
  ctx.strokeStyle = "#366f69";
  ctx.lineWidth = 2;
  ctx.beginPath();
  let last = null;
  for (const point of result.profile) {
    if (last === null || point.offset !== last + 1) ctx.moveTo(xAt(point.offset), yAt(point.brightness));
    else ctx.lineTo(xAt(point.offset), yAt(point.brightness));
    last = point.offset;
  }
  ctx.stroke();
  ctx.strokeStyle = "#b15e34";
  ctx.setLineDash([6,4]);
  ctx.beginPath();
  ctx.moveTo(xAt(-cfg.half_span),yAt(cfg.cutoff));
  ctx.lineTo(xAt(cfg.half_span),yAt(cfg.cutoff));
  ctx.stroke();
  ctx.setLineDash([]);
}

function pointLabel(point) {
  return "(" + point.x + ", " + point.y + ") · 前后 " + point.longitudinal.toFixed(1) + " px · " +
    "横向 " + point.transverse.toFixed(1) + " px · " + point.brightness.toFixed(1);
}

function show() {
  const {card, result} = state;
  get("workspace").hidden = false;
  get("empty").hidden = true;
  get("origin").textContent = (card.synthetic ? "合成练习图" : "导入原片") + " · 修订 " + card.revision;
  get("card-title").textContent = card.name;
  get("dimensions").textContent = card.width + " × " + card.height + " px · " + card.photo_name;
  get("name").value = card.name;
  for (const field of settings) field.value = card.settings[field.dataset.setting];
  get("probe-x").max = card.width - 1;
  get("probe-y").max = card.height - 1;
  get("probe-x").value = state.probe?.x ?? Math.round(result.center.x);
  get("probe-y").value = state.probe?.y ?? Math.round(result.center.y);
  get("probe-result").textContent = state.probe ? pointLabel(state.probe) : "选择原片像素";
  const reading = result.reading;
  get("span-width").textContent = reading ? reading.width_mm.toFixed(2) : "—";
  get("span-unit").textContent = reading ? "mm" : "未见连续印痕";
  get("measurement-state").textContent = "截位 " + result.station + " px";
  get("selected-level").textContent = reading
    ? "同一连续印痕 " + reading.pixels + " px · 原片截面"
    : "当前截面没有满足亮度门槛的连续印痕";
  for (const [id, filename] of Object.entries({proof: "proof.png", csv: "profile.csv", zip: "observation.zip"})) {
    get(id).href = "/trackplate" + route("/export/" + filename) + savedQuery();
  }
  paintSource();
  paintChart();
  get("compare-dock").hidden = false;
  get("compare-panel").hidden = true;
  state.compare = null;
  state.selectedIdx = null;
  syncCompareOptions();
}

async function open(card) {
  const base = "/cards/" + card.id;
  const photo = new Image();
  photo.src = "/trackplate" + base + "/capture.png";
  const [result] = await Promise.all([api(base + "/measurement?revision=" + card.revision), photo.decode()]);
  Object.assign(state, {card, result, photo, probe: null});
  get("library").value = card.id;
  show();
}

async function probe(x, y) {
  state.probe = await api(route("/probe") + savedQuery() + "&x=" + x + "&y=" + y);
  get("probe-x").value = x;
  get("probe-y").value = y;
  get("probe-result").textContent = pointLabel(state.probe);
  paintSource();
}

get("practice").addEventListener("click", () => action(async () => {
  await open(await api("/practice", {method: "POST"}));
  await library();
}));

get("library").addEventListener("change", () => {
  if (get("library").value) action(async () => open(await api("/cards/" + get("library").value)));
});
get("open-import").addEventListener("click", () => {
  notice("", "import-notice");
  get("import").showModal();
});
get("cancel-import").addEventListener("click", () => get("import").close());
get("upload").addEventListener("submit", (event) => {
  event.preventDefault();
  action(async () => {
    const card = await api("/cards", {method: "POST", body: new FormData(event.target)});
    await open(card);
    await library();
    get("import").close();
    event.target.reset();
  }, "import-notice");
});
document.querySelectorAll("[data-mode]").forEach((button) => button.addEventListener("click", () => {
  state.mode = button.dataset.mode;
  document.querySelectorAll("[data-mode]").forEach((item) =>
    item.setAttribute("aria-pressed", String(item === button)));
}));
get("capture").addEventListener("click", (event) => {
  if (!state.card) return;
  const canvas = event.currentTarget;
  const rect = canvas.getBoundingClientRect();
  const x = Math.max(0, Math.min(canvas.width - 1, Math.floor((event.clientX - rect.left) * canvas.width / rect.width)));
  const y = Math.max(0, Math.min(canvas.height - 1, Math.floor((event.clientY - rect.top) * canvas.height / rect.height)));
  action(async () => {
    await probe(x, y);
    if (state.mode === "mark") {
      settings.find((field) => field.dataset.setting === "station").value = Math.round(state.probe.longitudinal);
      notice("已填入原片前后截位，保存后更新宽度和校样。");
    }
  });
});
get("probe-form").addEventListener("submit", (event) => {
  event.preventDefault();
  action(() => probe(get("probe-x").value, get("probe-y").value));
});
get("calibration").addEventListener("submit", (event) => {
  event.preventDefault();
  action(async () => {
    const fields = Object.fromEntries(settings.map((field) => [field.dataset.setting, Number(field.value)]));
    const card = await api(route(""), {
      method: "PATCH", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({revision: state.card.revision, name: get("name").value, settings: fields}),
    });
    await open(card);
    await library();
  });
});
get("prepare").addEventListener("click", () => action(async () => {
  const {url} = await api(route("/prepare") + savedQuery(), {method: "POST"});
  location.assign(url);
}));

/* ------------------------- 新旧毫米对照 ------------------------- */

function loadImage(src) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("对照原片加载失败"));
    image.src = src;
  });
}

function mmMapper(config) {
  const rad = Number(config.angle) * Math.PI / 180;
  const fx = Math.cos(rad), fy = Math.sin(rad);
  const qx = -fy, qy = fx;
  const scale = Number(config.mm_per_px);
  const cx = Number(config.cx), cy = Number(config.cy);
  return (u, v) => [cx + (u / scale) * fx + (v / scale) * qx,
                    cy + (u / scale) * fy + (v / scale) * qy];
}

function compareGeom() {
  const frame = state.compare.frame;
  return {span: frame.index_span, side: frame.side, step: frame.step_mm};
}

function idxToCell(idx) {
  const {span, side, step} = compareGeom();
  const row = Math.floor(idx / side);
  const col = idx % side;
  const iu = span - row;
  const iv = col - span;
  return {row, col, iu, iv, u: iu * step, v: iv * step};
}

function coordText(x, y) {
  return (x === null || x === undefined || y === null || y === undefined)
    ? "一侧缺拍 · 无实际源像素" : `(${x}, ${y})`;
}

function setCoordValue(id, x, y) {
  const node = get(id);
  node.textContent = coordText(x, y);
  node.dataset.x = x ?? "";
  node.dataset.y = y ?? "";
}

function filteredIndices(filter) {
  const categories = state.compare.categories;
  const indices = [];
  for (let idx = 0; idx < categories.length; idx += 1) {
    const cat = categories[idx];
    if (filter === "all" || (filter === "change" && (cat === "new" || cat === "gone")) ||
        (filter !== "all" && filter !== "change" && cat === filter)) {
      indices.push(idx);
    }
  }
  return indices;
}

function setCompareFilter(filter) {
  state.compareFilter = filter;
  document.querySelectorAll("[data-filter]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.filter === filter));
  });
  renderCompareTable();
}

function renderCompareChrome() {
  const report = state.compare;
  const counts = report.counts;
  const geom = compareGeom();
  get("compare-frame-info").textContent =
    `共同范围：前后、左右均从原点向外 ${report.frame.half_width_mm.toFixed(2)} mm` +
    `（两卡实际取样半宽较小者：现 ${report.frame.half_current_mm.toFixed(2)} mm / 旧 ${report.frame.half_old_mm.toFixed(2)} mm）` +
    ` · 格距 ${geom.step.toFixed(3)} mm（不细过更清楚照片的 ${report.frame.sharper_pixel_mm} mm/px）` +
    ` · 格网 ${geom.side}×${geom.side} · 可判 ${counts.judged} · 一侧缺拍 ${counts.outside}`;

  const chips = [
    {key: "change", zh: "变化（新印＋消失）", count: counts.changes, color: "#b15e34"},
    ...COMPARE_META.map((meta) => ({...meta, count: counts[meta.key]})),
  ];
  const summary = get("compare-summary");
  summary.replaceChildren(...chips.map((item) => {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.filter = item.key;
    button.setAttribute("aria-pressed", String(state.compareFilter === item.key));
    const swatch = document.createElement("i");
    swatch.className = "swatch";
    swatch.style.background = item.color;
    button.append(swatch, document.createTextNode(`${item.zh} ${item.count}`));
    button.addEventListener("click", () => setCompareFilter(item.key));
    return button;
  }));

  const tabs = [
    {key: "change", zh: "变化"}, {key: "new", zh: "新印"}, {key: "gone", zh: "消失"},
    {key: "both", zh: "都在"}, {key: "blank", zh: "都空"}, {key: "outside", zh: "缺拍"},
    {key: "all", zh: "全部"},
  ];
  const filters = get("compare-filters");
  filters.replaceChildren(...tabs.map((tab) => {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.filter = tab.key;
    button.setAttribute("aria-pressed", String(state.compareFilter === tab.key));
    button.textContent = `${tab.zh} ${tab.key === "all" ? counts.total
      : tab.key === "change" ? counts.changes : counts[tab.key]}`;
    button.addEventListener("click", () => setCompareFilter(tab.key));
    return button;
  }));
}

function renderCompareMap() {
  const report = state.compare;
  const {side} = compareGeom();
  const canvas = get("compare-map");
  canvas.width = side;
  canvas.height = side;
  const ctx = canvas.getContext("2d");
  const categories = report.categories;
  for (let idx = 0; idx < categories.length; idx += 1) {
    ctx.fillStyle = compareColor[categories[idx]] || "#ccc";
    ctx.fillRect(idx % side, Math.floor(idx / side), 1, 1);
  }
  const wrap = canvas.parentElement;
  wrap.style.position = "relative";
  let cursor = get("compare-map-cursor");
  if (!cursor) {
    cursor = document.createElement("i");
    cursor.id = "compare-map-cursor";
    cursor.style.cssText =
      "position:absolute;border:2px solid #ffd400;box-shadow:0 0 0 1px #352a21;pointer-events:none;display:none;z-index:2;";
    wrap.append(cursor);
  }
  updateMapCursor();
}

function updateMapCursor() {
  const cursor = get("compare-map-cursor");
  if (!cursor) return;
  const idx = state.selectedIdx;
  if (idx === null || idx === undefined) {
    cursor.style.display = "none";
    return;
  }
  const {side} = compareGeom();
  const {row, col} = idxToCell(idx);
  const wrap = get("compare-map").parentElement;
  const pad = 8; // .compare-map-wrap padding
  const size = get("compare-map").clientWidth || wrap.clientWidth - pad * 2;
  cursor.style.display = "block";
  cursor.style.left = `${pad + col / side * size - 2}px`;
  cursor.style.top = `${pad + row / side * size - 2}px`;
  cursor.style.width = `${size / side + 3}px`;
  cursor.style.height = `${size / side + 3}px`;
}

function buildSourceBase(which) {
  const report = state.compare;
  const summary = which === "current" ? report.current : report.old;
  const photo = which === "current" ? state.photo : state.oldPhoto;
  const {span, step} = compareGeom();
  const canvas = document.createElement("canvas");
  canvas.width = summary.width;
  canvas.height = summary.height;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(photo, 0, 0, canvas.width, canvas.height);

  const map = mmMapper(summary);
  const halfCell = step / 2;
  const paths = Object.fromEntries(COMPARE_META.map((meta) => [meta.key, new Path2D()]));
  report.categories.forEach((category, idx) => {
    const {iu, iv} = idxToCell(idx);
    const u = iu * step;
    const v = iv * step;
    const path = paths[category] || paths.blank;
    const corners = [[u - halfCell, v - halfCell], [u + halfCell, v - halfCell],
                     [u + halfCell, v + halfCell], [u - halfCell, v + halfCell]];
    corners.forEach(([uu, vv], corner) => {
      const [x, y] = map(uu, vv);
      if (corner === 0) path.moveTo(x, y);
      else path.lineTo(x, y);
    });
    path.closePath();
  });
  for (const meta of COMPARE_META) {
    ctx.globalAlpha = meta.key === "outside" ? 0.16 : 0.42;
    ctx.fillStyle = meta.color;
    ctx.fill(paths[meta.key]);
  }
  ctx.globalAlpha = 1;

  const half = report.frame.half_width_mm;
  ctx.strokeStyle = "#ffd600";
  ctx.lineWidth = Math.max(2, summary.width / 320);
  ctx.beginPath();
  [[-half, -half], [-half, half], [half, half], [half, -half]].forEach(([u, v], corner) => {
    const [x, y] = map(u, v);
    if (corner === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.closePath();
  ctx.stroke();
  return canvas;
}

function presentSource(which) {
  const base = which === "current" ? state.currentBase : state.oldBase;
  const canvas = get(which === "current" ? "compare-current-canvas" : "compare-old-canvas");
  if (!base) return;
  canvas.width = base.width;
  canvas.height = base.height;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(base, 0, 0);
  const idx = state.selectedIdx;
  if (idx !== null && idx !== undefined) {
    const report = state.compare;
    const x = which === "current" ? report.cx[idx] : report.ox[idx];
    const y = which === "current" ? report.cy[idx] : report.oy[idx];
    if (x !== null && y !== null) {
      const radius = Math.max(7, base.width / 90);
      ctx.strokeStyle = "#ffd400";
      ctx.lineWidth = Math.max(2, base.width / 420);
      ctx.beginPath();
      ctx.arc(x, y, radius, 0, 2 * Math.PI);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(x - radius * 1.7, y);
      ctx.lineTo(x + radius * 1.7, y);
      ctx.moveTo(x, y - radius * 1.7);
      ctx.lineTo(x, y + radius * 1.7);
      ctx.stroke();
    }
  }
}

function renderCompareTable() {
  if (!state.compare) return;
  const filter = state.compareFilter;
  const indices = filteredIndices(filter);
  const tbody = get("compare-table").querySelector("tbody");
  const CAP = 400;
  const shown = indices.slice(0, CAP);
  tbody.replaceChildren(...shown.map((idx) => {
    const report = state.compare;
    const {u, v} = idxToCell(idx);
    const tr = document.createElement("tr");
    tr.dataset.idx = String(idx);
    if (idx === state.selectedIdx) tr.classList.add("selected");
    tr.innerHTML =
      `<td>(${u.toFixed(1)}, ${v.toFixed(1)})</td>` +
      `<td data-role="current-coord">${coordText(report.cx[idx], report.cy[idx])}</td>` +
      `<td data-role="old-coord">${coordText(report.ox[idx], report.oy[idx])}</td>` +
      `<td><span class="cat-badge cat-${report.categories[idx]}">${compareZh[report.categories[idx]]}</span></td>`;
    tr.addEventListener("click", () => selectCell(idx));
    return tr;
  }));
  if (indices.length > CAP) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan="4">仅列出前 ${CAP} 条，共 ${indices.length} 条；可点格网或用类别页签缩小范围。</td>`;
    tbody.append(tr);
  }
  if (!indices.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan="4">这一类别没有格点。</td>`;
    tbody.append(tr);
  }
}

async function selectCell(idx) {
  if (!state.compare) return;
  state.selectedIdx = idx;
  const report = state.compare;
  const category = report.categories[idx];
  const {u, v} = idxToCell(idx);
  get("compare-readout-fields").hidden = false;
  get("compare-mm").textContent =
    `共同毫米坐标：前后 u=${u.toFixed(3)} mm，左右 v=${v.toFixed(3)} mm（格 ${idxToCell(idx).iu}, ${idxToCell(idx).iv}）——这是共同网格位置，不是原片坐标`;
  const categoryNode = get("compare-category");
  categoryNode.textContent = compareZh[category];
  categoryNode.dataset.category = category;
  categoryNode.className = `cat-badge cat-${category}`;

  const cx = report.cx[idx];
  const cy = report.cy[idx];
  const ox = report.ox[idx];
  const oy = report.oy[idx];
  setCoordValue("compare-current-xy", cx, cy);
  setCoordValue("compare-old-xy", ox, oy);
  get("compare-current-level").textContent = "";
  get("compare-old-level").textContent = "";
  get("compare-probe-current").textContent = "现片点读中…";
  get("compare-probe-old").textContent = ox === null ? "旧照这一侧缺拍，未点读。" : "旧照点读中…";

  document.querySelectorAll("#compare-table tr.selected").forEach((row) => row.classList.remove("selected"));
  const row = get("compare-table").querySelector(`tr[data-idx="${idx}"]`);
  if (row) {
    row.classList.add("selected");
    row.scrollIntoView({block: "nearest"});
  }
  updateMapCursor();
  presentSource("current");
  presentSource("old");

  const otherId = state.compareOtherId;
  const probeJobs = [];
  if (cx !== null && cy !== null) {
    probeJobs.push(api(route("/probe") + savedQuery() + `&x=${cx}&y=${cy}`).then((probe) => {
      get("compare-probe-current").textContent = "现有原片点读 · " + pointLabel(probe);
      get("compare-current-level").textContent =
        `亮度 ${probe.brightness.toFixed(1)} / 上限 ${state.card.settings.cutoff} · ${probe.marked ? "判为印痕" : "未达上限"}`;
    }).catch(() => {
      get("compare-probe-current").textContent = "现片点读失败";
    }));
  } else {
    get("compare-probe-current").textContent = "现在这张未拍到该位置。";
  }
  if (ox !== null && oy !== null) {
    probeJobs.push(api(`/cards/${otherId}/probe?x=${ox}&y=${oy}`).then((probe) => {
      get("compare-probe-old").textContent = "旧照原片点读 · " + pointLabel(probe);
      get("compare-old-level").textContent =
        `亮度 ${probe.brightness.toFixed(1)} / 其自存上限 ${probe.marked ? "· 判为印痕" : "· 未达上限"}`;
    }).catch(() => {
      get("compare-probe-old").textContent = "旧照点读失败";
    }));
  }
  await Promise.all(probeJobs);
}

async function runCompare(otherId) {
  if (!state.card) throw new Error("请先打开现在这张拓片卡");
  if (!otherId) {
    notice("请先在「旧照拓片卡」里挑选要对比的卡片。");
    return;
  }
  state.compareOtherId = otherId;
  const query = savedQuery() + "&other=" + encodeURIComponent(otherId) + "&view=compact";
  const [report, oldPhoto] = await Promise.all([
    api(route("/compare") + query),
    loadImage(`/trackplate/cards/${encodeURIComponent(otherId)}/capture.png`),
  ]);
  state.compare = report;
  state.oldPhoto = oldPhoto;
  state.selectedIdx = null;
  state.compareFilter = "change";
  get("compare-panel").hidden = false;

  state.currentBase = buildSourceBase("current");
  state.oldBase = buildSourceBase("old");
  renderCompareChrome();
  renderCompareMap();
  presentSource("current");
  presentSource("old");
  renderCompareTable();

  const linkQuery = `?other=${encodeURIComponent(otherId)}&revision=${state.card.revision}`;
  const proofUrl = "/trackplate" + route("/export/compare-proof.png") + linkQuery;
  const csvUrl = "/trackplate" + route("/export/compare.csv") + linkQuery;
  get("compare-proof-link").href = proofUrl;
  get("compare-proof-download").href = proofUrl;
  get("compare-csv-link").href = csvUrl;
  const image = get("compare-proof-image");
  image.hidden = false;
  image.src = proofUrl;

  get("compare-readout-fields").hidden = true;
  get("compare-probe-current").textContent = "";
  get("compare-probe-old").textContent = "";
  setCompareFilter(filteredIndices("change").length ? "change" : "all");
  window.trackplateCompare = {runCompare, selectCell, state};
}

get("compare-run").addEventListener("click", () => {
  const otherId = get("compare-other").value;
  action(() => runCompare(otherId));
});
get("compare-other").addEventListener("change", (event) => {
  state.compareOtherId = event.target.value;
  if (event.target.value) action(() => runCompare(event.target.value));
});
get("compare-map").addEventListener("click", (event) => {
  if (!state.compare) return;
  const canvas = event.currentTarget;
  const rect = canvas.getBoundingClientRect();
  const {side} = compareGeom();
  const col = Math.floor((event.clientX - rect.left) / rect.width * side);
  const row = Math.floor((event.clientY - rect.top) / rect.height * side);
  if (col < 0 || col >= side || row < 0 || row >= side) return;
  selectCell(row * side + col);
});
get("compare-table").querySelector("tbody").addEventListener("click", (event) => {
  const row = event.target.closest("tr[data-idx]");
  if (row) selectCell(Number(row.dataset.idx));
});
window.addEventListener("resize", () => { if (state.compare) updateMapCursor(); });

// Deep links: /?card=<current id>&other=<old id> open and run immediately.
action(async () => {
  await library();
  const params = new URLSearchParams(location.search);
  const cardId = params.get("card") || params.get("current") || params.get("id");
  const otherId = params.get("other") || params.get("old") || params.get("compare");
  if (cardId && state.cards.some((row) => row.id === cardId)) {
    const card = await api("/cards/" + cardId);
    await open(card);
    if (otherId) {
      get("compare-other").value = otherId;
      await runCompare(otherId);
    }
  }
});
