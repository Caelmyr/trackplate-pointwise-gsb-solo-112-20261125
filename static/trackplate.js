"use strict";

const get = (id) => document.getElementById(id);
const state = {card: null, result: null, photo: null, probe: null, mode: "probe", busy: false};
const settings = [...document.querySelectorAll("[data-setting]")];

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
  const select = get("library");
  select.replaceChildren(new Option("选取已存卡片", ""));
  for (const card of cards) select.append(new Option(card.name, card.id));
  select.value = state.card?.id || "";
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
    const ends = result.reading.segments
      ? result.reading.segments.flatMap((segment) => segment.ends)
      : result.reading.ends;
    for (const pt of ends) {
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
    const runs = result.reading.segments || [result.reading];
    ctx.fillStyle = "#e5ece3";
    for (const run of runs) {
      const [left, right] = run.ends.map((point) => xAt(point.offset));
      ctx.fillRect(left, 15, Math.max(2, right - left), height - 40);
    }
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
  const area = result.area;
  get("span-width").textContent = reading ? reading.width_mm.toFixed(2) : "—";
  get("span-unit").textContent = reading
    ? (reading.mode === "pad" ? "mm · " + reading.segments.length + " 段" : "mm")
    : "未见连续印痕";
  get("measurement-state").textContent = "截位 " + result.station + " px";
  if (reading && reading.mode === "pad") {
    get("selected-level").textContent = reading.segments.length
      ? "掌垫各段实际宽度合计 · " + reading.pixels + " px"
      : "当前截面没有落在掌垫上的暗印痕";
    get("segment-list").textContent = reading.segments.length
      ? reading.segments.map((segment) =>
          "#" + (segment.index + 1) + " " + segment.pixels + " px（" + segment.width_mm.toFixed(2) +
          " mm）· 偏移 " + segment.start + "…" + segment.end).join("　")
      : "空段不计入宽度";
  } else {
    get("selected-level").textContent = reading
      ? "同一连续印痕 " + reading.pixels + " px · 原片截面"
      : "当前截面没有满足亮度门槛的连续印痕";
    get("segment-list").textContent = "手填普通连续印痕读数";
  }
  get("pad-area").textContent = area ? area.mm2.toFixed(2) : "—";
  get("pad-unit").textContent = area && !area.origin_marked
    ? "mm² · 参考原点不在暗印痕上"
    : "mm² · " + (area ? area.pixels : 0) + " px";
  if (area) {
    const attached = area.blocks.filter((block) => block.attached).length;
    const loose = area.blocks.filter((block) => !block.attached).length;
    get("area-detail").textContent = area.origin_marked
      ? "只算归入的暗印痕 " + area.pixels + " px；原点块 " + area.base_pixels + " px，接上 " +
        attached + " 块，留下 " + loose + " 块。"
      : "参考原点不在暗印痕上，面积留空。";
  }
  for (const [id, filename] of Object.entries({proof: "proof.png", csv: "profile.csv", zip: "observation.zip"})) {
    get(id).href = "/trackplate" + route("/export/" + filename) + savedQuery();
  }
  paintSource();
  paintChart();
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
    const fields = Object.fromEntries(settings.map((field) => [
      field.dataset.setting, field.tagName === "SELECT" ? field.value : Number(field.value)]));
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
action(library);
