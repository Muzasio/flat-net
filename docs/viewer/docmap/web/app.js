// docmap page. Draws only what is in BOOT.facts; nothing here invents links.
"use strict";
const B = window.BOOT, F = B.facts;
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const post = (m) => window.webkit.messageHandlers.docmap.postMessage(m);
const byId = {};
F.nodes.forEach((n) => (byId[n.id] = n));
const CODE = new Set(["function", "method", "class", "namespace", "constant", "module"]);
const FILEOPS = new Set(["reads", "writes", "lists", "creates", "deletes", "moves", "changes", "checks"]);
const CALLS = new Set(["calls", "calls_later", "references", "instantiates"]);

// ---------- views: which nodes and links each diagram shows ----------
function up(id) { // one level up: method -> class, function/constant -> module, heading -> doc
  const n = byId[id];
  return n && ["function", "method", "constant", "heading"].includes(n.kind) && n.parent ? n.parent : id;
}

const VIEWS = {
  overview: {
    label: "Overview",
    keep: (n) => ["module", "class", "namespace", "external_module", "command", "folder", "file"].includes(n.kind),
    edges: (e) => ["imports", "includes", "inherits", "runs_command", "contains"].includes(e.kind) || FILEOPS.has(e.kind),
    map: up,
  },
  calls: {
    label: "Functions & calls",
    keep: (n) => ["function", "method", "class", "module"].includes(n.kind),
    edges: (e) => CALLS.has(e.kind) && (e.to.startsWith("py:") || e.to.startsWith("ts:")),
    map: (id) => id,
  },
  files: {
    label: "Files & commands",
    keep: (n) => ["function", "method", "module", "command", "file", "folder"].includes(n.kind),
    edges: (e) => e.kind === "runs_command" || FILEOPS.has(e.kind),
    map: (id) => id,
  },
  docs: {
    label: "Docs",
    keep: (n) => n.kind === "doc" || CODE.has(n.kind) || ["command", "file", "folder", "external_module"].includes(n.kind),
    edges: (e) => e.kind === "documents" || e.kind === "links",
    map: (id) => (byId[id] && byId[id].kind === "heading" ? byId[id].parent : id),
  },
  runtime: {
    label: "Runtime",
    // callbacks handed to timers, threads, signals and buttons: who registers what, read from the code
    keep: () => true,
    edges: (e) => (e.kind === "references" || e.kind === "calls_later") && !!e.callback_of,
    map: (id) => id,
    note: (e) => e.callback_of + (e.kw ? " " + e.kw + "=" : "") + (e.delay !== undefined ? " " + e.delay + " ms" : ""),
  },
  coverage: { label: "Coverage" },
};

function graphFor(name) {
  const v = VIEWS[name], agg = {}, used = new Set();
  F.edges.forEach((e) => {
    if (!v.edges(e)) return;
    const a = v.map(e.from), b = v.map(e.to);
    if ((a === b && name !== "runtime") || !byId[a] || !byId[b] || !v.keep(byId[a]) || !v.keep(byId[b])) return;
    if (name === "overview" && e.kind === "contains" && !["class", "namespace"].includes(byId[b].kind)) return;
    const key = a + "\u0001" + b;
    const g = agg[key] || (agg[key] = { from: a, to: b, kinds: new Set(), certain: false, count: 0, src: [] });
    g.kinds.add(e.kind); g.count++; g.src.push(e);
    if (v.note) (g.notes = g.notes || new Set()).add(v.note(e));
    if (e.certainty === "certain") g.certain = true;
    used.add(a); used.add(b);
  });
  let nodes = [...used].map((id) => byId[id]);
  if (name === "calls") F.nodes.forEach((n) => { if (["function", "method"].includes(n.kind) && !used.has(n.id)) nodes.push(n); });
  return { nodes, edges: Object.values(agg) };
}

// ---------- layered layout (left to right) ----------
function layout(g) {
  const id = (n) => n.id, out = {}, inn = {};
  g.nodes.forEach((n) => { out[n.id] = []; inn[n.id] = []; });
  // break cycles: depth-first, edges that point back are ignored for ranking
  const state = {}, forward = [];
  const dfs = (u) => {
    state[u] = 1;
    g.edges.filter((e) => e.from === u).forEach((e) => {
      if (state[e.to] === 1) return;
      forward.push(e);
      if (!state[e.to]) dfs(e.to);
    });
    state[u] = 2;
  };
  g.nodes.slice().sort((a, b) => (a.line || 0) - (b.line || 0)).forEach((n) => { if (!state[n.id]) dfs(n.id); });
  forward.forEach((e) => { out[e.from].push(e.to); inn[e.to].push(e.from); });
  const rank = {}, order = [];
  const indeg = {}; g.nodes.forEach((n) => (indeg[n.id] = inn[n.id].length));
  const queue = g.nodes.filter((n) => !indeg[n.id]).map(id);
  while (queue.length) {
    const u = queue.shift(); order.push(u); rank[u] = rank[u] || 0;
    out[u].forEach((w) => { rank[w] = Math.max(rank[w] || 0, rank[u] + 1); if (--indeg[w] === 0) queue.push(w); });
  }
  const cols = [];
  g.nodes.forEach((n) => { const r = rank[n.id] || 0; (cols[r] = cols[r] || []).push(n.id); });
  const pos = {};
  const place = () => cols.forEach((c) => c && c.forEach((u, i) => (pos[u] = i)));
  place();
  for (let pass = 0; pass < 8; pass++) { // barycenter ordering, sweeping both ways
    const list = pass % 2 ? cols.slice().reverse() : cols;
    list.forEach((c) => {
      if (!c) return;
      const bc = {};
      c.forEach((u) => {
        const nb = pass % 2 ? out[u] : inn[u];
        bc[u] = nb.length ? nb.reduce((s, w) => s + pos[w], 0) / nb.length : pos[u];
      });
      c.sort((a, b) => bc[a] - bc[b]);
      c.forEach((u, i) => (pos[u] = i));
    });
  }
  const W = (n) => Math.max(90, 7.2 * Math.max(label(n).length, sub(n).length) + 26), H = 38;
  const GAP = g.edges.some((e) => e.from === e.to) ? 70 : g.edges.some((e) => e.notes) ? 34 : 14;
  const at = {};
  let x = 0;
  const tallest = Math.max(...cols.map((c) => (c ? c.length : 0)));
  cols.forEach((c) => {
    if (!c) return;
    const w = Math.max(...c.map((u) => W(byId[u])));
    const top = ((tallest - c.length) * (H + GAP)) / 2;
    c.forEach((u, i) => (at[u] = { x, y: top + i * (H + GAP), w: W(byId[u]), h: H }));
    x += w + (g.edges.some((e) => e.notes) ? 170 : 90);
  });
  return at;
}
// ---------- hand edits (layout.json): positions, groups, labels, notes, manual links ----------
const L = Object.assign({ version: 1, positions: {}, groups: {}, labels: {}, notes: {}, links: [] }, B.layout || {});
let STALE = B.stale || [];
const undo = [];
function edit(fn) { // every change: remember the old layout (for Undo), apply, save
  undo.push(JSON.stringify(L)); if (undo.length > 100) undo.shift();
  fn(); post({ type: "layout", layout: L }); repaint();
}
const realLabel = (n) => (n.kind === "method" ? n.id.split(":").pop() : n.name);
const label = (n) => L.labels[n.id] || realLabel(n);
const sub = (n) => (L.labels[n.id] ? "= " + realLabel(n) + " · " : "") + n.kind
  + (n.line && CODE.has(n.kind) && n.kind !== "module" ? "  line " + n.line : "");

// ---------- drawing ----------
let current = "overview", G = null, AT = null, view = { x: 0, y: 0, k: 1 }, selected = null, editing = false;
const picked = new Set();
const svg = $("#svg"), world = $("#world");
const NS = "http://www.w3.org/2000/svg";
const mk = (tag, attrs, parent) => { const el = document.createElementNS(NS, tag); for (const k in attrs) el.setAttribute(k, attrs[k]); parent && parent.appendChild(el); return el; };

function curve(a, b) {
  if (a === b) return `M${a.x + a.w - 30},${a.y} C${a.x + a.w + 10},${a.y - 48} ${a.x + a.w + 52},${a.y} ${a.x + a.w},${a.y + a.h / 2}`;
  const x1 = a.x + a.w, y1 = a.y + a.h / 2, x2 = b.x, y2 = b.y + b.h / 2;
  const back = x2 <= x1, dx = back ? 80 : Math.max(40, (x2 - x1) / 2);
  return back ? `M${x1},${y1} C${x1 + dx},${y1 - 60} ${x2 - dx},${y2 - 60} ${x2},${y2}`
    : `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}`;
}

function draw(name) {
  current = name;
  document.querySelectorAll("#views button").forEach((b) => b.classList.toggle("on", b.dataset.v === name));
  const cov = name === "coverage";
  $("#coverage").hidden = !cov; svg.style.display = cov ? "none" : "block";
  $("#legend").style.display = $("#zoom").style.display = cov ? "none" : "flex";
  if (cov) return drawCoverage();
  G = graphFor(name); AT = layout(G);
  repaint(); legend(); fit();
  if (selected && AT[selected]) select(selected, false);
}

function repaint() { // draws the current view from AT (auto layout) + saved positions + your groups and links
  if (!G) return;
  const saved = L.positions[current] || {};
  Object.keys(AT).forEach((id) => { if (saved[id]) { AT[id].x = saved[id][0]; AT[id].y = saved[id][1]; } });
  world.innerHTML = "";
  const gg = mk("g", {}, world), eg = mk("g", {}, world), mg = mk("g", {}, world), ng = mk("g", {}, world);
  (L.groups[current] || []).forEach((grp, gi) => {
    const box = grp.members.map((m) => AT[m]).filter(Boolean);
    if (!box.length) return;
    const x = Math.min(...box.map((a) => a.x)) - 14, y = Math.min(...box.map((a) => a.y)) - 30;
    const w = Math.max(...box.map((a) => a.x + a.w)) - x + 14, h = Math.max(...box.map((a) => a.y + a.h)) - y + 14;
    const missing = grp.members.length - box.length;
    const name = grp.name + (missing ? "  (" + missing + " not in this view)" : "");
    // A box that is not a member but lies inside the rectangle would look like a member: then mark members one by one.
    const intruders = Object.keys(AT).filter((id) => !grp.members.includes(id)).some((id) => {
      const o = AT[id]; return o.x < x + w && o.x + o.w > x && o.y < y + h && o.y + o.h > y;
    });
    const g = mk("g", { class: "group" + (intruders ? " split" : "") }, gg);
    g.dataset.group = gi;
    if (!intruders) {
      mk("rect", { x, y, width: w, height: h, rx: 10 }, g);
      mk("text", { x: x + 10, y: y + 18 }, g).textContent = name;
    } else {
      box.forEach((a) => {
        mk("rect", { x: a.x - 5, y: a.y - 18, width: a.w + 10, height: a.h + 23, rx: 8 }, g);
        mk("text", { x: a.x, y: a.y - 6 }, g).textContent = name;
      });
      mk("title", {}, g).textContent = "Other boxes sit between the members, so the group is shown on each member. Drag the members together to get one box.";
    }
  });
  G.edges.forEach((e) => {
    const p = mk("path", { d: curve(AT[e.from], AT[e.to]), class: "edge " + (e.certain ? "certain" : "resolved"), "marker-end": "url(#arrow)" }, eg);
    p.dataset.from = e.from; p.dataset.to = e.to;
    mk("title", {}, p).textContent = [...e.kinds].join(", ") + (e.certain ? " (certain)" : " (proven indirectly)") + (e.count > 1 ? ", " + e.count + " places" : "");
    if (e.notes) {
      const a = AT[e.from], b = AT[e.to], self = e.from === e.to;
      mk("text", self ? { x: a.x + 6, y: a.y - 8, class: "elabel" }
        : { x: (a.x + a.w + b.x) / 2, y: (a.y + b.y + a.h) / 2 - 6, class: "elabel", "text-anchor": "middle" }, eg)
        .textContent = (self ? "repeats: " : "") + [...e.notes].join(", ");
    }
  });
  L.links.forEach((l) => {
    if (!AT[l.from] || !AT[l.to]) return;
    const p = mk("path", { d: curve(AT[l.from], AT[l.to]), class: "edge manual", "marker-end": "url(#arrow)" }, mg);
    p.dataset.from = l.from; p.dataset.to = l.to;
    mk("title", {}, p).textContent = "Your link (not from the code): " + (l.label || "");
    if (l.label) {
      const a = AT[l.from], b = AT[l.to];
      mk("text", { x: (a.x + a.w + b.x) / 2, y: (a.y + b.y + a.h) / 2 - 6, class: "mlabel" }, mg).textContent = l.label;
    }
  });
  G.nodes.forEach((n) => {
    const a = AT[n.id], g = mk("g", { class: "node" + (picked.has(n.id) ? " picked" : ""), transform: `translate(${a.x},${a.y})` }, ng);
    g.dataset.id = n.id;
    mk("rect", { width: a.w, height: a.h, style: `stroke:var(--k-${n.kind})` }, g);
    mk("rect", { width: 5, height: a.h, style: `fill:var(--k-${n.kind});stroke:none` }, g);
    mk("text", { x: 12, y: 16 }, g).textContent = label(n);
    mk("text", { x: 12, y: 30, class: "sub" }, g).textContent = sub(n);
    if (L.notes[n.id]) mk("text", { x: a.w - 16, y: 14, class: "notemark" }, g).textContent = "✎";
  });
  if (selected && AT[selected]) highlight(selected);
}

function legend() {
  const kinds = [...new Set(G.nodes.map((n) => n.kind))];
  $("#legend").innerHTML = kinds.map((k) => `<span><i style="background:var(--k-${k})"></i>${k.replace("_", " ")}</span>`).join("")
    + '<span><svg><line x1="0" y1="4" x2="28" y2="4" class="edge"/></svg>certain</span>'
    + '<span><svg><line x1="0" y1="4" x2="28" y2="4" class="edge resolved"/></svg>proven indirectly</span>';
}

function setView() { world.setAttribute("transform", `translate(${view.x},${view.y}) scale(${view.k})`); }
function fit() {
  const xs = Object.values(AT);
  if (!xs.length) return;
  const w = Math.max(...xs.map((a) => a.x + a.w)), h = Math.max(...xs.map((a) => a.y + a.h));
  const r = svg.getBoundingClientRect();
  view.k = Math.min(1.2, (r.width - 40) / w, (r.height - 70) / h);
  view.x = (r.width - w * view.k) / 2; view.y = 20; setView();
}
function focus(id) {
  const a = AT[id]; if (!a) return;
  const r = svg.getBoundingClientRect();
  view.k = Math.max(view.k, 0.8);
  view.x = r.width / 2 - (a.x + a.w / 2) * view.k; view.y = r.height / 2 - (a.y + a.h / 2) * view.k; setView();
}

svg.addEventListener("wheel", (ev) => {
  ev.preventDefault();
  const r = svg.getBoundingClientRect(), mx = ev.clientX - r.left, my = ev.clientY - r.top;
  const k = Math.min(3, Math.max(0.08, view.k * (ev.deltaY < 0 ? 1.12 : 1 / 1.12)));
  view.x = mx - ((mx - view.x) * k) / view.k; view.y = my - ((my - view.y) * k) / view.k; view.k = k; setView();
}, { passive: false });
// Mouse: drag background = pan. In edit mode: drag a box = move it, drag a group title = move the group.
// Click a box = select (Shift+click = pick several). While "Link to" is active, the next box clicked is the target.
let drag = null, dragged = false, linking = null;
const toWorld = (ev) => { const r = svg.getBoundingClientRect(); return { x: (ev.clientX - r.left - view.x) / view.k, y: (ev.clientY - r.top - view.y) / view.k }; };
svg.addEventListener("mousedown", (ev) => {
  dragged = false;
  const node = ev.target.closest(".node"), grp = ev.target.closest(".group");
  if (editing && node) {
    const ids = picked.has(node.dataset.id) ? [...picked] : [node.dataset.id];
    drag = { kind: "move", ids, from: toWorld(ev), start: ids.map((i) => [AT[i].x, AT[i].y]), before: JSON.stringify(L) };
  } else if (editing && grp) {
    const ids = L.groups[current][+grp.dataset.group].members.filter((m) => AT[m]);
    drag = { kind: "move", ids, from: toWorld(ev), start: ids.map((i) => [AT[i].x, AT[i].y]), before: JSON.stringify(L) };
  } else {
    drag = { kind: "pan", x: ev.clientX - view.x, y: ev.clientY - view.y, sx: ev.clientX, sy: ev.clientY };
    svg.classList.add("drag");
  }
});
window.addEventListener("mousemove", (ev) => {
  if (!drag) return;
  if (drag.kind === "pan") {
    if (Math.abs(ev.clientX - drag.sx) + Math.abs(ev.clientY - drag.sy) > 4) dragged = true;
    view.x = ev.clientX - drag.x; view.y = ev.clientY - drag.y; setView();
    return;
  }
  const p = toWorld(ev), dx = p.x - drag.from.x, dy = p.y - drag.from.y;
  if (Math.abs(dx) + Math.abs(dy) > 3) dragged = true;
  const pos = (L.positions[current] = L.positions[current] || {});
  drag.ids.forEach((id, i) => (pos[id] = [Math.round(drag.start[i][0] + dx), Math.round(drag.start[i][1] + dy)]));
  repaint();
});
window.addEventListener("mouseup", () => {
  if (drag && drag.kind === "move" && dragged) { undo.push(drag.before); post({ type: "layout", layout: L }); }
  drag = null; svg.classList.remove("drag");
});
svg.addEventListener("click", (ev) => {
  if (dragged) return;
  const node = ev.target.closest(".node");
  if (!node) { picked.clear(); return select(null); }
  const id = node.dataset.id;
  if (linking) return finishLink(id);
  if (ev.shiftKey) { picked.has(id) ? picked.delete(id) : picked.add(id); repaint(); return details(selected || id); }
  picked.clear(); select(id, true);
});
document.querySelectorAll("#zoom button").forEach((b) => b.addEventListener("click", () => {
  if (b.dataset.z === "fit") return fit();
  const r = svg.getBoundingClientRect(), k = view.k * (b.dataset.z === "in" ? 1.25 : 0.8);
  view.x = r.width / 2 - ((r.width / 2 - view.x) * k) / view.k; view.y = r.height / 2 - ((r.height / 2 - view.y) * k) / view.k; view.k = k; setView();
}));

// ---------- selection and details ----------
function viewFor(id) {
  if (current !== "coverage" && AT && AT[id]) return current;
  for (const v of ["calls", "overview", "files", "runtime", "docs"]) if (graphFor(v).nodes.some((n) => n.id === id)) return v;
  return null;
}

function highlight(id) {
  const near = new Set([id]);
  document.querySelectorAll(".edge").forEach((p) => {
    const on = p.dataset.from === id || p.dataset.to === id;
    p.classList.toggle("hi", on); p.classList.toggle("dim", !on);
    if (on) { near.add(p.dataset.from); near.add(p.dataset.to); }
  });
  document.querySelectorAll(".node").forEach((g) => { g.classList.toggle("dim", !near.has(g.dataset.id)); g.classList.toggle("sel", g.dataset.id === id); });
}

function select(id, open) {
  selected = id;
  document.querySelectorAll(".node").forEach((g) => g.classList.remove("sel", "dim"));
  document.querySelectorAll(".edge").forEach((p) => p.classList.remove("hi", "dim"));
  if (!id) { details(null); return; }
  const v = viewFor(id);
  if (v && v !== current) draw(v);
  if (AT && AT[id]) { highlight(id); if (open) focus(id); }
  details(id);
  if (open) openNode(id);
}

function where(n) { // file and line to open for a node
  if (!n) return null;
  if (n.file && n.line) return { file: n.file, line: n.line };
  if (n.kind === "file" && n.on_disk) return { file: n.name, line: 1 };
  return null;
}
function openNode(id) { const w = where(byId[id]); if (w) openFile(w.file, w.line); }

function editBar(id) {
  if (!editing) return "";
  const sel = picked.size ? [...picked] : id ? [id] : [];
  const n = sel.length;
  const groups = L.groups[current] || [];
  const inGroup = id && groups.some((g) => g.members.includes(id));
  return `<div class="editbar"><b>Edit</b> <span class="tag">${n ? n + " box(es) selected" : "select a box (Shift+click for more)"}</span><br>
    <button data-act="group" ${n ? "" : "disabled"}>Group</button>
    <button data-act="ungroup" ${inGroup ? "" : "disabled"}>Remove from group</button>
    <button data-act="label" ${id ? "" : "disabled"}>Rename label</button>
    <button data-act="note" ${id ? "" : "disabled"}>Note</button>
    <button data-act="link" ${id ? "" : "disabled"}>${linking ? "Click the target box..." : "Link to..."}</button>
    <button data-act="reset">Reset positions (this view)</button>
    <button data-act="undo" ${undo.length ? "" : "disabled"}>Undo</button></div>`;
}

function details(id) {
  const n = id && byId[id];
  if (!n) {
    $("#details").innerHTML = editBar(null) + (id ? '<p class="err">Unknown item: ' + esc(id) + "</p>"
      : '<p class="hint">Click a box to see where it is, what it uses, and which docs explain it.</p>');
    return;
  }
  const w = where(n);
  const ids = new Set([id]); F.nodes.forEach((m) => { if (m.parent === id) ids.add(m.id); });
  const docs = F.edges.filter((e) => e.kind === "documents" && ids.has(e.to));
  const outE = F.edges.filter((e) => e.from === id && e.kind !== "contains");
  const inE = F.edges.filter((e) => e.to === id && e.kind !== "contains" && e.kind !== "documents");
  const name = (x) => (byId[x] ? label(byId[x]) : x);
  const row = (e, other) => `<li><span class="lnk" data-sel="${esc(other)}">${esc(name(other))}</span> <span class="tag ${e.certainty}">${e.kind}${e.callback_of ? " (callback of " + esc(e.callback_of) + (e.kw ? " " + esc(e.kw) + "=" : "") + (e.delay !== undefined ? ", " + e.delay + " ms" : "") + ")" : ""}${e.name && e.name !== name(other) ? " " + esc(e.name) : ""}${e.args ? ": " + esc(e.args.join(" ")) : ""}${e.via ? " (via " + esc(e.via) + ")" : ""}</span> <span class="lnk tag" data-open="${esc(e.file)}" data-line="${e.line}">${esc(e.file)}:${e.line}</span></li>`;
  let h = editBar(id) + `<h3>${esc(label(n))}</h3><div class="kind">${L.labels[id] ? "your label for <b>" + esc(realLabel(n)) + "</b> &middot; " : ""}${esc(n.kind)}${w ? ` &middot; <span class="lnk" data-open="${esc(w.file)}" data-line="${w.line}">${esc(w.file)}, line ${w.line}${n.end_line && n.end_line !== w.line ? "-" + n.end_line : ""}</span>` : ""}${n.exact === false ? " &middot; path pattern (* = not known)" : ""}</div>`;
  if (n.doc) h += `<p>${esc(n.doc)}</p>`;
  if (L.notes[id]) h += `<div class="yours"><b>Your note</b><p>${esc(L.notes[id])}</p></div>`;
  const mine = L.links.filter((l) => l.from === id || l.to === id);
  if (mine.length) h += `<div class="yours"><b>Your links (not from the code)</b><ul>${mine.map((l) => `<li><span class="lnk" data-sel="${esc(l.from === id ? l.to : l.from)}">${esc(name(l.from === id ? l.to : l.from))}</span> ${l.from === id ? "&rarr;" : "&larr;"} ${esc(l.label || "")} ${editing ? `<button data-act="unlink" data-i="${L.links.indexOf(l)}">remove</button>` : ""}</li>`).join("")}</ul></div>`;
  const saved = RESEARCH.filter((r) => r.node === id || r.term === label(n) || r.term === realLabel(n));
  h += `<div class="ext"><button data-look="${esc(realLabel(n))}" ${B.settings.internet ? "" : 'disabled title="Turn Internet on (top right) to look things up"'}>Look up online</button>`
    + (saved.length ? `<b>Saved research (external, not from the code)</b><ul>${saved.map((r) => r.items.map((i) => `<li><a class="web" href="${esc(i.url)}">${esc(i.title)}</a> <span class="tag">${esc(r.saved)} &middot; ${esc(i.license || "")}</span></li>`).join("")).join("")}</ul>` : "")
    + "</div>";
  h += `<b>Explained in docs (${docs.length})</b><ul>${docs.map((e) => `<li><span class="lnk" data-open="${esc(e.file)}" data-line="${e.line}">${esc(e.file)}:${e.line}</span> <span class="tag">${esc(byId[e.from] ? byId[e.from].name.replace(/`/g, "") : "")}</span></li>`).join("") || "<li class='hint'>none found</li>"}</ul>`;
  if (outE.length) h += `<b>Uses (${outE.length})</b><ul>${outE.map((e) => row(e, e.to)).join("")}</ul>`;
  if (inE.length) h += `<b>Used by (${inE.length})</b><ul>${inE.map((e) => row(e, e.from)).join("")}</ul>`;
  $("#details").innerHTML = h;
}

// ---------- edit actions ----------
function ask(title, value, multiline) { // small in-page dialog; resolves to the text, or null on Cancel
  return new Promise((done) => {
    const m = $("#modal");
    m.innerHTML = `<div class="box"><b>${esc(title)}</b>${multiline ? `<textarea rows="6">${esc(value || "")}</textarea>` : `<input value="${esc(value || "")}">`}
      <div class="row"><button data-a="cancel">Cancel</button><button data-a="ok" class="primary">OK</button></div></div>`;
    m.hidden = false;
    const field = m.querySelector("input,textarea"); field.focus(); field.select();
    const close = (v) => { m.hidden = true; m.innerHTML = ""; done(v); };
    m.onclick = (ev) => { if (ev.target.dataset.a) close(ev.target.dataset.a === "ok" ? field.value : null); };
    field.onkeydown = (ev) => { if (ev.key === "Escape") close(null); if (ev.key === "Enter" && !multiline) close(field.value); };
  });
}

function finishLink(target) {
  const from = linking; linking = null;
  if (from === target) return details(from);
  ask("Label for your link (optional)", "").then((text) => {
    if (text === null) return details(from);
    edit(() => L.links.push({ from, to: target, label: text.trim() }));
    details(from);
  });
}

$("#details").addEventListener("click", async (ev) => {
  const act = ev.target.dataset.act; if (!act) return;
  ev.stopPropagation();
  const id = selected, sel = picked.size ? [...picked] : id ? [id] : [];
  if (act === "group") {
    const name = await ask("Group name", ""); if (!name) return;
    edit(() => {
      const gs = (L.groups[current] = L.groups[current] || []);
      gs.forEach((g) => (g.members = g.members.filter((m) => !sel.includes(m))));
      gs.push({ name: name.trim(), members: sel });
      L.groups[current] = gs.filter((g) => g.members.length);
    });
  } else if (act === "ungroup") {
    edit(() => { L.groups[current] = (L.groups[current] || []).map((g) => ({ ...g, members: g.members.filter((m) => m !== id) })).filter((g) => g.members.length); });
  } else if (act === "label") {
    const t = await ask("Your label for " + realLabel(byId[id]) + " (empty = real name)", L.labels[id]); if (t === null) return;
    edit(() => { if (t.trim()) L.labels[id] = t.trim(); else delete L.labels[id]; });
  } else if (act === "note") {
    const t = await ask("Your note for " + realLabel(byId[id]) + " (empty = remove)", L.notes[id], true); if (t === null) return;
    edit(() => { if (t.trim()) L.notes[id] = t.trim(); else delete L.notes[id]; });
  } else if (act === "link") {
    linking = linking ? null : id;
  } else if (act === "unlink") {
    edit(() => L.links.splice(+ev.target.dataset.i, 1));
  } else if (act === "reset") {
    edit(() => delete L.positions[current]); draw(current);
  } else if (act === "undo" && undo.length) {
    const old = JSON.parse(undo.pop());
    Object.keys(L).forEach((k) => delete L[k]); Object.assign(L, old);
    post({ type: "layout", layout: L }); draw(current);
  }
  details(selected);
});
document.addEventListener("keydown", (ev) => {
  if (editing && (ev.ctrlKey || ev.metaKey) && ev.key === "z" && !ev.target.closest("input,textarea")) {
    ev.preventDefault(); const b = document.querySelector('[data-act="undo"]'); if (b && !b.disabled) b.click();
  }
});

document.addEventListener("click", (ev) => {
  const t = ev.target.closest("[data-sel],[data-open],[data-node],.doclink");
  if (!t) return;
  if (t.dataset.open) return openFile(t.dataset.open, +t.dataset.line || 1);
  if (t.dataset.sel || t.dataset.node) return select(t.dataset.sel || t.dataset.node, true);
  if (t.classList.contains("doclink")) {
    const [path, anchor] = t.dataset.target.split("#");
    const base = t.dataset.from.split("/").slice(0, -1);
    const parts = path ? base.concat(path.split("/")) : t.dataset.from.split("/");
    const rel = []; parts.forEach((p) => (p === ".." ? rel.pop() : p && p !== "." && rel.push(p)));
    const file = rel.join("/");
    const head = anchor && byId["doc:" + file + "#" + anchor];
    if (byId["doc:" + file] || byId["file:" + file]) openFile(file, head ? head.line : 1);
    else select(byId["file:" + file] ? "file:" + file : null);
  }
});

// ---------- tabs ----------
const tabs = []; let active = null;
function openFile(file, line) {
  let t = tabs.find((x) => x.file === file);
  if (!t) {
    t = { file, line, page: document.createElement("div") };
    t.page.className = "page"; t.page.innerHTML = '<p class="hint">Loading...</p>';
    $("#pages").querySelectorAll(":scope > .hint").forEach((h) => h.remove());
    $("#pages").appendChild(t.page); tabs.push(t);
    post({ type: "open", file, line });
  }
  t.line = line; show(t); jump(t);
}
window.DM = {
  fileLoaded(file, html, line) { const t = tabs.find((x) => x.file === file); if (!t) return; t.page.innerHTML = html; t.loaded = true; jump(t); },
  toast(msg) { const el = $("#toast"); el.textContent = msg; el.classList.add("on"); clearTimeout(el._t); el._t = setTimeout(() => el.classList.remove("on"), 4000); },
};
function show(t) {
  active = t;
  tabs.forEach((x) => x.page.classList.toggle("on", x === t));
  $("#tabs").innerHTML = tabs.map((x, i) => `<div class="tab${x === t ? " on" : ""}" data-i="${i}">${esc(x.file.split("/").pop())}<b data-close="${i}" title="Close">&times;</b></div>`).join("");
  $("#tabpath").textContent = t.file; $("#external").disabled = false;
  saveState();
}
function jump(t) {
  if (!t.loaded) return;
  t.page.querySelectorAll(".hit").forEach((h) => h.classList.remove("hit"));
  const el = t.page.querySelector("#L" + t.line);
  if (el) { el.classList.add("hit"); el.scrollIntoView({ block: "center" }); }
}
$("#tabs").addEventListener("click", (ev) => {
  const c = ev.target.dataset.close;
  if (c !== undefined) {
    ev.stopPropagation();
    const [t] = tabs.splice(+c, 1); t.page.remove();
    if (tabs.length) show(tabs[Math.min(+c, tabs.length - 1)]);
    else { active = null; $("#tabs").innerHTML = ""; $("#tabpath").textContent = ""; $("#external").disabled = true; saveState(); }
    return;
  }
  const tab = ev.target.closest(".tab"); if (tab) show(tabs[+tab.dataset.i]);
});
$("#external").addEventListener("click", () => active && post({ type: "external", file: active.file }));

// ---------- coverage view ----------
function drawCoverage() {
  const p = F.problems, at = (f, l) => `<span class="lnk" data-open="${esc(f)}" data-line="${l}">${esc(f)}:${l}</span>`;
  const groups = {};
  F.unknown.forEach((u) => (groups[u.reason] = groups[u.reason] || []).push(u));
  $("#coverage").innerHTML =
    `<h2>Self-check: <span class="${p.length ? "bad" : "ok"}">${p.length ? "FAILED, " + p.length + " problem(s)" : "PASSED"}</span></h2>`
    + (p.length ? "<ul>" + p.map((x) => `<li class="bad">${esc(x)}</li>`).join("") + "</ul>" : "<p>Every box points to a real line, every doc link exists, and the two counting methods agree.</p>")
    + `<h2>Hand edits that no longer match the code: <span class="${STALE.length ? "bad" : "ok"}">${STALE.length}</span></h2>`
    + (STALE.length ? `<p class="hint">Your edits point at something that was renamed or removed. They are not drawn. Remove them, or rename the code back.</p><ul>${STALE.map((x, i) => `<li class="bad">${esc(x.what)} <code>${esc(x.id)}</code>${x.view ? " in view " + esc(x.view) : ""}${x.group ? ", group " + esc(x.group) : ""} <button data-stale="${i}">Remove</button></li>`).join("")}</ul>` : "<p>None.</p>")
    + `<h2>Not understood: ${F.unknown.length} (never drawn)</h2><p class="hint">The scanner could not prove these. Check them in the code.</p>`
    + Object.keys(groups).sort((a, b) => groups[b].length - groups[a].length).map((r) => `<details><summary>${esc(r)} (${groups[r].length})</summary><ul>${groups[r].map((u) => `<li>${at(u.file, u.line)} <code>${esc(u.code)}</code></li>`).join("")}</ul></details>`).join("")
    + `<h2>Doc terms with more than one meaning: ${F.ambiguous.length} (not linked)</h2><ul>${F.ambiguous.map((a) => `<li>${at(a.file, a.line)} <code>${esc(a.text)}</code>: ${a.candidates.map(esc).join(", ")}</li>`).join("")}</ul>`
    + `<h2>Doc terms that match nothing in the code: ${F.unlinked.length}</h2><details><summary>show</summary><ul>${F.unlinked.map((u) => `<li>${at(u.file, u.line)} <code>${esc(u.text)}</code></li>`).join("")}</ul></details>`;
}

$("#coverage").addEventListener("click", (ev) => {
  const i = ev.target.dataset.stale; if (i === undefined) return;
  const x = STALE[+i];
  edit(() => {
    if (x.what === "position") delete (L.positions[x.view] || {})[x.id];
    else if (x.what === "group member") (L.groups[x.view] || []).forEach((g) => (g.members = g.members.filter((m) => m !== x.id)));
    else if (x.what === "label") delete L.labels[x.id];
    else if (x.what === "note") delete L.notes[x.id];
    else if (x.what === "manual link") L.links = L.links.filter((l) => l.from !== x.id && l.to !== x.id);
    Object.keys(L.groups).forEach((v) => (L.groups[v] = L.groups[v].filter((g) => g.members.length)));
  });
});

// ---------- online lookup (only with Internet on) ----------
let RESEARCH = B.research || [], lastLookup = null;
document.addEventListener("click", (ev) => {
  const q = ev.target.dataset && ev.target.dataset.look;
  if (q === undefined) return;
  ev.stopPropagation();
  lastLookup = { term: q, node: selected };
  $("#modal").hidden = false;
  $("#modal").innerHTML = `<div class="box wide"><b>Looking up "${esc(q)}" online...</b><div class="row"><button data-a="close">Close</button></div></div>`;
  $("#modal").onclick = (e) => { if (e.target.dataset.a === "close") $("#modal").hidden = true; };
  post({ type: "lookup", query: q });
}, true);
window.DM.lookupResult = (r) => {
  const m = $("#modal");
  if (m.hidden) return;
  if (r.error) { m.innerHTML = `<div class="box"><b>Lookup failed</b><p>${esc(r.error)}</p><div class="row"><button data-a="close">Close</button></div></div>`; return; }
  const names = { wikipedia: "Wikipedia", stackoverflow: "Stack Overflow", github: "GitHub", mslearn: "Microsoft Learn", official: "Official docs" };
  let i = 0; const flat = [];
  m.innerHTML = `<div class="box wide"><b>Results for "${esc(r.query)}"</b> <span class="tag">${esc(r.time)}. External: these are not from your code. Tick what to keep.</span><div class="results">`
    + Object.keys(r.results).map((src) => `<h4>${names[src] || src}</h4>` + (r.results[src].map((it) => { flat.push(Object.assign({ source: names[src] || src }, it)); return `<label><input type="checkbox" data-i="${i++}"> <a class="web" href="${esc(it.url)}">${esc(it.title)}</a><br><span class="tag">${esc(it.summary || "")}</span></label>`; }).join("") || '<p class="hint">nothing found</p>')).join("")
    + Object.keys(r.errors || {}).map((src) => `<p class="err">${names[src] || src}: ${esc(r.errors[src])}</p>`).join("")
    + `</div><div class="row"><button data-a="close">Close</button><button data-a="save" class="primary">Save selected</button></div></div>`;
  m.onclick = (e) => {
    if (e.target.dataset.a === "close") m.hidden = true;
    if (e.target.dataset.a === "save") {
      const items = [...m.querySelectorAll("input:checked")].map((c) => flat[+c.dataset.i]);
      post({ type: "saveResearch", term: lastLookup.term, node: lastLookup.node, items }); m.hidden = true;
    }
  };
};
window.DM.setResearch = (list) => { RESEARCH = list; details(selected); DM.toast("Saved to docs/viewer/research/"); };

// ---------- search ----------
const search = $("#search"), results = $("#results");
search.addEventListener("input", () => {
  const q = search.value.trim().toLowerCase().replace(/\(\)$/, "");
  if (!q) { results.style.display = "none"; return; }
  const hits = F.nodes.filter((n) => label(n).toLowerCase().includes(q) || n.name.toLowerCase().includes(q)).slice(0, 40);
  results.innerHTML = hits.map((n) => `<div data-sel="${esc(n.id)}">${esc(label(n))}<small>${esc(n.kind)}${n.file ? " &middot; " + esc(n.file) + ":" + n.line : ""}</small></div>`).join("") || '<div class="hint">nothing found</div>';
  results.style.display = "block";
});
search.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { const f = results.querySelector("[data-sel]"); if (f) f.click(); } if (ev.key === "Escape") results.style.display = "none"; });
results.addEventListener("click", () => { results.style.display = "none"; });

// ---------- bar, splitter, state ----------
$("#project").textContent = B.project;
$("#views").innerHTML = Object.keys(VIEWS).map((v) => `<button data-v="${v}">${VIEWS[v].label}</button>`).join("");
$("#views").addEventListener("click", (ev) => { if (ev.target.dataset.v) { draw(ev.target.dataset.v); saveState(); } });
const badge = $("#check");
function setBadge() {
  const bad = F.problems.length, st = STALE.length;
  badge.textContent = bad ? "Self-check FAILED (" + bad + ")" : st ? st + " stale edit(s)" : "Self-check passed";
  badge.className = "badge " + (bad || st ? "bad" : "ok");
  badge.title = st ? "Some of your edits point at things no longer in the code. See Coverage." : "";
}
setBadge();
window.DM.setStale = (list) => { STALE = list; setBadge(); if (current === "coverage") drawCoverage(); };
$("#edit").addEventListener("click", () => {
  editing = !editing; linking = null;
  $("#edit").classList.toggle("on", editing); document.body.classList.toggle("editing", editing);
  details(selected); saveState();
});
badge.addEventListener("click", () => draw("coverage"));
$("#rescan").addEventListener("click", () => { saveState(); post({ type: "rescan" }); });
const inet = $("#internet");
inet.checked = !!B.settings.internet;
inet.addEventListener("change", () => { saveState(); post({ type: "internet", on: inet.checked }); });
let splitting = false;
$("#split").addEventListener("mousedown", (ev) => { splitting = true; ev.preventDefault(); });
window.addEventListener("mousemove", (ev) => { if (splitting) $("#left").style.flexBasis = Math.max(300, Math.min(window.innerWidth - 300, ev.clientX)) + "px"; });
window.addEventListener("mouseup", () => (splitting = false));

function saveState() { // window.name survives a reload of this page (used by Rescan and the Internet switch)
  window.name = JSON.stringify({ view: current, sel: selected, editing, tabs: tabs.map((t) => [t.file, t.line]), active: active ? active.file : null });
}
let saved = {}; try { saved = JSON.parse(window.name || "{}"); } catch (e) { saved = {}; }
if (saved.editing) $("#edit").click();
draw(VIEWS[saved.view] ? saved.view : "overview");
(saved.tabs || []).forEach(([f, l]) => openFile(f, l));
if (saved.active) { const t = tabs.find((x) => x.file === saved.active); if (t) show(t); }
if (saved.sel && byId[saved.sel]) select(saved.sel, false);
window.addEventListener("resize", () => current !== "coverage" && fit());
