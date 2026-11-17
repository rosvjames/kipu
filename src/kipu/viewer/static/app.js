/* kipu viewer: blocks in lanes, click to open, follow the data step by step. No dependencies. */
"use strict";

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const SEP = "\u0001";
const DATA = new Set(["reads", "writes", "feeds", "passes"]);
const RANK = { exact: 3, inferred: 2, unresolved: 1 };
const CONF_ES = { exact: "exacta", inferred: "inferida", unresolved: "sin resolver" };
const FONT = getComputedStyle(document.documentElement).getPropertyValue("--font").trim();
const KIND_ES = { table: "tabla", stage: "stage", external: "fuente externa", task: "paso", flow: "flow",
  module: "módulo Python", unknown: "sin resolver", file: "archivo", schema: "schema", dir: "carpeta",
  files: "archivos", unresolved: "sin resolver", dbt_project: "proyecto dbt" };
const PLURAL = { table: ["tabla", "tablas"], stage: ["stage", "stages"], task: ["paso", "pasos"],
  module: ["módulo", "módulos"], file: ["archivo", "archivos"], unknown: ["sin resolver", "sin resolver"] };
const VERB = { reads: "lee", writes: "escribe en", feeds: "alimenta a", passes: "entrega su resultado a",
  runs: "ejecuta", triggers: "dispara", imports: "importa" };
const ICON = {
  db: "M2.5 4c0-1.3 2.5-2.2 5.5-2.2s5.5.9 5.5 2.2v8c0 1.3-2.5 2.2-5.5 2.2S2.5 13.3 2.5 12zM2.5 4c0 1.3 2.5 2.2 5.5 2.2s5.5-.9 5.5-2.2M2.5 8c0 1.3 2.5 2.2 5.5 2.2s5.5-.9 5.5-2.2",
  flow: "M2.5 2.5h5v3.4h-5zM8.5 10.1h5v3.4h-5zM5 5.9v4.3c0 .8.6 1.5 1.5 1.5h2",
  folder: "M1.8 4.2c0-.6.4-1 1-1h3l1.4 1.6h6c.6 0 1 .4 1 1v6c0 .6-.4 1-1 1H2.8c-.6 0-1-.4-1-1z",
  globe: "M8 1.8a6.2 6.2 0 100 12.4A6.2 6.2 0 008 1.8zM1.8 8h12.4M8 1.8c-2.4 2.2-2.4 10.2 0 12.4M8 1.8c2.4 2.2 2.4 10.2 0 12.4",
  file: "M4 1.8h5l3 3v9.4H4zM9 1.8v3h3",
  code: "M5.5 4.5L2 8l3.5 3.5M10.5 4.5L14 8l-3.5 3.5",
  what: "M6 6.2a2 2 0 113.1 1.6c-.7.5-1.1.9-1.1 1.7M8 12.2v.3",
};
const BH = 56, TOP_H = 46, CH = 28, CGAP = 7, PAD = 12, HEAD = 36, SUBGAP = 44, GAPX = 84, GAPY = 26, MIN_FIT = 0.85;

const S = { data: null, version: -1, mode: null, open: new Set(), sel: null, hover: null, showChanges: false,
  showCtl: false, cam: { x: 0, y: 0, k: 1 }, tour: null, first: true };
let M = null, L = null, A = null;            // model, layout, aggregated edges
let nodeEls = new Map(), edgeEls = new Map(), ctlEls = [];
const svg = $("#map"), world = $("#world");

const _ctx = document.createElement("canvas").getContext("2d");
function tw(text, size = 13, weight = 500) { _ctx.font = `${weight} ${size}px ${FONT}`; return _ctx.measureText(text).width; }
const clip = (s, n = 30) => (s.length > n ? s.slice(0, n - 1) + "…" : s);
const status = (msg) => { $("#status").textContent = msg || ""; };

// ====================================================================== model
function buildModel() {
  const d = S.data;
  const m = { nodes: new Map(), parent: new Map(), children: new Map(), dbt: new Map(), tops: [], tray: [],
    edges: [], dOut: new Map(), dIn: new Map(), control: [], order: new Map() };
  d.nodes.forEach((n, i) => { m.order.set(n.id, i); (n.kind === "dbt_project" ? m.dbt : m.nodes).set(n.id, n); });
  const adopt = (id, p) => { m.parent.set(id, p); if (!m.children.has(p)) m.children.set(p, []); m.children.get(p).push(id); };
  const synth = (id, kind, label) => { if (!m.nodes.has(id)) { m.nodes.set(id, { id, kind, label, synthetic: true, meta: {} }); m.order.set(id, 1e6 + m.order.size); } return id; };
  for (const n of d.nodes) {
    if (["dbt_project", "schema", "dir", "flow"].includes(n.kind)) continue;
    if (n.parent && m.nodes.has(n.parent)) adopt(n.id, n.parent);
    else if (n.kind === "file") { const p = String(n.meta?.path || ""); const dir = p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : ""; adopt(n.id, synth("files:" + dir, "files", dir ? dir + "/" : "archivos")); }
    else if (n.kind === "unknown") adopt(n.id, synth("group:unresolved", "unresolved", "Sin resolver"));
    else if (n.kind === "module") adopt(n.id, synth("group:root", "dir", "./"));
  }
  for (const [gid, kids] of [...m.children]) {               // a made-up group of one is just that node
    if (m.nodes.get(gid).synthetic && kids.length === 1) { m.parent.delete(kids[0]); m.children.delete(gid); m.nodes.delete(gid); }
  }
  for (const [id, n] of [...m.nodes]) if (["schema", "dir", "flow", "files", "unresolved"].includes(n.kind) && !m.children.has(id)) m.nodes.delete(id);

  const has = (e) => m.nodes.has(e.source) && m.nodes.has(e.target);
  const kinds = S.mode === "code" ? new Set(["imports"]) : DATA;
  m.edges = d.edges.filter((e) => kinds.has(e.kind) && has(e));
  for (const e of d.edges) {
    if (DATA.has(e.kind) && has(e)) {
      if (!m.dOut.has(e.source)) m.dOut.set(e.source, []);
      if (!m.dIn.has(e.target)) m.dIn.set(e.target, []);
      m.dOut.get(e.source).push(e); m.dIn.get(e.target).push(e);
    } else if (e.kind === "runs" || e.kind === "triggers") m.control.push(e);
  }
  const top = (id) => m.parent.get(id) || id;
  const touched = new Set();
  m.edges.forEach((e) => { touched.add(e.source); touched.add(e.target); touched.add(top(e.source)); touched.add(top(e.target)); });
  m.control.forEach((e) => { if (e.kind === "runs") touched.add(e.source); });      // a step that launches something is not idle
  m.touched = touched;
  for (const id of m.nodes.keys()) if (!m.parent.has(id)) (touched.has(id) ? m.tops : m.tray).push(id);
  m.top = top;
  return m;
}

function layerName(name) {
  if (/gold|mart|report|analytic/i.test(name)) return "gold";
  if (/silver|stag|stg|interm|clean|curated/i.test(name)) return "silver";
  if (/bronze|raw|landing|ingest/i.test(name)) return "bronze";
  return "store";
}
function cat(id) {
  const n = M.nodes.get(id) || M.dbt.get(id);
  if (!n) return "store";
  if (n.kind === "flow" || n.kind === "task") return "proc";
  if (n.kind === "dir" || n.kind === "module") return "code";
  if (n.kind === "external") return "ext";
  if (n.kind === "unknown" || n.kind === "unresolved") return "bad";
  if (n.kind === "schema") return layerName(n.label);
  if (n.kind === "table" || n.kind === "stage") { const p = M.parent.get(id); return p ? layerName(M.nodes.get(p).label) : "store"; }
  return "store";
}
const isStore = (id) => !["proc", "code"].includes(cat(id));
const isGroup = (id) => M.children.has(id);
const vis = (id) => { const p = M.parent.get(id); return p && !S.open.has(p) ? p : id; };
const label = (id) => (M.nodes.get(id) || M.dbt.get(id) || { label: id }).label;

function runnersOf(id) {            // who runs this leaf: "flow › task"
  const n = M.nodes.get(id);
  const targets = new Set([id]);
  if (n?.meta?.dbt_project) targets.add(n.meta.dbt_project);
  return M.control.filter((e) => e.kind === "runs" && targets.has(e.target));
}
function tagFor(id) {
  const n = M.nodes.get(id);
  if (n.kind === "flow") {
    const bits = [];
    if (n.meta?.schedule?.length) bits.push("⏱ " + n.meta.schedule[0]);
    const trig = M.control.filter((e) => e.kind === "triggers" && e.target === id);
    if (trig.length) bits.push("⚡ tras " + trig.map((e) => label(e.source)).join(", "));
    return bits.join(" · ");
  }
  const leaves = isGroup(id) ? M.children.get(id) : [id];
  const flows = new Set();
  leaves.forEach((l) => runnersOf(l).forEach((e) => flows.add(label(M.parent.get(e.source) || e.source))));
  if (!flows.size) return "";
  const a = [...flows];
  return "⚙ " + a.slice(0, 2).join(", ") + (a.length > 2 ? ` +${a.length - 2}` : "");
}
const GENERIC = { table: "Tabla de datos.", stage: "Zona de archivos dentro del almacén de datos.", task: "Paso de un flow.",
  module: "Módulo de Python.", external: "Fuente externa al proyecto.", file: "Archivo de datos.", flow: "Flow de orquestación.",
  schema: "Grupo de tablas.", dir: "Carpeta de código.", dbt_project: "Proyecto dbt.",
  unknown: "kipu no pudo saber a qué apunta este nombre leyendo el código." };
function firstBit(text, max = 170) {
  const t = String(text).replace(/\s+/g, " ").trim();
  if (t.length <= max) return t;
  const cut = t.slice(0, max), dot = cut.lastIndexOf(". ");
  return dot > 50 ? cut.slice(0, dot + 1) : cut.slice(0, cut.lastIndexOf(" ")) + "…";
}
function explain(id) {            // the plain-language line for a node, and where it comes from
  const n = M.nodes.get(id) || M.dbt.get(id);
  if (!n) return { text: "", src: "none" };
  const m = n.meta || {}, code = m.description || m.comment || "";
  if (n.note && !n.note.stale) return { text: n.note.what, why: n.note.why, src: "ai", code };
  if (code) return { text: firstBit(code), src: "code", code };
  if (n.note) return { text: n.note.what, why: n.note.why, src: "ai", stale: true };
  return { text: GENERIC[n.kind] || "", src: "none" };
}
function srcMark(x) {
  if (x.src === "ai" && x.stale) return '<span class="src warn" title="El código cambió después de que se escribió">⚠ escrita por IA antes del último cambio</span>';
  if (x.src === "ai") return '<span class="src ai" title="Redactada por tu agente a partir de lo que kipu leyó del código">✦ explicación de IA</span>';
  if (x.src === "code") return '<span class="src" title="Comentario o descripción que ya estaba en el código">tomado del código</span>';
  return "";
}
function runsLabel(id) {          // "▶ build_obt.py" on a step that launches something
  const r = M.control.filter((e) => e.kind === "runs" && e.source === id);
  return r.length ? "▶ " + r.map((e) => clip(label(e.target), 18)).join(", ") : "";
}
function summary(id) {
  const c = {};
  M.children.get(id).forEach((k) => { const kind = M.nodes.get(k).kind; c[kind] = (c[kind] || 0) + 1; });
  return Object.entries(c).map(([k, v]) => `${v} ${(PLURAL[k] || [k, k])[v === 1 ? 0 : 1]}`).join(" · ");
}

// ====================================================================== layout
function layerize(ids, pairs) {
  const idx = new Map(ids.map((id, i) => [id, i]));
  const adj = new Map(ids.map((i) => [i, []])), indeg = new Map(ids.map((i) => [i, 0]));
  for (const [k, c] of pairs) {
    const [a, b] = k.split(SEP);
    if (!idx.has(a) || !idx.has(b) || a === b) continue;
    const rev = pairs.get(b + SEP + a) || 0;
    if (rev > c) continue;                                   // keep only the dominant direction of a two-way pair
    if (rev === c) { const sa = isStore(a), sb = isStore(b); if ((sa && !sb) || (sa === sb && idx.get(a) > idx.get(b))) continue; }
    adj.get(a).push(b); indeg.set(b, indeg.get(b) + 1);
  }
  const succ = new Map(ids.map((i) => [i, []])), pred = new Map(ids.map((i) => [i, []]));
  const state = new Map(), post = [];
  const visit = (v) => {
    state.set(v, 1);
    for (const w of adj.get(v)) {
      if (state.get(w) === 1) continue;                      // back edge: ignored for layering only
      succ.get(v).push(w); pred.get(w).push(v);
      if (!state.get(w)) visit(w);
    }
    state.set(v, 2); post.push(v);
  };
  ids.filter((i) => !indeg.get(i)).forEach((i) => state.get(i) || visit(i));
  ids.forEach((i) => state.get(i) || visit(i));
  const topo = post.reverse(), layer = new Map();
  topo.forEach((v) => layer.set(v, Math.max(0, ...pred.get(v).map((u) => layer.get(u) + 1))));
  topo.forEach((v) => { if (!pred.get(v).length && succ.get(v).length) layer.set(v, Math.min(...succ.get(v).map((w) => layer.get(w))) - 1); });
  const cols = [];
  ids.forEach((v) => { const l = layer.get(v); (cols[l] = cols[l] || []).push(v); });
  const pos = new Map();
  const place = () => cols.forEach((c) => c.forEach((v, i) => pos.set(v, i - (c.length - 1) / 2)));
  const sweep = (nb, order) => order.forEach((ci) => {
    const c = cols[ci]; if (!c) return;
    const key = (v) => { const n = nb.get(v); return n.length ? n.reduce((s, u) => s + pos.get(u), 0) / n.length : pos.get(v); };
    const keys = new Map(c.map((v) => [v, key(v)]));
    c.sort((a, b) => keys.get(a) - keys.get(b) || idx.get(a) - idx.get(b)); place();
  });
  place();
  const fwd = cols.map((_, i) => i), bwd = [...fwd].reverse();
  sweep(pred, fwd); sweep(succ, bwd); sweep(pred, fwd);
  return cols.filter(Boolean);
}

function pairsOf(edges, map) {
  const p = new Map();
  for (const e of edges) { const a = map(e.source), b = map(e.target); if (a && b && a !== b) p.set(a + SEP + b, (p.get(a + SEP + b) || 0) + 1); }
  return p;
}

function childWidth(id) {
  const n = M.nodes.get(id);
  const runs = runsLabel(id);
  return Math.ceil(tw(clip(n.label), 13, 500)) + 22 + (n.kind === "task" ? 18 : 0) + (n.meta?.dbt_model || n.meta?.dbt_seed ? 28 : 0)
    + (runs ? Math.ceil(tw(runs, 10.5, 600)) + 12 : 0);
}

function layoutInside(gid) {
  const g = M.nodes.get(gid), kids = [...M.children.get(gid)];
  let cols;
  if (g.kind === "flow") {
    const rank = (id) => ({ tasks: 0, errors: 1, finally: 2 }[M.nodes.get(id).meta?.section] ?? 0);
    cols = [kids.sort((a, b) => rank(a) - rank(b) || (M.nodes.get(a).line || 0) - (M.nodes.get(b).line || 0))];
  } else {
    const inside = new Set(kids);
    kids.sort((a, b) => label(a).localeCompare(label(b)));
    cols = layerize(kids, pairsOf(M.edges.filter((e) => inside.has(e.source) && inside.has(e.target)), (x) => x));
  }
  const title = tw(g.label, 13.5, 700) + tw(summary(gid), 11, 500) + 104;
  const colW = cols.map((c) => Math.max(...c.map(childWidth)));
  const colH = cols.map((c) => c.length * CH + (c.length - 1) * CGAP);
  const innerH = Math.max(...colH);
  const innerW = colW.reduce((a, b) => a + b, 0) + SUBGAP * (cols.length - 1);
  const w = Math.max(innerW + PAD * 2, title), out = [];
  let x = PAD + (w - PAD * 2 - innerW) / 2;
  cols.forEach((c, ci) => {
    let y = HEAD + (innerH - colH[ci]) / 2;
    c.forEach((id) => { out.push({ id, x, y, w: colW[ci], h: CH }); y += CH + CGAP; });
    x += colW[ci] + SUBGAP;
  });
  return { w, h: HEAD + innerH + PAD, kids: out };
}

function computeLayout() {
  const rects = new Map();
  const cols = layerize(M.tops.slice().sort((a, b) => M.order.get(a) - M.order.get(b)), pairsOf(M.edges, M.top));
  const sized = new Map();
  for (const id of M.tops) {
    const n = M.nodes.get(id), tag = tagFor(id);
    if (isGroup(id) && S.open.has(id)) sized.set(id, { ...layoutInside(id), type: "group", tag });
    else if (isGroup(id)) sized.set(id, { w: Math.max(132, tw(clip(n.label, 26), 14, 650) + 78, tw(summary(id), 11.5) + 56), h: BH, type: "block", tag });
    else sized.set(id, { w: Math.max(132, tw(clip(n.label, 26), 14, 650) + 52), h: TOP_H, type: "top", tag });
  }
  let x = 0;
  for (const col of cols) {
    const w = Math.max(...col.map((id) => sized.get(id).w));
    const extra = (id) => (sized.get(id).tag ? 12 : 0);
    const total = col.reduce((s, id) => s + sized.get(id).h + extra(id), 0) + GAPY * (col.length - 1);
    let y = -total / 2;
    for (const id of col) {
      const s = sized.get(id), r = { x: x + (w - s.w) / 2, y, w: s.w, h: s.h, type: s.type, tag: s.tag };
      rects.set(id, r);
      (s.kids || []).forEach((k) => rects.set(k.id, { x: r.x + k.x, y: r.y + k.y, w: k.w, h: k.h, type: "child" }));
      y += s.h + extra(id) + GAPY;
    }
    x += w + GAPX;
  }
  let x1 = Infinity, y1 = Infinity, x2 = -Infinity, y2 = -Infinity;
  for (const r of rects.values()) { x1 = Math.min(x1, r.x); y1 = Math.min(y1, r.y); x2 = Math.max(x2, r.x + r.w); y2 = Math.max(y2, r.y + r.h + (r.tag ? 12 : 0)); }
  return { rects, bbox: rects.size ? { x: x1, y: y1, w: x2 - x1, h: y2 - y1 } : null };
}

function aggregate() {
  const recs = new Map(), of = new Map();
  for (const e of M.edges) {
    const u = vis(e.source), v = vis(e.target);
    if (u === v || !L.rects.has(u) || !L.rects.has(v)) continue;
    const key = u + SEP + v, rkey = v + SEP + u;
    let r = recs.get(key) || recs.get(rkey);
    if (!r) recs.set(key, (r = { key, u, v, fwd: [], ret: [] }));
    (r.u === u ? r.fwd : r.ret).push(e);
    of.set(e.id, r);
  }
  for (const r of recs.values()) {
    if (r.ret.length > r.fwd.length) { [r.u, r.v] = [r.v, r.u]; [r.fwd, r.ret] = [r.ret, r.fwd]; }
    r.all = [...r.fwd, ...r.ret];
    r.conf = r.all.reduce((c, e) => (RANK[e.confidence] < RANK[c] ? e.confidence : c), "exact");
    const dbt = r.fwd.length && r.fwd.every((e) => e.kind === "feeds" && e.evidence.some((v) => v.lens === "dbt"));
    r.dbt = dbt ? M.nodes.get(r.fwd[0].target)?.meta?.dbt_project || true : null;
  }
  return { recs, of };
}

// ---- geometry of one connection
function route(ru, rv) {
  if (rv.x >= ru.x + ru.w - 2) return ["R", "L"];
  if (rv.x + rv.w <= ru.x + 2) return ["L", "R"];
  return ["R", "R"];
}
function pathFor(p1, s1, p2, s2) {
  const dir = (s) => (s === "R" ? 1 : -1);
  let d1, d2;
  if (s1 === s2) d1 = d2 = 30 + Math.abs(p2.y - p1.y) * 0.22;
  else d1 = d2 = Math.max(26, Math.abs(p2.x - p1.x) * 0.45);
  const c1 = { x: p1.x + dir(s1) * d1, y: p1.y }, c2 = { x: p2.x + dir(s2) * d2, y: p2.y };
  return { d: `M${p1.x},${p1.y} C${c1.x},${c1.y} ${c2.x},${c2.y} ${p2.x},${p2.y}`,
    mid: { x: (p1.x + 3 * c1.x + 3 * c2.x + p2.x) / 8, y: (p1.y + 3 * c1.y + 3 * c2.y + p2.y) / 8 } };
}
function connect(list) {          // list of {a, b} rect ids -> adds .p1 .p2 .s1 .s2 with ports spread along each side
  const ports = new Map();
  const ask = (id, side, item, end, otherY) => { const k = id + SEP + side; if (!ports.has(k)) ports.set(k, []); ports.get(k).push({ item, end, otherY }); };
  for (const it of list) {
    const ru = L.rects.get(it.a), rv = L.rects.get(it.b);
    [it.s1, it.s2] = route(ru, rv);
    ask(it.a, it.s1, it, "p1", rv.y + rv.h / 2); ask(it.b, it.s2, it, "p2", ru.y + ru.h / 2);
  }
  for (const [k, arr] of ports) {
    const [id, side] = k.split(SEP), r = L.rects.get(id);
    arr.sort((x, y) => x.otherY - y.otherY);
    const span = Math.min(r.h - 10, Math.max(0, (arr.length - 1) * 9)), y0 = r.y + r.h / 2 - span / 2;
    arr.forEach((p, i) => { p.item[p.end] = { x: side === "R" ? r.x + r.w : r.x, y: arr.length === 1 ? r.y + r.h / 2 : y0 + (span * i) / (arr.length - 1) }; });
  }
}

// ====================================================================== render
function icon(id) {
  const k = M.nodes.get(id).kind;
  const name = { schema: "db", flow: "flow", dir: "folder", files: "folder", external: "globe", file: "file", module: "code",
    unknown: "what", unresolved: "what", table: "db", stage: "db" }[k] || "file";
  return `<path class="ico" d="${ICON[name]}"/>`;
}
function tagSvg(text, x, y) {
  const w = tw(text, 10.5) + 14;
  return `<g class="tag" transform="translate(${x},${y})"><rect width="${w}" height="18" rx="9"/><text x="7" y="12.5">${esc(text)}</text></g>`;
}

function render(keepCam = true) {
  L = computeLayout();
  A = aggregate();
  const changedLeaves = new Set(S.data.nodes.filter((n) => n.change).map((n) => n.id));
  const impact = new Set();
  if (S.showChanges) {
    const q = [...changedLeaves].flatMap((id) => (isGroup(id) ? M.children.get(id) : [id]));
    const seen = new Set(q);
    while (q.length) for (const e of M.dOut.get(q.pop()) || []) if (!seen.has(e.target)) { seen.add(e.target); impact.add(e.target); q.push(e.target); }
  }
  const groups = [], nodes = [], taskNo = new Map();
  for (const [gid, kids] of M.children) if (M.nodes.get(gid).kind === "flow") {
    let i = 0;
    kids.filter((k) => (M.nodes.get(k).meta?.section || "tasks") === "tasks").sort((a, b) => (M.nodes.get(a).line || 0) - (M.nodes.get(b).line || 0)).forEach((k) => taskNo.set(k, ++i));
  }
  for (const [id, r] of L.rects) {
    const n = M.nodes.get(id), c = "c-" + cat(id), sel = S.sel?.type === "node" && S.sel.id === id ? " sel" : "";
    const title = `<title>${esc(n.label)} · ${esc(KIND_ES[n.kind] || n.kind)}\n${esc(explain(id).text)}</title>`;
    if (r.type === "group") {
      groups.push(`<g class="grp ${c}${sel}" data-id="${esc(id)}" transform="translate(${r.x},${r.y})">${title}
        <rect class="gbox" width="${r.w}" height="${r.h}" rx="13"/>
        <g transform="translate(12,10)">${icon(id)}</g>
        <text class="t" x="34" y="23">${esc(n.label)}</text>
        <text class="s" x="${40 + tw(n.label, 13.5, 700)}" y="23">${esc(summary(id))}</text>
        <g class="info" data-info="1" transform="translate(${r.w - 50},7)"><rect width="22" height="22" rx="11"/><text x="11" y="15.5" text-anchor="middle">i</text></g>
        <text class="chev" x="${r.w - 20}" y="24">−</text>
        ${r.tag ? tagSvg(r.tag, 12, r.h - 9) : ""}</g>`);
      continue;
    }
    const kids = isGroup(id) ? M.children.get(id) : [];
    const nChanged = kids.filter((k) => changedLeaves.has(k)).length;
    const cls = ["n", r.type, c, n.change || nChanged ? "changed" : "",
      impact.has(id) || kids.some((k) => impact.has(k)) ? "impact" : "",
      r.type === "child" && !M.touched.has(id) ? "nodata" : "", sel].join(" ");
    let inner;
    if (r.type === "child") {
      const no = taskNo.get(id), dbt = n.meta?.dbt_model || n.meta?.dbt_seed;
      inner = `<rect class="box" width="${r.w}" height="${r.h}" rx="8"/>
        ${no ? `<text class="num" x="9" y="18.5">${no}</text>` : ""}
        <text class="t" x="${n.kind === "task" ? 27 : 11}" y="18.5">${esc(clip(n.label))}</text>
        ${dbt ? `<text class="mini" x="${r.w - 26}" y="18">dbt</text>` : ""}
        ${runsLabel(id) ? `<text class="runs" x="${r.w - 9}" y="18" text-anchor="end">${esc(runsLabel(id))}</text>` : ""}`;
    } else {
      const sub = r.type === "block" ? summary(id) : KIND_ES[n.kind] || n.kind;
      inner = `<rect class="box" width="${r.w}" height="${r.h}" rx="11"/>
        <rect class="bar" x="7" y="9" width="4" height="${r.h - 18}" rx="2"/>
        <g transform="translate(19,${r.type === "block" ? 11 : 8})">${icon(id)}</g>
        <text class="t" x="42" y="${r.type === "block" ? 24 : 21}">${esc(clip(n.label, 26))}</text>
        <text class="s" x="42" y="${r.type === "block" ? 42 : 37}">${esc(sub)}</text>
        ${r.type === "block" ? `<text class="chev" x="${r.w - 19}" y="25">+</text>` : ""}
        ${r.tag ? tagSvg(r.tag, 12, r.h - 9) : ""}`;
    }
    const badge = nChanged ? `<g class="badge" transform="translate(${r.w - 4},2)"><circle r="9"/><text text-anchor="middle" y="3.5">${nChanged}</text></g>` : "";
    nodes.push(`<g class="${cls}" data-id="${esc(id)}" transform="translate(${r.x},${r.y})">${title}${inner}${badge}</g>`);
  }

  // data arrows
  const items = [...A.recs.values()].map((r) => ({ a: r.u, b: r.v, rec: r }));
  connect(items);
  const edges = [], pills = [];
  for (const it of items) {
    const r = it.rec, p = pathFor(it.p1, it.s1, it.p2, it.s2);
    const fine = L.rects.get(r.u).type === "child" || L.rects.get(r.v).type === "child";
    const n = r.fwd.length, width = Math.min(3.4, 1.5 + Math.log2(n) * 0.55);
    const cls = ["e", fine ? "fine" : "", it.s1 === it.s2 ? "arc" : "", r.conf, r.ret.length ? "ret" : "", r.all.some((e) => e.change) ? "added" : ""].join(" ");
    edges.push(`<g class="${cls}" data-edge="${esc(r.key)}"><path class="line" d="${p.d}" style="stroke-width:${width}"/>
      <circle class="knot" cx="${it.p1.x}" cy="${it.p1.y}" r="2.6"/><path class="hit" d="${p.d}"/></g>`);
    if (!fine && (n > 1 || r.ret.length || r.dbt)) {
      const text = (r.dbt ? "dbt · " : "") + n + (r.ret.length ? ` ↩ ${r.ret.length}` : "");
      const w = tw(text, 11, 600) + 16;
      pills.push(`<g class="pill${r.dbt ? " dbt" : ""}" data-edge="${esc(r.key)}"${typeof r.dbt === "string" ? ` data-dbt="${esc(r.dbt)}"` : ""} transform="translate(${p.mid.x},${p.mid.y})">
        <title>${n} ${n === 1 ? "conexión" : "conexiones"} de ${esc(label(r.u))} a ${esc(label(r.v))}${r.ret.length ? `, ${r.ret.length} de vuelta` : ""}${r.dbt ? " (hechas por dbt)" : ""}. Clic para ver la evidencia.</title>
        <rect x="${-w / 2}" y="-10" width="${w}" height="20" rx="10"/><text text-anchor="middle" y="4">${esc(text)}</text></g>`);
    }
  }

  // orchestration connectors (shown on demand)
  const ctl = [];
  const container = (id) => (L.rects.has(id) ? id : L.rects.has(M.parent.get(id)) ? M.parent.get(id) : null);
  for (const e of M.control) {
    const a = container(vis(e.source)) || container(e.source);
    let targets;
    if (M.dbt.has(e.target)) targets = new Set([...M.nodes.values()].filter((n) => n.meta?.dbt_project === e.target).map((n) => container(M.top(n.id))));
    else targets = new Set([container(vis(e.target)) || container(e.target)]);
    for (const b of targets) if (a && b && a !== b) ctl.push({ a, b, e });
  }
  connect(ctl);
  const ctlSvg = ctl.map((c, i) => {
    const p = pathFor(c.p1, c.s1, c.p2, c.s2);
    return `<g class="ctl" data-ctl="${i}"><path class="line" d="${p.d}"/><text x="${p.mid.x}" y="${p.mid.y - 5}" text-anchor="middle">${esc(VERB[c.e.kind])}</text></g>`;
  });

  $("#l-groups").innerHTML = groups.join("");
  $("#l-ctl").innerHTML = ctlSvg.join("");
  $("#l-edges").innerHTML = edges.join("");
  $("#l-nodes").innerHTML = nodes.join("");
  $("#l-pills").innerHTML = pills.join("");
  nodeEls = new Map(); edgeEls = new Map();
  world.querySelectorAll("[data-id]").forEach((el) => nodeEls.set(el.dataset.id, el));
  $("#l-edges").querySelectorAll("[data-edge]").forEach((el) => edgeEls.set(el.dataset.edge, { g: el, path: el.querySelector(".line") }));
  $("#l-pills").querySelectorAll("[data-edge]").forEach((el) => { const x = edgeEls.get(el.dataset.edge); if (x) x.pill = el; });
  ctlEls = ctl.map((c, i) => ({ ...c, el: $("#l-ctl").children[i] }));

  const empty = $("#empty");
  empty.hidden = L.rects.size > 0;
  if (!L.rects.size) empty.innerHTML = S.mode === "data"
    ? "No se detectó flujo de datos en este proyecto.<br>Prueba la vista <b>Código</b> para ver cómo se importan los módulos."
    : "No se detectaron imports entre módulos.";
  const tray = $("#tray");
  tray.hidden = !M.tray.length;
  tray.innerHTML = M.tray.length ? `<span>${S.mode === "data" ? "Sin conexiones de datos detectadas:" : "Sin imports:"}</span>` +
    M.tray.slice(0, 8).map((id) => `<button data-tray="${esc(id)}">${esc(label(id))}</button>`).join("") +
    (M.tray.length > 8 ? `<span>+${M.tray.length - 8}</span>` : "") : "";
  world.classList.toggle("show-ctl", S.showCtl);
  if (!keepCam) fit(false);
  applyFocus();
}

// ====================================================================== focus (hover / selection / tour)
function focusSet(id) {
  const ids = new Set([id]), nodes = new Set([id]), edges = new Set(), ctl = new Set();
  if (isGroup(id)) M.children.get(id).forEach((c) => { ids.add(c); nodes.add(c); });
  for (const r of A.recs.values()) if (ids.has(r.u) || ids.has(r.v)) { edges.add(r.key); nodes.add(r.u); nodes.add(r.v); }
  ctlEls.forEach((c, i) => { if (ids.has(c.a) || ids.has(c.b)) { ctl.add(i); nodes.add(c.a); nodes.add(c.b); } });
  return { nodes, edges, ctl };
}
function applyFocus() {
  let f = null, cur = null;
  if (S.tour) {
    const t = S.tour, nodes = new Set([t.start, ...t.steps.map((e) => e.target)]), edges = new Set();
    t.steps.forEach((e) => { const r = A.of.get(e.id); if (r) edges.add(r.key); });
    f = { nodes, edges, ctl: new Set() };
    const last = t.steps[t.steps.length - 1];
    cur = { node: last ? last.target : t.start, edge: last && A.of.get(last.id)?.key };
  } else if (S.hover) f = focusSet(S.hover);
  else if ((S.sel?.type === "node" || S.sel?.type === "group") && L.rects.has(S.sel.id)) f = focusSet(S.sel.id);
  else if (S.sel?.type === "edge" && A.recs.has(S.sel.id)) { const r = A.recs.get(S.sel.id); f = { nodes: new Set([r.u, r.v]), edges: new Set([r.key]), ctl: new Set() }; }
  world.classList.toggle("focus", !!f);
  const lit = new Set();
  if (f) f.nodes.forEach((id) => { lit.add(vis(id)); lit.add(id); const p = M.parent.get(id); if (p) lit.add(p); });
  nodeEls.forEach((el, id) => {
    el.classList.toggle("on", lit.has(id));
    el.classList.toggle("cur", !!cur && (cur.node === id || vis(cur.node) === id));
  });
  edgeEls.forEach((x, key) => {
    const on = !!f && f.edges.has(key);
    x.g.classList.toggle("on", on); x.g.classList.toggle("trail", on && !!S.tour);
    x.g.classList.toggle("cur", !!cur && cur.edge === key);
    if (x.pill) x.pill.classList.toggle("on", on);
  });
  ctlEls.forEach((c, i) => c.el.classList.toggle("on", !!f && f.ctl.has(i)));
}

// ====================================================================== camera
function applyCam() {
  world.setAttribute("transform", `translate(${S.cam.x},${S.cam.y}) scale(${S.cam.k})`);
  if (!L || !M) return;
  const w = viewSize().w, right = [], left = [];
  for (const id of M.tops) {
    const r = L.rects.get(id); if (!r) continue;
    if (r.x * S.cam.k + S.cam.x > w - 30) right.push(id); else if ((r.x + r.w) * S.cam.k + S.cam.x < 30) left.push(id);
  }
  const set = (el, ids, text) => { el.hidden = !ids.length; el.textContent = text; el.onclick = () => frame(ids.slice(0, 2), 120); };
  set($("#more-r"), right, `${right.length} más →`); set($("#more-l"), left.reverse(), `← ${left.length} más`);
}
function viewSize() { const b = svg.getBoundingClientRect(); return { w: b.width, h: b.height }; }
let camAnim = 0;
function animateCam(to, ms = 380) {
  const from = { ...S.cam }, t0 = performance.now(), run = ++camAnim;
  const step = () => {
    if (run !== camAnim) return;
    const t = Math.min(1, (performance.now() - t0) / ms), k = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
    S.cam = { x: from.x + (to.x - from.x) * k, y: from.y + (to.y - from.y) * k, k: from.k + (to.k - from.k) * k };
    applyCam();
    if (t < 1) requestAnimationFrame(step);
  };
  setTimeout(() => { if (run === camAnim) { S.cam = { ...to }; applyCam(); } }, ms + 60);   // lands even if frames are throttled
  requestAnimationFrame(step);
}
function fit(animate = true) {
  if (!L.bbox) return;
  const v = viewSize(), m = 36, b = L.bbox;
  const k = Math.max(MIN_FIT, Math.min(1.15, (v.w - m * 2) / b.w, (v.h - m * 2 - 30) / b.h));
  const x = b.w * k <= v.w - m * 2 ? (v.w - b.w * k) / 2 - b.x * k : m - b.x * k;     // too wide: start at the source end
  const y = b.h * k <= v.h - m * 2 ? (v.h - b.h * k) / 2 - b.y * k : m - b.y * k;
  const to = { x, y, k };
  if (animate) animateCam(to); else { camAnim++; S.cam = to; applyCam(); }
}
function zoomAt(cx, cy, factor) {
  const k = Math.max(0.3, Math.min(2.2, S.cam.k * factor)), f = k / S.cam.k;
  camAnim++;
  S.cam = { x: cx - (cx - S.cam.x) * f, y: cy - (cy - S.cam.y) * f, k };
  applyCam();
}
function frame(ids, pad = 90, bottom = 0) {       // bring these rects into view, zoomed for reading
  const rs = ids.map((id) => L.rects.get(vis(id)) || L.rects.get(id)).filter(Boolean);
  if (!rs.length) return;
  const x1 = Math.min(...rs.map((r) => r.x)) - pad, y1 = Math.min(...rs.map((r) => r.y)) - pad;
  const x2 = Math.max(...rs.map((r) => r.x + r.w)) + pad, y2 = Math.max(...rs.map((r) => r.y + r.h)) + pad;
  const v = viewSize(), vh = v.h - bottom, vw = v.w - ($("#panel").hidden || v.w < 760 ? 0 : 370);
  const k = Math.max(0.55, Math.min(1.1, vw / (x2 - x1), vh / (y2 - y1)));
  animateCam({ k, x: vw / 2 - ((x1 + x2) / 2) * k, y: vh / 2 - ((y1 + y2) / 2) * k });
}

// ====================================================================== interaction
function toggleGroup(id, open) {
  const before = L.rects.get(id);
  if (!before) return;
  const sx = before.x * S.cam.k + S.cam.x, sy = before.y * S.cam.k + S.cam.y;
  if (open ?? !S.open.has(id)) S.open.add(id); else S.open.delete(id);
  render();
  const after = L.rects.get(id);
  camAnim++;
  S.cam.x = sx - after.x * S.cam.k; S.cam.y = sy - after.y * S.cam.k;      // the block stays where you clicked it
  applyCam();
  const v = viewSize(), right = (after.x + after.w) * S.cam.k + S.cam.x, bot = (after.y + after.h) * S.cam.k + S.cam.y;
  if (right > v.w - 20 || bot > v.h - 20 || sy < 10) frame([id], 60);
  syncOpenButton();
}
function syncOpenButton() {
  const groups = [...M.children.keys()].filter((g) => L.rects.has(g));
  $("#btn-open").textContent = groups.length && groups.every((g) => S.open.has(g)) ? "Cerrar todo" : "Abrir todo";
}
function reveal(id) {
  const p = M.parent.get(id);
  if (p && !S.open.has(p)) { S.open.add(p); render(); syncOpenButton(); }
  select({ type: "node", id });
  if (L.rects.has(id)) frame([id], 160);
}
function select(sel) {
  S.sel = sel;
  nodeEls.forEach((el, id) => el.classList.toggle("sel", sel?.type === "node" && sel.id === id));
  renderPanel();
  applyFocus();
}

let drag = null, moved = false;
svg.addEventListener("pointerdown", (ev) => { drag = { x: ev.clientX, y: ev.clientY, cx: S.cam.x, cy: S.cam.y }; moved = false; });
window.addEventListener("pointermove", (ev) => {
  if (!drag) return;
  const dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
  if (!moved && Math.hypot(dx, dy) < 4) return;
  moved = true; camAnim++; svg.classList.add("panning");
  S.cam.x = drag.cx + dx; S.cam.y = drag.cy + dy; applyCam();
});
window.addEventListener("pointerup", () => { drag = null; svg.classList.remove("panning"); });
svg.addEventListener("wheel", (ev) => {
  ev.preventDefault();
  const b = svg.getBoundingClientRect();
  if (ev.ctrlKey || ev.metaKey) zoomAt(ev.clientX - b.left, ev.clientY - b.top, Math.exp(-ev.deltaY * 0.01));
  else { camAnim++; S.cam.x -= ev.deltaX; S.cam.y -= ev.deltaY; applyCam(); }
}, { passive: false });
svg.addEventListener("click", (ev) => {
  if (moved) return;
  const pill = ev.target.closest("[data-dbt]"), node = ev.target.closest("[data-id]"), edge = ev.target.closest("[data-edge]");
  const info = ev.target.closest("[data-info]");
  if (pill) return select({ type: "dbt", id: pill.dataset.dbt });
  if (node) {
    const id = node.dataset.id;
    if (info) return select({ type: "node", id });
    if (isGroup(id)) { if (!S.tour) toggleGroup(id); return select(S.open.has(id) ? { type: "group", id } : null); }
    return select({ type: "node", id });
  }
  if (edge) return select({ type: "edge", id: edge.dataset.edge });
  select(null);
});
svg.addEventListener("mouseover", (ev) => {
  if (drag) return;
  const node = ev.target.closest("[data-id]"), edge = ev.target.closest("[data-edge]");
  if (node) { S.hover = node.dataset.id; if (!S.tour) applyFocus(); }
  else if (edge) {
    const r = A.recs.get(edge.dataset.edge);
    if (r) status(`${label(r.u)} → ${label(r.v)} · ${r.fwd.length} ${r.fwd.length === 1 ? "conexión" : "conexiones"}${r.ret.length ? ` (y ${r.ret.length} de vuelta)` : ""} · ${CONF_ES[r.conf]} · clic para ver la evidencia`);
  }
});
svg.addEventListener("mouseout", (ev) => {
  if (ev.target.closest("[data-id]") && S.hover) { S.hover = null; if (!S.tour) applyFocus(); }
  if (ev.target.closest("[data-edge]")) status("");
});

// ====================================================================== panel
function loc(file, line, text) {
  if (!file) return "";
  const href = `vscode://file/${encodeURI(S.data.root + "/" + file)}${line ? ":" + line : ""}`;
  return `<a class="loc" href="${href}" title="Abrir en el editor">${esc(text || file + (line ? ":" + line : ""))}</a>`;
}
const go = (id) => `<span class="go" data-go="${esc(id)}">${esc(label(id))}</span>`;
const confChip = (c) => `<span class="conf ${c}">${CONF_ES[c]}</span>`;
const evHtml = (e) => e.evidence.map((v) => `<div class="ev">${esc(v.detail)} ${loc(v.file, v.line)}</div>`).join("");
function edgeLi(e, other, arrow) {
  return `<li><div class="row">${arrow} ${M.nodes.has(other) ? go(other) : `<b>${esc(label(other))}</b>`} <span class="meta">${esc(VERB[e.kind] || e.kind)}</span>${confChip(e.confidence)}</div>${evHtml(e)}</li>`;
}
function changeChip(n) { return n.change ? `<span class="kind changed">${n.change === "added" ? "nuevo" : "modificado"}</span>` : ""; }

function renderPanel() {
  const panel = $("#panel"), body = $("#panel-body"), sel = S.sel;
  panel.hidden = !sel || sel.type === "group";
  if (panel.hidden) return;
  let html = "";
  if (sel.type === "changes") {
    const ch = S.data.changes || {}, changed = S.data.nodes.filter((n) => n.change);
    html = `<h2>Cambios</h2><p class="meta">Desde la línea base${ch.baseline_at ? " del " + esc(ch.baseline_at.replace("T", " ").slice(0, 16)) + " UTC" : ""}.</p>
      ${changed.length ? `<ul class="list">${changed.map((n) => `<li>${changeChip(n)} ${M.nodes.has(n.id) ? go(n.id) : esc(n.label)} <span class="meta">${esc(KIND_ES[n.kind] || n.kind)}</span></li>`).join("")}</ul>`
        : `<p class="empty">Nada todavía. Cuando tú o tu agente editen archivos, aparecerán aquí.</p>`}
      ${(ch.removed || []).length ? `<h3>Eliminados</h3><ul class="list">${ch.removed.map((n) => `<li>${esc(n.label)} <span class="meta">${esc(KIND_ES[n.kind] || n.kind)}</span></li>`).join("")}</ul>` : ""}
      <div class="actions"><button id="btn-baseline" title="Los cambios se medirán desde este momento">Reiniciar línea base</button></div>`;
  } else if (sel.type === "edge") {
    const r = A.recs.get(sel.id);
    if (!r) return select(null);
    const li = (e) => `<li><div class="row">${go(e.source)} <span class="meta">${esc(VERB[e.kind] || e.kind)}</span> ${go(e.target)}${confChip(e.confidence)}</div>${evHtml(e)}</li>`;
    html = `<h2>${esc(label(r.u))} → ${esc(label(r.v))}</h2>
      <p class="meta">${r.fwd.length} ${r.fwd.length === 1 ? "conexión" : "conexiones"}${r.dbt ? " hechas por dbt" : ""}. La línea se dibuja con la certeza más baja del grupo: ${confChip(r.conf)}</p>
      <ul class="list">${r.fwd.map(li).join("")}</ul>
      ${r.ret.length ? `<h3>De vuelta (${r.ret.length})</h3><ul class="list">${r.ret.map(li).join("")}</ul>` : ""}`;
  } else if (sel.type === "dbt") {
    const n = M.dbt.get(sel.id);
    if (!n) return select(null);
    const models = [...M.nodes.values()].filter((x) => x.meta?.dbt_project === n.id);
    const runs = M.control.filter((e) => e.kind === "runs" && e.target === n.id);
    html = `<h2>${esc(n.label)}</h2><div><span class="kind">proyecto dbt</span></div>
      <div style="margin-top:6px">${loc(n.file, n.line)}</div>
      <div class="meta">identidad de tablas: ${esc(n.meta?.identity || "")}</div>
      ${runs.length ? `<h3>Lo ejecuta</h3><ul class="list">${runs.map((e) => edgeLi(e, e.source, "←")).join("")}</ul>` : ""}
      <h3>Modelos y seeds (${models.length})</h3><ul class="list">${models.map((m) => `<li>${go(m.id)} <span class="meta">${esc(m.meta.materialized || "seed")}</span></li>`).join("")}</ul>`;
  } else {
    const id = sel.id, n = M.nodes.get(id);
    if (!n) return select(null);
    const m = n.meta || {}, all = S.data.edges.filter((e) => e.kind !== "next");
    const ins = all.filter((e) => e.target === id), outs = all.filter((e) => e.source === id);
    const facts = [];
    if (m.dbt_model) facts.push(`modelo dbt <b>${esc(m.dbt_model)}</b> · ${esc(m.materialized || "")}`);
    if (m.dbt_source) facts.push(`source dbt <b>${esc(m.dbt_source)}</b>`);
    if (m.identity) facts.push(`identidad: ${esc(m.identity)}`);
    if (m.database) facts.push(`base de datos: ${esc(m.database)}`);
    if (m.type) facts.push(`tipo: <b>${esc(m.type)}</b>${m.inside ? ` · dentro de ${esc(m.inside)}` : ""}${m.section && m.section !== "tasks" ? ` · sección ${esc(m.section)}` : ""}`);
    if (m.schedule) facts.push(`cron: <b>${esc(m.schedule.join(", "))}</b>`);
    if (m.paths) facts.push(`rutas: ${esc(m.paths.join(", "))}`);
    if (m.functions?.length) facts.push(`funciones: ${m.functions.map((f) => loc(n.file, f.line, f.name)).join(", ")}`);
    if (m.aliases) facts.push(`también aparece como: ${esc(m.aliases.join(", "))}`);
    if (m.note) facts.push(`<b>nota:</b> ${esc(m.note)}`);
    if (m.uncommitted) facts.push(`<b>sin commit</b> en git`);
    const runners = isGroup(id) ? [] : runnersOf(id), x = explain(id);
    const kids = isGroup(id) ? M.children.get(id) : null;
    const canTour = !kids && S.mode === "data" && ((M.dOut.get(id) || []).length || (M.dIn.get(id) || []).length);
    html = `<h2>${esc(n.label)}</h2>
      <div><span class="kind">${esc(KIND_ES[n.kind] || n.kind)}</span>${changeChip(n)}</div>
      ${n.file ? `<div style="margin-top:6px">${loc(n.file, n.line)}</div>` : n.synthetic || isGroup(id) ? "" : '<div class="meta" style="margin-top:6px">sin definición en el proyecto</div>'}
      ${x.text ? `<p class="desc">${esc(x.text)}</p>` : ""}
      ${x.why ? `<p class="why">${esc(x.why)}</p>` : ""}
      ${srcMark(x)}
      ${x.code && x.code !== x.text ? `<details class="more-code"><summary>${x.src === "ai" ? "Comentario en el código" : "Ver completo"}</summary>${esc(x.code)}</details>` : ""}
      <div class="facts">${facts.map((f) => `<div class="meta">${f}</div>`).join("")}
      ${runners.length ? `<div class="meta">lo ejecuta: ${runners.map((e) => `${go(e.source)} <span>(${esc(label(M.parent.get(e.source) || e.source))})</span>`).join(", ")}</div>` : ""}</div>
      ${canTour ? `<div class="actions">${(M.dIn.get(id) || []).length ? '<button id="t-up">◀ Cómo llega el dato aquí</button>' : ""}${(M.dOut.get(id) || []).length ? '<button class="primary" id="t-down">▶ Hacia dónde va</button>' : ""}</div>` : ""}
      ${kids && !L.rects.has(id) ? '<div class="meta">No está en el mapa: no tiene conexiones en esta vista.</div>' : ""}
      ${kids ? `<div class="actions"${L.rects.has(id) ? "" : " hidden"}><button id="g-toggle">${S.open.has(id) ? "Cerrar bloque" : "Abrir bloque"}</button></div>
        <h3>Contiene (${kids.length})</h3><ul class="list">${kids.map((k) => `<li>${go(k)} <span class="meta">${esc(KIND_ES[M.nodes.get(k).kind] || "")}</span> ${changeChip(M.nodes.get(k))}</li>`).join("")}</ul>` : ""}
      ${ins.length || !kids ? `<h3>Entra (${ins.length})</h3>${ins.length ? `<ul class="list">${ins.map((e) => edgeLi(e, e.source, "←")).join("")}</ul>` : '<p class="empty">nada</p>'}` : ""}
      ${outs.length || !kids ? `<h3>Sale (${outs.length})</h3>${outs.length ? `<ul class="list">${outs.map((e) => edgeLi(e, e.target, "→")).join("")}</ul>` : '<p class="empty">nada</p>'}` : ""}`;
  }
  body.innerHTML = html;
  body.querySelectorAll("[data-go]").forEach((el) => (el.onclick = () => reveal(el.dataset.go)));
  const on = (sel2, fn) => { const el = body.querySelector(sel2); if (el) el.onclick = fn; };
  on("#btn-baseline", async () => { await fetch("/api/baseline", { method: "POST" }); poll(true); });
  on("#g-toggle", () => { toggleGroup(sel.id); renderPanel(); });
  on("#t-down", () => startTour(sel.id, []));
  on("#t-up", () => { const path = pathTo(sel.id); startTour(path.length ? path[0].source : sel.id, path); });
}

// ====================================================================== guided tour
function depth(id, adj, nextOf, memo, stack = new Set()) {
  if (memo.has(id)) return memo.get(id);
  if (stack.has(id)) return 0;
  stack.add(id);
  let best = 0;
  for (const e of adj.get(id) || []) best = Math.max(best, 1 + depth(nextOf(e), adj, nextOf, memo, stack));
  stack.delete(id); memo.set(id, best);
  return best;
}
let downMemo = new Map(), upMemo = new Map();
const down = (id) => depth(id, M.dOut, (e) => e.target, downMemo);
const up = (id) => depth(id, M.dIn, (e) => e.source, upMemo);
function mainSource() {
  const c = [...M.dOut.keys()].filter((id) => !(M.dIn.get(id) || []).length);
  const score = (id) => down(id) + (M.nodes.get(id).kind === "external" ? 0.5 : 0);
  return (c.length ? c : [...M.dOut.keys()]).sort((a, b) => score(b) - score(a))[0];
}
function pathTo(id) {
  const path = [], seen = new Set([id]);
  let cur = id;
  for (;;) {
    const ins = (M.dIn.get(cur) || []).filter((e) => !seen.has(e.source)).sort((a, b) => up(b.source) - up(a.source));
    if (!ins.length) break;
    path.unshift(ins[0]); cur = ins[0].source; seen.add(cur);
  }
  return path;
}
function tourOptions() {
  const t = S.tour;
  if (t.queue.length) return [t.queue[0]];
  const visited = new Set([t.start, ...t.steps.map((e) => e.target)]);
  const cur = t.steps.length ? t.steps[t.steps.length - 1].target : t.start;
  return (M.dOut.get(cur) || []).filter((e) => !visited.has(e.target)).sort((a, b) => down(b.target) - down(a.target));
}
function startTour(start, queue) {
  if (!start) return status("No hay un flujo de datos que recorrer.");
  endTour(true);
  S.tour = { start, steps: [], queue: [...queue], playing: false, speed: 1, run: 0, ended: false };
  S.sel = null; S.hover = null; renderPanel();
  showStep(false);
}
function endTour(silent) {
  if (!S.tour) return;
  S.tour.run++; S.tour = null;
  $("#tour").hidden = true; $("#dot").setAttribute("visibility", "hidden");
  if (!silent) applyFocus();
}
function tourNext(choice) {
  const t = S.tour; if (!t) return;
  const e = choice || tourOptions()[0];
  if (!e) { t.ended = true; t.playing = false; t.run++; return renderTourBar(); }
  if (t.queue[0] === e) t.queue.shift(); else t.queue = [];
  t.steps.push(e);
  showStep(true);
}
function tourPrev() {
  const t = S.tour; if (!t || !t.steps.length) return;
  t.queue.unshift(t.steps.pop()); t.ended = false; t.playing = false;
  showStep(false);
}
function showStep(animate) {
  const t = S.tour, run = ++t.run, e = t.steps[t.steps.length - 1];
  let opened = false;
  for (const id of e ? [e.source, e.target] : [t.start]) { const p = M.parent.get(id); if (p && !S.open.has(p)) { S.open.add(p); opened = true; } }
  if (opened) { render(); syncOpenButton(); } else applyFocus();
  renderTourBar();
  frame(e ? [e.source, e.target] : [t.start], 90, $("#tour").offsetHeight + 30);       // keep the stop above the bar
  const arrive = () => {
    if (!S.tour || S.tour.run !== run) return;
    ring(e ? e.target : t.start);
    if (t.playing && tourOptions().length > 1) { t.playing = false; t.atFork = true; return renderTourBar(); }
    const read = 1500 + 42 * ($("#tour .tour-text")?.textContent.length || 0);       // longer text, longer stop
    if (t.playing) setTimeout(() => { if (S.tour && S.tour.run === run && t.playing) tourNext(); }, Math.min(8000, read) / t.speed);
  };
  if (e && animate) travel(e, run, arrive); else setTimeout(arrive, 450);
}
function travel(e, run, done) {
  const dot = $("#dot"), rec = A.of.get(e.id), ms = 1000 / S.tour.speed, t0 = performance.now() + 380;
  const reversed = rec && !rec.fwd.includes(e);
  let finished = false;
  const finish = () => { if (finished) return; finished = true; dot.setAttribute("visibility", "hidden"); done(); };
  const step = () => {
    if (!S.tour || S.tour.run !== run || finished) return dot.setAttribute("visibility", "hidden");
    const path = rec && edgeEls.get(rec.key)?.path, t = (performance.now() - t0) / ms;
    if (path && t >= 0) {
      const len = path.getTotalLength(), k = Math.min(1, t), eased = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
      const p = path.getPointAtLength(len * (reversed ? 1 - eased : eased));
      dot.setAttribute("cx", p.x); dot.setAttribute("cy", p.y); dot.setAttribute("visibility", "visible");
    }
    if (t >= 1 || !path) return finish();
    requestAnimationFrame(step);
  };
  setTimeout(() => { if (S.tour && S.tour.run === run) finish(); }, ms + 480);      // by the clock, not by frames
  requestAnimationFrame(step);
}
function ring(id) {
  const r = L.rects.get(vis(id)); if (!r) return;
  const el = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  el.setAttribute("class", "ring"); el.setAttribute("x", r.x); el.setAttribute("y", r.y);
  el.setAttribute("width", r.w); el.setAttribute("height", r.h); el.setAttribute("rx", 9);
  $("#l-tour").appendChild(el);
  setTimeout(() => el.remove(), 950);
}
function renderTourBar() {
  const t = S.tour, bar = $("#tour");
  bar.hidden = false;
  const e = t.steps[t.steps.length - 1], cur = e ? e.target : t.start, opts = tourOptions(), n = M.nodes.get(cur);
  const done = t.ended || !opts.length;
  const total = t.steps.length + (done ? 0 : t.queue.length || 1 + down(opts[0].target));
  const top = M.top(cur), x = explain(cur);
  const kind = `${KIND_ES[n.kind] || n.kind}${top !== cur ? " · " + label(top) : ""}`;
  let head, intro = "", how = "";
  if (!e) {
    const sum = S.data.summary;
    head = `<div class="tour-step"><span>Antes de empezar</span></div>
      <div class="tour-text">${sum ? esc(sum.text) : "Vamos a seguir el dato paso a paso, desde donde entra hasta donde termina."}</div>
      ${sum ? srcMark({ src: "ai", stale: sum.stale }) : ""}
      <div class="tour-from">Empieza en <b>${esc(n.label)}</b>${x.text ? ": " + esc(firstBit(x.text, 110)) : ""}</div>
      ${!sum && !(S.data.notes?.fresh) ? '<div class="tour-hint">Para explicaciones más claras, pídele a tu agente de IA: «explica este proyecto con kipu».</div>' : ""}`;
  } else {
    if (top !== cur && top !== M.top(e.source)) { const g = explain(top); if (g.src !== "none") intro = `<div class="tour-chap">Entras a <b>${esc(label(top))}</b>: ${esc(firstBit(g.text, 130))}</div>`; }
    const src = `<b>${esc(label(e.source))}</b>`;
    how = { reads: `Lee de ${src}.`, writes: `Lo escribe ${src}.`, feeds: `Se construye a partir de ${src}.`, passes: `Recibe el resultado de ${src}.` }[e.kind] || "";
    how = `<span>${how}</span>`;
    const best = e.evidence.find((v) => v.confidence === e.confidence) || e.evidence[0];
    const doubt = e.confidence === "exact" ? "" : `<span class="conf ${e.confidence}" title="${esc(best?.detail || "")}">${e.confidence === "inferred" ? "conexión deducida" : "sin resolver"}</span>`;
    head = `<div class="tour-step"><span>Paso ${t.steps.length} de ${total}</span>${top !== cur ? `<span class="chapter">${esc(label(top))}</span>` : ""}</div>
      ${intro}
      <div class="tour-title"><b>${esc(n.label)}</b> <span class="k">${esc(KIND_ES[n.kind] || n.kind)}</span></div>
      <div class="tour-text">${esc(x.text)}</div>
      <div class="tour-how">${how} ${best ? loc(best.file, best.line, "ver en el código ↗") : ""} ${doubt} ${srcMark(x)}</div>`;
  }
  let forks = "";
  if (e && done) forks = `<div class="tour-forks"><div class="q">Fin del recorrido: de aquí el dato no sigue a ningún otro lado.</div></div>`;
  else if (opts.length > 1) forks = `<div class="tour-forks"><div class="q">De aquí salen ${opts.length} caminos. ¿Cuál seguimos?</div><div class="opts">${opts.slice(0, 4).map((o, i) =>
    `<button data-fork="${i}" class="${i ? "" : "main"}"><b>${i ? "" : "★ "}${esc(label(o.target))}</b><span>${esc(firstBit(explain(o.target).text, 64))}</span></button>`).join("")}</div>${opts.length > 4 ? `<div class="k">y ${opts.length - 4} más (ábrelos desde el mapa)</div>` : ""}</div>`;
  bar.innerHTML = `<div class="tour-prog"><i style="width:${total ? Math.round((100 * t.steps.length) / total) : 100}%"></i></div>
    ${head}${forks}
    <div class="tour-ctl">${e ? `<button id="tp">◀ Anterior</button>
      <button id="tplay">${t.playing ? "⏸ Pausa" : "▶ Reproducir"}</button>
      <button id="tn" class="primary" ${done ? "disabled" : ""}>Siguiente ▶</button>`
      : `<button id="tplay" class="primary">▶ Empezar</button><button id="tn">Paso a paso ▶</button>`}
      <button id="tspeed" class="ghost" title="Velocidad de la reproducción">${t.speed}×</button><span class="grow"></span>
      <button id="tclose" class="ghost">✕ Salir</button></div>`;
  const on = (sel, fn) => { const el = bar.querySelector(sel); if (el) el.onclick = fn; };
  on("#tp", tourPrev);
  on("#tn", () => { t.playing = false; t.atFork = false; tourNext(); });
  on("#tplay", () => { t.playing = !t.playing; t.atFork = false; if (t.playing) { t.ended ? startTour(t.start, []) : tourNext(); } else { t.run++; $("#dot").setAttribute("visibility", "hidden"); renderTourBar(); } });
  on("#tspeed", () => { t.speed = { 1: 1.5, 1.5: 2, 2: 0.5, 0.5: 1 }[t.speed]; renderTourBar(); });
  on("#tclose", () => endTour());
  bar.querySelectorAll("[data-fork]").forEach((b) => (b.onclick = () => { t.playing = !!t.atFork; t.atFork = false; tourNext(opts[+b.dataset.fork]); }));
}

// ====================================================================== data & live updates
async function load() {
  S.data = await (await fetch("/api/graph")).json();
  const d = S.data;
  $("#project").textContent = d.project || "";
  document.title = `kipu · ${d.project || ""}`;
  if (!S.mode) S.mode = d.edges.some((e) => DATA.has(e.kind)) ? "data" : "code";
  M = buildModel(); downMemo = new Map(); upMemo = new Map();
  [...S.open].forEach((id) => M.children.has(id) || S.open.delete(id));
  if (S.sel?.type === "node" && !M.nodes.has(S.sel.id)) S.sel = null;
  if (S.tour) {                                              // survive a rescan mid-tour only if the path still exists
    const ids = new Map(d.edges.map((e) => [e.id, e]));
    if (M.nodes.has(S.tour.start) && [...S.tour.steps, ...S.tour.queue].every((e) => ids.has(e.id))) {
      S.tour.steps = S.tour.steps.map((e) => ids.get(e.id)); S.tour.queue = S.tour.queue.map((e) => ids.get(e.id));
    } else { endTour(true); status("El recorrido se cerró: el camino cambió."); }
  }
  const n = d.nodes.filter((x) => x.change).length;
  $("#n-changes").textContent = n;
  document.querySelectorAll("[data-mode]").forEach((b) => b.classList.toggle("on", b.dataset.mode === S.mode));
  $("#btn-tour").hidden = S.mode !== "data" || !M.dOut.size;
  $("#btn-ctl").hidden = !M.control.length;
  render(!S.first);
  syncOpenButton(); renderPanel();
  if (S.tour) renderTourBar();
  if (!S.first && n) { const live = $("#live"); live.classList.remove("pulse"); void live.offsetWidth; live.classList.add("pulse"); }
  S.first = false;
}
async function poll(force) {
  const live = $("#live");
  try {
    const v = await (await fetch("/api/version")).json();
    live.classList.toggle("err", !!v.error);
    live.textContent = v.error ? "● error al analizar (se muestra el último mapa)" : "● en vivo";
    if (force === true || v.version !== S.version) { S.version = v.version; await load(); }
  } catch (_) { live.classList.add("err"); live.textContent = "● desconectado"; }
}

// ====================================================================== controls
document.querySelectorAll("[data-mode]").forEach((b) => (b.onclick = () => {
  if (S.mode === b.dataset.mode) return;
  endTour(true); S.mode = b.dataset.mode; S.sel = null; S.open.clear(); S.first = true; load();
}));
$("#btn-tour").onclick = () => startTour(mainSource(), []);
$("#btn-changes").onclick = (ev) => {
  S.showChanges = !S.showChanges; ev.currentTarget.classList.toggle("on", S.showChanges);
  render(); select(S.showChanges ? { type: "changes" } : null);
};
$("#btn-ctl").onclick = (ev) => { S.showCtl = !S.showCtl; ev.currentTarget.classList.toggle("on", S.showCtl); world.classList.toggle("show-ctl", S.showCtl); };
$("#btn-open").onclick = () => {
  const groups = [...M.children.keys()].filter((g) => L.rects.has(g));
  const all = groups.every((g) => S.open.has(g));
  groups.forEach((g) => (all ? S.open.delete(g) : S.open.add(g)));
  render(false); syncOpenButton();
};
$("#btn-theme").onclick = () => {
  const dark = document.documentElement.dataset.theme !== "dark";
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  try { localStorage.setItem("kipu-theme", dark ? "dark" : "light"); } catch (_) { /* private mode */ }
};
try { const th = localStorage.getItem("kipu-theme"); if (th) document.documentElement.dataset.theme = th; } catch (_) { /* private mode */ }
$("#z-in").onclick = () => { const v = viewSize(); zoomAt(v.w / 2, v.h / 2, 1.25); };
$("#z-out").onclick = () => { const v = viewSize(); zoomAt(v.w / 2, v.h / 2, 0.8); };
$("#z-fit").onclick = () => fit(true);
$("#panel-close").onclick = () => select(null);
$("#tray").onclick = (ev) => { const b = ev.target.closest("[data-tray]"); if (b) select({ type: "node", id: b.dataset.tray }); };
$("#search").addEventListener("keydown", (ev) => {
  if (ev.key !== "Enter") return;
  const q = ev.target.value.trim().toLowerCase(); if (!q) return;
  const all = [...M.nodes.values()];
  const hit = all.find((n) => n.label.toLowerCase() === q) || all.find((n) => n.label.toLowerCase().includes(q) || n.id.toLowerCase().includes(q));
  if (hit) reveal(hit.id); else status(`Sin resultados para “${q}”`);
});
window.addEventListener("keydown", (ev) => {
  if (ev.target.tagName === "INPUT") return;
  if (ev.key === "Escape") { if (S.tour) endTour(); else select(null); }
  if (!S.tour) return;
  if (ev.key === "ArrowRight") { S.tour.playing = false; tourNext(); }
  if (ev.key === "ArrowLeft") tourPrev();
  if (ev.key === " ") { ev.preventDefault(); $("#tplay")?.click(); }
});
window.addEventListener("resize", () => { if (L && !S.tour) fit(false); });

poll(true);
setInterval(poll, 1000);
