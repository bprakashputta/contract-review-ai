// contract-review-ai UI: vanilla JS, no build step. All text goes through textContent.
const $ = (s) => document.querySelector(s);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};
const state = { id: null, doc: null, review: null, active: null };

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
  return body;
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

async function loadMeta() {
  try {
    const m = await api("/api/meta");
    $("#meta").textContent =
      `${m.model} · playbook: ${m.playbook.name} · spend $${m.spend.total_usd.toFixed(2)} / $${m.spend.cap_usd.toFixed(2)}`;
  } catch (e) {
    $("#meta").textContent = "API unavailable";
  }
}

async function loadDocs() {
  const docs = await api("/api/documents");
  const ol = $("#docs");
  ol.replaceChildren();
  for (const d of docs) {
    const li = el("li");
    const b = el("button", null, d.filename.replace(/\.(txt|pdf|docx)$/i, ""));
    b.type = "button";
    if (d.id === state.id) b.setAttribute("aria-current", "true");
    b.append(el("span", "when", new Date(d.created_at * 1000).toLocaleString()));
    b.addEventListener("click", () => openDoc(d.id));
    li.append(b);
    ol.append(li);
  }
}

// ---------- upload ----------
async function upload(file) {
  const st = $("#upload-status");
  st.className = "status small";
  st.textContent = `Reading ${file.name}…`;
  const fd = new FormData();
  fd.append("file", file);
  try {
    const r = await api("/api/documents", { method: "POST", body: fd });
    st.textContent = `${r.sections} sections · ${r.chars.toLocaleString()} chars`;
    await openDoc(r.id);
    await loadDocs();
  } catch (e) {
    st.className = "status small err";
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
  $("#doc-view").hidden = false;
  $("#doc-title").textContent = r.document.filename.replace(/\.(txt|pdf|docx)$/i, "");
  renderAll();
  const ans = $("#answers");
  ans.replaceChildren();
  for (const a of r.answers.slice().reverse()) ans.append(renderAnswer(a));
  document.querySelectorAll(".docs button").forEach((b) => b.removeAttribute("aria-current"));
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
  const add = (k, v) => { const sp = el("span"); sp.append(`${k} `, el("b", null, v)); s.append(sp); };
  add("chars", d.text.length.toLocaleString());
  add("sections", String(d.sections.length));
  if (r) {
    add("mode", r.mode);
    add("sent", `${r.sections_sent}/${r.sections_total} sections`);
    const c = r.calls.reduce((a, x) => a + x.cost_usd, 0);
    const tok = r.calls.reduce((a, x) => a + x.input_tokens, 0);
    add("tokens", tok.toLocaleString());
    add("cost", `$${c.toFixed(4)}`);
    add("latency", `${(r.calls.reduce((a, x) => a + x.latency_ms, 0) / 1000).toFixed(1)}s`);
  }
  $("#doc-kicker").textContent = r ? `Reviewed · prompt ${r.prompt_version}` : "Not reviewed yet";
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

function focusQuote(start, cat) {
  const marks = [...document.querySelectorAll("#paper mark")];
  marks.forEach((m) => m.classList.remove("active"));
  const target = marks.find((m) => +m.dataset.start <= start && start < +m.dataset.start + m.textContent.length)
    || marks.find((m) => m.dataset.cats.split(" ").includes(cat));
  if (target) {
    target.classList.add("active");
    const paper = $("#paper");
    paper.scrollTop += target.getBoundingClientRect().top - paper.getBoundingClientRect().top - 140;
    if (getComputedStyle($(".right")).position !== "sticky") target.scrollIntoView({ behavior: "smooth", block: "center" });
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
      const rect = range.getBoundingClientRect();
      paper.scrollBy({ top: rect.top - paper.getBoundingClientRect().top - 60, behavior: "smooth" });
      const sel = getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      return;
    }
    pos += len;
  }
}

function renderReview() {
  const r = state.review;
  $("#risks-block").hidden = !r;
  $("#clauses-block").hidden = !r;
  if (!r) return;
  // risks
  const rl = $("#risks");
  rl.replaceChildren();
  for (const f of r.risks) {
    const li = el("li");
    li.append(el("span", `sev ${f.severity}`, f.severity));
    const body = el("div");
    body.append(el("div", "risk-msg", f.message), el("div", "rule-id", f.rule_id));
    const ev = f.evidence[0];
    if (ev) {
      const a = el("span", "risk-ev", `“${ev.text.length > 180 ? ev.text.slice(0, 180) + "…" : ev.text}”`);
      a.addEventListener("click", () => focusQuote(ev.start, f.category));
      body.append(a);
    }
    li.append(body);
    rl.append(li);
  }
  const hi = r.risks.filter((x) => x.severity === "high").length;
  $("#risk-count").textContent = `${r.risks.length} flags · ${hi} high`;
  // clauses
  const tb = $("#clauses");
  tb.replaceChildren();
  let found = 0, dropped = 0, repaired = 0;
  for (const c of r.clauses) {
    const tr = el("tr", c.found ? "hit" : "");
    tr.dataset.cat = c.category;
    tr.append(el("td", null, c.label));
    tr.append(el("td", c.found ? "yes" : "no", c.found ? `yes·${c.quotes.length}` : "no"));
    const td = el("td");
    if (c.found) {
      found++;
      const q = c.quotes[0];
      td.append(el("span", "q", q.text));
      const meta = el("span", "q-meta", `${q.section_id || ""}${c.note ? " · " + c.note : ""}`);
      if (q.status === "repaired") meta.append(el("span", "tag", "repaired"));
      td.append(meta);
      tr.addEventListener("click", () => {
        document.querySelectorAll(".clauses tr").forEach((x) => x.classList.remove("active"));
        tr.classList.add("active");
        focusQuote(q.start, c.category);
      });
    } else td.append(el("span", "no", "—"));
    repaired += c.quotes.filter((q) => q.status === "repaired").length;
    dropped += c.dropped_quotes.length;
    tr.append(td);
    tb.append(tr);
  }
  $("#clause-count").textContent = `${found}/${r.clauses.length} found`;
  const n = r.clauses.reduce((a, c) => a + c.quotes.length, 0);
  $("#verify-note").textContent =
    `${n} quotes verified against the text${repaired ? `, ${repaired} repaired to the document's exact wording` : ""}; ` +
    `${dropped} dropped because they could not be found.`;
}

$("#run").addEventListener("click", async () => {
  const b = $("#run");
  b.disabled = true;
  b.textContent = "Reviewing";
  b.classList.add("spin");
  try {
    state.review = await api(`/api/documents/${state.id}/review`, { method: "POST" });
    renderAll();
    loadMeta();
  } catch (e) {
    $("#doc-kicker").textContent = `Review failed: ${e.message}`;
  } finally {
    b.disabled = false;
    b.classList.remove("spin");
    renderStats();
  }
});

// ---------- Q&A ----------
function renderAnswer(a) {
  const li = el("li");
  li.append(el("div", "qline", a.question));
  li.append(el("div", null, a.answer));
  if (a.answerable && !a.supported) li.append(el("div", "unsup", "no verified citation · treat as unsupported"));
  for (const c of a.citations) {
    const s = el("span", "cite", `${c.section_id || ""} “${c.text.length > 220 ? c.text.slice(0, 220) + "…" : c.text}”`);
    s.addEventListener("click", () => focusSpan(c.start, c.end));
    li.append(s);
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
    $("#question").value = "";
    loadMeta();
  } catch (err) {
    const li = el("li", "unsup", err.message);
    $("#answers").prepend(li);
  } finally {
    btn.disabled = false;
  }
});

// ---------- boot ----------
loadMeta();
loadDocs().then(() => {
  // deep links: #doc=<id>&focus=<category key> opens a document scrolled to a clause
  const m = location.hash.match(/doc=([a-f0-9]+)/);
  const f = location.hash.match(/focus=([a-z_]+)/);
  if (m)
    openDoc(m[1])
      .then(() => {
        if (f) document.querySelector(`.clauses tr[data-cat="${f[1]}"]`)?.click();
        if (f) history.replaceState(null, "", `#doc=${m[1]}&focus=${f[1]}`);
      })
      .catch(() => {});
});
