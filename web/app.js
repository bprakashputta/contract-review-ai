// contract-review-ai UI: vanilla JS, no build step. All text goes through textContent.
const $ = (s) => document.querySelector(s);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};
const state = { id: null, doc: null, review: null, active: null };
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");
let booting = true; // deep links jump straight to the target
const scrollBehavior = () => (booting || reduceMotion.matches ? "auto" : "smooth");
const stripExt = (name) => name.replace(/\.(txt|pdf|docx)$/i, "");
const SVG_NS = "http://www.w3.org/2000/svg";

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
  return body;
}

// small inline icons (decorative: every icon sits next to text that says the same thing)
const ICONS = {
  high: "M10 3 18 17H2z|M10 8.5v3.5|M10 14.2v.3",
  medium: "M10 2.8 17.2 10 10 17.2 2.8 10z|M10 7v4|M10 13.2v.3",
  low: "M10 3.5a6.5 6.5 0 1 1 0 13 6.5 6.5 0 0 1 0-13z|M7 10h6",
  info: "M10 3.5a6.5 6.5 0 1 1 0 13 6.5 6.5 0 0 1 0-13z|M10 9v4.2|M10 6.6v.3",
  doc: "M5.5 2.5h6l3 3v12h-9z|M11.5 2.5v3h3",
  check: "M4.5 10.5 8 14l7.5-8",
  dash: "M5.5 10h9",
};
function icon(name, cls = "ico") {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 20 20");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("class", cls);
  for (const d of ICONS[name].split("|")) {
    const p = document.createElementNS(SVG_NS, "path");
    p.setAttribute("d", d);
    svg.append(p);
  }
  return svg;
}
const SEV_LABEL = { high: "High", medium: "Medium", low: "Low", info: "Info" };
function sevChip(sev) {
  const c = el("span", `sev sev-${sev}`);
  c.append(icon(sev in ICONS ? sev : "info"), el("span", null, SEV_LABEL[sev] || sev));
  return c;
}

// ---------- theme ----------
(function theme() {
  const key = "crai-theme";
  let saved = null;
  try { saved = localStorage.getItem(key); } catch (_) {}
  if (saved) document.documentElement.dataset.theme = saved;
  $("#theme").addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;
    const next = dark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem(key, next); } catch (_) {}
  });
})();

// ---------- tabs ----------
const TABS = ["clauses", "risks", "ask"];
function selectTab(name, focus = false) {
  for (const t of TABS) {
    const tab = $(`#tab-${t}`);
    const on = t === name;
    tab.setAttribute("aria-selected", String(on));
    tab.tabIndex = on ? 0 : -1;
    $(`#panel-${t}`).hidden = !on;
    if (on && focus) tab.focus();
  }
}
for (const t of TABS) {
  const tab = $(`#tab-${t}`);
  tab.addEventListener("click", () => selectTab(t));
  tab.addEventListener("keydown", (e) => {
    const i = TABS.indexOf(t);
    let j = null;
    if (e.key === "ArrowRight") j = (i + 1) % TABS.length;
    else if (e.key === "ArrowLeft") j = (i + TABS.length - 1) % TABS.length;
    else if (e.key === "Home") j = 0;
    else if (e.key === "End") j = TABS.length - 1;
    if (j != null) { e.preventDefault(); selectTab(TABS[j], true); }
  });
}

async function loadMeta() {
  try {
    const m = await api("/api/meta");
    $("#playbook").textContent = `${m.playbook.name} · v${m.playbook.version}`;
    const meta = $("#meta");
    meta.replaceChildren(
      el("span", "meta-item", m.model),
      el("span", "meta-item", null),
    );
    const spend = meta.lastChild;
    spend.append("Spend ", el("span", "num", `$${m.spend.total_usd.toFixed(2)} / $${m.spend.cap_usd.toFixed(2)}`));
  } catch (e) {
    $("#meta").textContent = "API unavailable";
  }
}

async function loadDocs() {
  const docs = await api("/api/documents");
  const ol = $("#docs");
  ol.replaceChildren();
  $("#doc-count").textContent = docs.length ? String(docs.length) : "";
  for (const d of docs) {
    const li = el("li");
    const b = el("button", "doc-item");
    b.type = "button";
    if (d.id === state.id) b.setAttribute("aria-current", "true");
    const txt = el("span", "doc-text");
    txt.append(
      el("span", "doc-name", stripExt(d.filename)),
      el("span", "when", new Date(d.created_at * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })),
    );
    b.append(icon("doc", "ico doc-ico"), txt);
    b.addEventListener("click", () => openDoc(d.id));
    li.append(b);
    ol.append(li);
  }
}

// ---------- upload ----------
async function upload(file) {
  const st = $("#upload-status");
  st.className = "status";
  st.textContent = `Reading ${file.name}…`;
  const fd = new FormData();
  fd.append("file", file);
  try {
    const r = await api("/api/documents", { method: "POST", body: fd });
    st.textContent = `${r.sections} sections · ${r.chars.toLocaleString()} characters`;
    await openDoc(r.id);
    await loadDocs();
  } catch (e) {
    st.className = "status err";
    st.textContent = e.message;
  }
}
$("#file").addEventListener("change", (e) => e.target.files[0] && upload(e.target.files[0]));
const drop = $("#drop");
["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => e.dataTransfer.files[0] && upload(e.dataTransfer.files[0]));

// ---------- document ----------
async function openDoc(id) {
  const r = await api(`/api/documents/${id}`);
  state.id = id;
  state.doc = r.document;
  state.review = r.review;
  state.active = null;
  history.replaceState(null, "", `#doc=${id}`);
  $("#empty").hidden = true;
  $("#paper").hidden = false;
  $("#findings").hidden = false;
  $("#run").hidden = false;
  document.body.classList.add("has-doc");
  $("#doc-title").textContent = stripExt(r.document.filename);
  renderAll();
  const ans = $("#answers");
  ans.replaceChildren();
  for (const a of r.answers.slice().reverse()) ans.append(renderAnswer(a));
  $("#ask-count").textContent = r.answers.length ? String(r.answers.length) : "";
  $("#canvas").scrollTop = 0;
  loadDocs();
}

function renderAll() {
  renderStats();
  renderPaper();
  renderReview();
}

function renderStats() {
  const d = state.doc, r = state.review;
  const s = $("#doc-stats");
  s.replaceChildren();
  const add = (k, v) => { const sp = el("span", "stat"); sp.append(`${k} `, el("span", "num", v)); s.append(sp); };
  add("Characters", d.text.length.toLocaleString());
  add("Sections", String(d.sections.length));
  if (r) {
    add("Mode", r.mode);
    add("Sent", `${r.sections_sent}/${r.sections_total}`);
    const c = r.calls.reduce((a, x) => a + x.cost_usd, 0);
    const tok = r.calls.reduce((a, x) => a + x.input_tokens, 0);
    add("Input tokens", tok.toLocaleString());
    add("Cost", `$${c.toFixed(4)}`);
    add("Latency", `${(r.calls.reduce((a, x) => a + x.latency_ms, 0) / 1000).toFixed(1)} s`);
  }
  $("#doc-kicker").textContent = r ? `Reviewed with prompt ${r.prompt_version}` : "Not reviewed yet";
  $("#run").textContent = r ? "Run again" : "Run review";
}

function quoteSpans() {
  const spans = [];
  if (!state.review) return spans;
  for (const c of state.review.clauses)
    for (const q of c.quotes) spans.push({ start: q.start, end: q.end, cat: c.category, label: c.label });
  spans.sort((a, b) => a.start - b.start || b.end - a.end);
  const out = [];
  for (const s of spans) {
    const last = out[out.length - 1];
    if (last && s.start < last.end) {
      // overlapping quotes from two categories: extend the mark, keep both labels
      last.end = Math.max(last.end, s.end);
      if (!last.cats.includes(s.cat)) { last.cats.push(s.cat); last.labels.push(s.label); }
    } else out.push({ start: s.start, end: s.end, cats: [s.cat], labels: [s.label] });
  }
  return out;
}

function renderPaper() {
  const p = $("#paper");
  p.replaceChildren();
  const text = state.doc.text;
  let pos = 0;
  for (const s of quoteSpans()) {
    if (s.start > pos) p.append(document.createTextNode(text.slice(pos, s.start)));
    const m = el("mark", null, text.slice(s.start, s.end));
    m.dataset.cats = s.cats.join(" ");
    m.dataset.label = s.labels.join(" · ");
    m.dataset.start = s.start;
    p.append(m);
    pos = s.end;
  }
  p.append(document.createTextNode(text.slice(pos)));
}

function scrollCanvasTo(rect, offset) {
  const canvas = $("#canvas");
  const top = rect.top - canvas.getBoundingClientRect().top - offset;
  canvas.scrollBy({ top, behavior: scrollBehavior() });
  // stacked (narrow) layout: also bring the canvas itself into view
  if (getComputedStyle($(".workbench")).display !== "grid")
    canvas.scrollIntoView({ behavior: scrollBehavior(), block: "start" });
}

function focusQuote(start, cat) {
  const marks = [...document.querySelectorAll("#paper mark")];
  marks.forEach((m) => m.classList.remove("active"));
  const target = marks.find((m) => +m.dataset.start <= start && start < +m.dataset.start + m.textContent.length)
    || marks.find((m) => m.dataset.cats.split(" ").includes(cat));
  if (target) {
    target.classList.add("active");
    scrollCanvasTo(target.getBoundingClientRect(), 120);
  }
}

function focusSpan(start, end) {
  // a citation span may not be a highlighted clause: scroll to its character offset
  const paper = $("#paper");
  const walker = document.createTreeWalker(paper, NodeFilter.SHOW_TEXT);
  let pos = 0, node;
  while ((node = walker.nextNode())) {
    const len = node.textContent.length;
    if (pos + len > start) {
      const range = document.createRange();
      range.setStart(node, Math.max(0, start - pos));
      range.setEnd(node, Math.min(len, Math.max(0, start - pos) + Math.min(end - start, len)));
      scrollCanvasTo(range.getBoundingClientRect(), 80);
      const sel = getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      return;
    }
    pos += len;
  }
}

function quoteBlock(text, max, onClick, meta) {
  const b = el("button", "quote");
  b.type = "button";
  b.append(el("span", "quote-text", text.length > max ? text.slice(0, max) + "…" : text));
  if (meta) b.append(meta);
  b.addEventListener("click", onClick);
  return b;
}

function renderReview() {
  const r = state.review;
  $("#clauses-empty").hidden = !!r;
  $("#risks-empty").hidden = !!r;
  const rl = $("#risks"), tb = $("#clauses"), sum = $("#sev-summary");
  rl.replaceChildren(); tb.replaceChildren(); sum.replaceChildren();
  $("#verify-note").textContent = "";
  $("#risk-count").textContent = "";
  $("#clause-count").textContent = "";
  if (!r) return;

  // risks, most severe first (the API already orders them)
  const counts = {};
  for (const f of r.risks) counts[f.severity] = (counts[f.severity] || 0) + 1;
  for (const sev of ["high", "medium", "low", "info"]) {
    if (!counts[sev]) continue;
    const s = el("span", "sev-count");
    s.append(sevChip(sev), el("span", "num", String(counts[sev])));
    sum.append(s);
  }
  for (const f of r.risks) {
    const li = el("li", `risk risk-${f.severity}`);
    const head = el("div", "risk-head");
    head.append(sevChip(f.severity), el("code", "rule-id", f.rule_id));
    li.append(head, el("p", "risk-msg", f.message));
    const ev = f.evidence[0];
    if (ev) li.append(quoteBlock(ev.text, 200, () => focusQuote(ev.start, f.category)));
    rl.append(li);
  }
  $("#risk-count").textContent = String(r.risks.length);

  // clauses
  let found = 0, dropped = 0, repaired = 0;
  for (const c of r.clauses) {
    const li = el("li", `clause ${c.found ? "hit" : "miss"}`);
    li.dataset.cat = c.category;
    const head = el("div", "clause-head");
    const st = el("span", `found ${c.found ? "yes" : "no"}`);
    st.append(icon(c.found ? "check" : "dash"), el("span", null, c.found ? `${c.quotes.length} ${c.quotes.length === 1 ? "quote" : "quotes"}` : "Not found"));
    head.append(el("span", "clause-label", c.label), st);
    li.append(head);
    if (c.found) {
      found++;
      const q = c.quotes[0];
      const meta = el("span", "q-meta");
      if (q.section_id) meta.append(el("span", "sec", q.section_id));
      if (c.note) meta.append(el("span", "note", c.note));
      if (q.status === "repaired") meta.append(el("span", "tag", "repaired"));
      li.append(quoteBlock(q.text, 260, () => {
        document.querySelectorAll(".clause").forEach((x) => x.classList.remove("active"));
        li.classList.add("active");
        li.scrollIntoView({ behavior: scrollBehavior(), block: "nearest" });
        focusQuote(q.start, c.category);
      }, meta));
    }
    repaired += c.quotes.filter((q) => q.status === "repaired").length;
    dropped += c.dropped_quotes.length;
    tb.append(li);
  }
  $("#clause-count").textContent = `${found}/${r.clauses.length}`;
  const n = r.clauses.reduce((a, c) => a + c.quotes.length, 0);
  $("#verify-note").textContent =
    `${n} quotes verified against the text${repaired ? `, ${repaired} repaired to the document's exact wording` : ""}; ` +
    `${dropped} dropped because they could not be found.`;
}

$("#run").addEventListener("click", async () => {
  const b = $("#run");
  b.disabled = true;
  b.textContent = "Reviewing";
  b.classList.add("busy");
  try {
    state.review = await api(`/api/documents/${state.id}/review`, { method: "POST" });
    renderAll();
    loadMeta();
  } catch (e) {
    $("#doc-kicker").textContent = `Review failed: ${e.message}`;
  } finally {
    b.disabled = false;
    b.classList.remove("busy");
    renderStats();
  }
});

// ---------- Q&A ----------
function renderAnswer(a) {
  const li = el("li", "answer");
  li.append(el("p", "qline", a.question));
  li.append(el("p", "a-text", a.answer));
  if (a.answerable && !a.supported) {
    const u = el("p", "unsup");
    u.append(icon("high"), el("span", null, "No verified citation. Treat as unsupported."));
    li.append(u);
  }
  for (const c of a.citations) {
    const meta = el("span", "q-meta");
    if (c.section_id) meta.append(el("span", "sec", c.section_id));
    li.append(quoteBlock(c.text, 240, () => focusSpan(c.start, c.end), meta));
  }
  return li;
}
$("#ask").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("#question").value.trim();
  if (!q || !state.id) return;
  const btn = e.target.querySelector("button");
  btn.disabled = true;
  try {
    const a = await api(`/api/documents/${state.id}/ask`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ question: q }),
    });
    $("#answers").prepend(renderAnswer(a));
    $("#ask-count").textContent = String($("#answers").children.length);
    $("#question").value = "";
    loadMeta();
  } catch (err) {
    const li = el("li", "answer");
    li.append(el("p", "unsup", err.message));
    $("#answers").prepend(li);
  } finally {
    btn.disabled = false;
  }
});

// ---------- boot ----------
loadMeta();
const bootHash = location.hash;
loadDocs().then(() => {
  // deep links: #doc=<id>&focus=<category key>&tab=<clauses|risks|ask>
  const m = bootHash.match(/doc=([a-f0-9]+)/);
  const f = bootHash.match(/focus=([a-z_]+)/);
  const t = bootHash.match(/tab=(clauses|risks|ask)/);
  if (m)
    openDoc(m[1])
      .then(() => {
        if (f) document.querySelector(`.clause[data-cat="${f[1]}"] .quote`)?.click();
        if (t) selectTab(t[1]);
        if (f || t) history.replaceState(null, "", bootHash);
      })
      .catch(() => {})
      .finally(() => { booting = false; });
  else booting = false;
});
