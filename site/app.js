/* Shipping Morning Brief — all client logic.
   No framework, no build step, no dependencies. Three JSON files in, one page out.

   The teaching mechanism is in linkify(): glossary terms found in any rendered
   text become buttons that open an explanation. It runs over text nodes only, so
   it can never damage existing markup or nest a button inside a button. */

const state = { data: null, glossary: [], companies: [], links: [], byTerm: new Map(),
                byTicker: new Map(), byMetric: new Map(), termRe: null };

const $  = (s, r = document) => r.querySelector(s);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};

/* ---------------------------------------------------------------- formatting */
const fmt = (v, unit) => {
  if (v == null) return "—";
  const dp = Math.abs(v) >= 1000 ? 0 : Math.abs(v) >= 100 ? 2 : Math.abs(v) >= 10 ? 2 : 3;
  const n = v.toLocaleString("en-GB", { minimumFractionDigits: dp, maximumFractionDigits: dp });
  return unit === "USD/day" ? "$" + n : n;
};

/* Direction is shown with an arrow as well as colour, so the signal never
   depends on colour alone. */
const deltaNode = (change, value, hasValue = true) => {
  // No value at all is not the same as a value that did not move. Saying
  // "no change" when nothing was fetched would be a quiet lie.
  if (!hasValue) return el("div", "k-delta flat", "awaiting data");
  if (change == null) return el("div", "k-delta flat", "change unknown");
  if (change === 0) return el("div", "k-delta flat", "unchanged");
  const pct = value ? (change / (value - change)) * 100 : null;
  const cls = change > 0 ? "up" : "down";
  const arrow = change > 0 ? "▲" : "▼";
  const txt = `${arrow} ${change > 0 ? "+" : "−"}${fmt(Math.abs(change))}` +
              (pct != null && isFinite(pct) ? `  (${pct > 0 ? "+" : "−"}${Math.abs(pct).toFixed(1)}%)` : "");
  return el("div", "k-delta " + cls, txt);
};

/* "Stale" means this run could not refresh the number, which is not the same
   as the number being old — a value carried forward minutes ago is both fresh
   and unrefreshed. Label the state, not just the clock. */
const staleLabel = (m) => {
  if (m.value == null) return "no data";
  return "stale";
};

const staleTitle = (m) => {
  if (m.value == null) return "This number could not be fetched and has no previous value to fall back on.";
  return "Stale: today's update could not fetch this, so it is showing the last value, from "
       + (m.as_of ? new Date(m.as_of).toLocaleString("en-GB") : "an earlier run") + ".";
};

const ageText = (d) => {
  if (d == null) return "no data yet";
  if (d < 0.04) return "just now";
  if (d < 1) return `${Math.round(d * 24)}h ago`;
  return `${Math.round(d)}d ago`;
};

const timeText = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  const mins = (Date.now() - d) / 60000;
  if (mins < 60) return `${Math.max(1, Math.round(mins))} min ago`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)}h ago`;
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
};

/* ------------------------------------------------------------- term matching */
function buildTermIndex() {
  // One matcher covers glossary terms AND company names, so a market headline
  // explains its jargon and its companies in the same pass. Keyed by the
  // lowercased surface form; `kind` says which kind of card to open.
  const entries = [];

  for (const t of state.glossary) {
    state.byTerm.set(t.term.toLowerCase(), t);
    entries.push({ text: t.term, kind: "term", key: t.term });
    for (const a of t.aliases || []) {
      state.byTerm.set(a.toLowerCase(), t);
      entries.push({ text: a, kind: "term", key: t.term });
    }
  }

  for (const c of state.companies) {
    for (const tk of c.tickers) state.byTicker.set(tk.toUpperCase(), c);
    for (const name of c.match || [c.name]) {
      entries.push({
        text: name, kind: "company", key: c.name,
        // Short all-caps names like DHT, ZIM, MSC and BHP are only safe to match
        // when the case matches too, or they would fire inside ordinary words.
        exactCase: name.length <= 4 && name === name.toUpperCase(),
      });
    }
  }

  state.matchIndex = new Map();
  for (const e of entries) {
    const k = e.text.toLowerCase();
    // A glossary term wins over a company of the same name — "VLCC" should
    // explain the vessel class, and the company card is reachable from it.
    if (!state.matchIndex.has(k) || e.kind === "term") state.matchIndex.set(k, e);
  }

  // Longest first, so "Baltic Dry Index" wins over "Baltic Exchange" fragments,
  // "clean products" is never split into "clean", and "Maersk Tankers" is not
  // swallowed by "Maersk".
  const surfaces = [...state.matchIndex.values()].sort((a, b) => b.text.length - a.text.length);
  const esc = (x) => x.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  state.termRe = new RegExp("(?<![\\w.])(" + surfaces.map(e => esc(e.text)).join("|") + ")(?![\\w])", "gi");

  for (const l of state.links) state.byMetric.set(l.from, l);
}

/* Wrap glossary terms and tickers in `root`'s text nodes with buttons.
   Each term is linked at most once per container — a headline peppered with
   twelve identical links is harder to read, not more educational. */
function linkify(root) {
  const seen = new Set();
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      if (!node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
      // Never nest inside an existing control, and never rewrite the metadata
      // row — "LNG Prime" is a publication, not the term LNG.
      if (node.parentElement.closest("button, .term, .chip, .meta, .brief-foot, caption, th"))
        return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    }
  });
  const texts = [];
  for (let n = walker.nextNode(); n; n = walker.nextNode()) texts.push(n);

  for (const node of texts) {
    const src = node.nodeValue;
    state.termRe.lastIndex = 0;
    let m, last = 0, frag = null;
    while ((m = state.termRe.exec(src))) {
      const entry = state.matchIndex.get(m[0].toLowerCase());
      if (!entry) continue;
      if (entry.exactCase && m[0] !== entry.text) continue;
      if (seen.has(entry.key)) continue;
      seen.add(entry.key);
      frag = frag || document.createDocumentFragment();
      if (m.index > last) frag.appendChild(document.createTextNode(src.slice(last, m.index)));
      const b = el("button", entry.kind === "company" ? "term is-co" : "term", m[0]);
      b.type = "button";
      if (entry.kind === "company") {
        b.dataset.company = entry.key;
        b.setAttribute("aria-label", `${m[0]} — about this company`);
      } else {
        b.dataset.term = entry.key;
        b.setAttribute("aria-label", `${m[0]} — show definition`);
      }
      frag.appendChild(b);
      last = m.index + m[0].length;
    }
    if (frag) {
      if (last < src.length) frag.appendChild(document.createTextNode(src.slice(last)));
      node.parentNode.replaceChild(frag, node);
    }
  }
}

/* ------------------------------------------------------------------ popovers */
const pop = $("#pop");

function openPop(anchor, build) {
  pop.innerHTML = "";
  const close = el("button", "close", "×");
  close.type = "button";
  close.setAttribute("aria-label", "Close");
  close.onclick = hidePop;
  pop.appendChild(close);
  build(pop);
  linkify(pop);
  pop.hidden = false;

  const r = anchor.getBoundingClientRect();
  const w = pop.offsetWidth, h = pop.offsetHeight;
  let left = r.left + window.scrollX;
  left = Math.max(12, Math.min(left, window.scrollX + window.innerWidth - w - 12));
  // Flip above the anchor when there is no room below.
  const below = r.bottom + 8 + h < window.scrollY + window.innerHeight + window.scrollY;
  let top = r.bottom + window.scrollY + 8;
  if (r.bottom + 8 + h > window.innerHeight && r.top - h - 8 > 0) top = r.top + window.scrollY - h - 8;
  pop.style.left = left + "px";
  pop.style.top = top + "px";
  pop.dataset.open = "1";
  close.focus();
}

function hidePop() {
  pop.hidden = true;
  delete pop.dataset.open;
}

document.addEventListener("click", (e) => {
  const t = e.target.closest(".term, .chip, .kpi, .co-card");
  if (t) {
    e.preventDefault();
    if (t.dataset.term) showTerm(t.dataset.term, t);
    else if (t.dataset.company) showCompany(t.dataset.company, t);
    else if (t.dataset.metric) showMetric(t.dataset.metric, t);
    return;
  }
  if (pop.dataset.open && !e.target.closest("#pop")) hidePop();
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") hidePop(); });
window.addEventListener("resize", hidePop);

function relatedChips(parent, labelText, items) {
  if (!items.length) return;
  parent.appendChild(el("div", "rel-label", labelText));
  const row = el("div", "rel");
  for (const it of items) {
    const b = el("button", "chip", it.label);
    b.type = "button";
    if (it.term) b.dataset.term = it.term;
    if (it.company) b.dataset.company = it.company;
    row.appendChild(b);
  }
  parent.appendChild(row);
}

function showTerm(name, anchor) {
  const t = state.byTerm.get(name.toLowerCase());
  if (!t) return;
  openPop(anchor, (p) => {
    const h = el("h4", null, t.term);
    h.id = "popTitle";
    p.appendChild(h);
    p.appendChild(el("div", "kind", t.segment === "all" ? "Shipping term" : `${t.segment} term`));
    p.appendChild(el("p", "lead", t.short));
    if (t.long) p.appendChild(el("p", null, t.long));
    if (t.why) {
      const w = el("div", "why");
      w.appendChild(el("b", null, "Why it matters: "));
      w.appendChild(document.createTextNode(t.why));
      p.appendChild(w);
    }
    const rel = (t.see_also || [])
      .filter(s => state.byTerm.has(s.toLowerCase()))
      .map(s => ({ label: s, term: state.byTerm.get(s.toLowerCase()).term }));
    relatedChips(p, "Related", rel);
    const cos = state.companies
      .filter(c => (c.terms || []).some(x => x.toLowerCase() === t.term.toLowerCase()))
      .slice(0, 6)
      .map(c => ({ label: c.name, company: c.name }));
    relatedChips(p, "Companies where this matters", cos);
  });
}

function showCompany(name, anchor) {
  const c = state.companies.find(x => x.name === name);
  if (!c) return;
  openPop(anchor, (p) => {
    const h = el("h4", null, c.name);
    h.id = "popTitle";
    p.appendChild(h);
    p.appendChild(el("div", "kind", `${c.segment} · ${c.listed} · ${c.tickers.join(" / ")}`));
    p.appendChild(el("p", "lead", c.what));

    const dl = el("dl");
    const add = (k, v) => { if (!v) return; dl.appendChild(el("dt", null, k)); dl.appendChild(el("dd", null, v)); };
    add("Where it sits in the market", c.position);
    add("It earns more when", c.earns_when);
    if (c.note) add("Note", c.note);
    p.appendChild(dl);

    // Live values for the metrics that actually drive this company.
    const rows = (c.watch || [])
      .map(id => (state.data.metrics || []).concat(state.data.rates || []).find(m => m.id === id))
      .filter(Boolean);
    if (rows.length) {
      p.appendChild(el("div", "rel-label", "Numbers that drive it"));
      for (const m of rows) {
        const r = el("div", "watchrow");
        r.appendChild(el("span", null, m.label));
        const v = el("span", "v",
          m.value == null ? "not available" : `${fmt(m.value, m.unit)} ${m.unit}`);
        r.appendChild(v);
        p.appendChild(r);
      }
    }
    relatedChips(p, "Compare with",
      (c.peers || []).map(t => state.byTicker.get(t.toUpperCase()))
        .filter(Boolean).map(x => ({ label: x.name, company: x.name })));
    relatedChips(p, "Terms to know",
      (c.terms || []).filter(t => state.byTerm.has(t.toLowerCase()))
        .map(t => ({ label: t, term: state.byTerm.get(t.toLowerCase()).term })));
  });
}

function showMetric(id, anchor) {
  const all = (state.data.metrics || []).concat(state.data.rates || []);
  const m = all.find(x => x.id === id);
  if (!m) return;
  const link = state.byMetric.get(id);
  openPop(anchor, (p) => {
    const h = el("h4", null, m.label);
    h.id = "popTitle";
    p.appendChild(h);
    p.appendChild(el("div", "kind",
      m.value == null ? "no data" : `${fmt(m.value, m.unit)} ${m.unit} · ${ageText(m.age_days)}`));
    if (m.blurb) p.appendChild(el("p", "lead", m.blurb));
    if (m.status === "unavailable" && m.why_unavailable) {
      p.appendChild(el("div", "why", m.why_unavailable));
    }

    if (link) {
      p.appendChild(el("div", "rel-label", "What this moves"));
      for (const a of link.affects) {
        const target = all.find(x => x.id === a.to);
        const row = el("p", "ripple");
        const arrow = el("span", "arrow");
        arrow.className = "arrow " + (a.dir === "up" ? "up" : a.dir === "down" ? "down" : "flat");
        arrow.textContent = a.dir === "up" ? "▲" : a.dir === "down" ? "▼" : "◆";
        row.appendChild(arrow);
        const strong = el("b", null, (target ? target.label : a.to) + ": ");
        row.appendChild(strong);
        row.appendChild(document.createTextNode(a.because));
        p.appendChild(row);
      }
      if (link.watch_for) {
        const w = el("div", "why");
        w.appendChild(el("b", null, "Watch out: "));
        w.appendChild(document.createTextNode(link.watch_for));
        p.appendChild(w);
      }
    }
    if (m.source) {
      p.appendChild(el("div", "rel-label", "Source"));
      p.appendChild(el("p", null, m.source + (m.stale ? " — not refreshed today, showing the last value" : "")));
    }
  });
}

/* -------------------------------------------------------------------- render */
const KPI_GROUPS = [
  ["Energy", ["brent", "wti"]],
  ["Gas", ["ttf", "henry_hub"]],
  ["Freight & fuel", ["bdi", "vlsfo_sing"]],
];

function renderKpis() {
  const host = $("#kpis");
  host.innerHTML = "";
  const byId = new Map(state.data.metrics.map(m => [m.id, m]));
  const grouped = new Set(KPI_GROUPS.flatMap(([, ids]) => ids));

  for (const [label, ids] of KPI_GROUPS) {
    const rows = ids.map(id => byId.get(id)).filter(Boolean);
    if (!rows.length) continue;
    const wrap = el("div", "kpi-group");
    wrap.appendChild(el("div", "grp", label));
    const grid = el("div", "kpis");
    for (const m of rows) grid.appendChild(kpiTile(m));
    wrap.appendChild(grid);
    host.appendChild(wrap);
  }

  // Anything flagged as a KPI but not placed in a group above still shows.
  const rest = state.data.metrics.filter(x => x.kpi && !grouped.has(x.id));
  if (rest.length) {
    const wrap = el("div", "kpi-group");
    wrap.appendChild(el("div", "grp", "Other"));
    const grid = el("div", "kpis");
    for (const m of rest) grid.appendChild(kpiTile(m));
    wrap.appendChild(grid);
    host.appendChild(wrap);
  }
  linkify(host);
}

function kpiTile(m) {
  {
    const card = el("button", "kpi" + (m.stale ? " is-stale" : ""));
    card.type = "button";
    card.dataset.metric = m.id;
    const lab = el("div", "k-label");
    lab.appendChild(el("span", "t", m.label));
    if (m.stale) {
      const b = el("span", "badge stale", staleLabel(m));
      b.title = staleTitle(m);
      lab.appendChild(b);
    }
    card.appendChild(lab);
    const val = el("div", "k-value");
    val.appendChild(document.createTextNode(fmt(m.value, m.unit)));
    val.appendChild(el("span", "k-unit", m.unit));
    card.appendChild(val);
    card.appendChild(deltaNode(m.change, m.value, m.value != null));
    card.appendChild(el("div", "k-blurb", m.blurb || ""));
    return card;
  }
}

function newsItem(it) {
  const d = el("div", "item" + (it.routine ? " routine" : ""));

  const meta = el("div", "meta");
  meta.appendChild(el("span", null, it.source));
  if (it.ticker) {
    // Show the company's actual name. A four-letter ticker means nothing to
    // someone still learning who these companies are.
    const c = state.byTicker.get(it.ticker.toUpperCase());
    const chip = el("button", "chip", c ? c.name : it.ticker);
    chip.type = "button";
    if (c) {
      chip.dataset.company = c.name;
      chip.title = `${c.name} (${it.ticker}) — what this company does`;
    } else {
      chip.disabled = true;
    }
    meta.appendChild(chip);
  }
  if (it.category) meta.appendChild(el("span", null, it.category));
  if (it.nordic && it.source !== "Oslo Børs") meta.appendChild(el("span", "tag nordic", "Nordic"));
  if (it.routine) {
    const r = el("span", "tag", "routine filing");
    r.title = "A legally required disclosure, such as a large holder crossing an " +
              "ownership threshold. Required, but rarely the day's news.";
    meta.appendChild(r);
  }
  const t = timeText(it.published);
  if (t) meta.appendChild(el("span", null, t));
  d.appendChild(meta);

  const h = el("div", "headline");
  const a = el("a", null, it.title);
  a.href = it.link || "#";
  a.target = "_blank";
  a.rel = "noopener";
  h.appendChild(a);
  d.appendChild(h);

  // The summary is here so the page is readable without opening every article.
  if (it.summary) d.appendChild(el("p", "summary", it.summary));
  return d;
}

function renderNews() {
  for (const [key, host, countEl, emptyMsg] of [
    ["company", $("#companyNews"), $("#cCount"),
     "No company filings yet. Oslo Børs publishes only the current day, so this fills in as the brief runs each morning."],
    ["market", $("#marketNews"), $("#mCount"), "No headlines retrieved."]
  ]) {
    const items = (state.data.news && state.data.news[key]) || [];
    host.innerHTML = "";
    countEl.textContent = items.length ? `${items.length} items` : "";
    if (!items.length) { host.appendChild(el("div", "empty", emptyMsg)); continue; }
    const shown = items.slice(0, key === "market" ? 22 : 18);
    for (const it of shown) host.appendChild(newsItem(it));
    linkify(host);
  }
}

function table(caption, rows, opts = {}) {
  const wrap = el("div", "tablewrap");
  wrap.style.marginBottom = "14px";
  const t = el("table");
  t.appendChild(el("caption", null, caption));
  const thead = el("thead");
  const hr = el("tr");
  for (const [label, cls] of [["", ""], ["Latest", "num"], ["Change", "num"], ["Updated", "num"]]) {
    hr.appendChild(el("th", cls, label || opts.firstCol || "Route / vessel"));
  }
  thead.appendChild(hr);
  t.appendChild(thead);

  const tb = el("tbody");
  for (const r of rows) {
    const inactive = r.status === "unavailable" || r.value == null;
    const tr = el("tr", inactive ? "inactive" : "");
    const td0 = el("td");
    const btn = el("button", "term r-label", r.label);
    btn.type = "button";
    btn.dataset.metric = r.id;
    btn.setAttribute("aria-label", `${r.label} — details`);
    td0.appendChild(btn);
    if (r.blurb) td0.appendChild(el("div", "r-blurb", r.blurb));
    if (inactive && r.why_unavailable) td0.appendChild(el("div", "r-why", r.why_unavailable));
    tr.appendChild(td0);

    const v = el("td", "num");
    if (r.value == null) { const s = el("span", "dash", "—"); v.appendChild(s); }
    else v.textContent = `${fmt(r.value, r.unit)} ${r.unit}`;
    tr.appendChild(v);

    const c = el("td", "num");
    if (r.change == null) c.appendChild(el("span", "dash", "—"));
    else {
      const sp = el("span", r.change > 0 ? "up" : r.change < 0 ? "down" : "flat",
        `${r.change > 0 ? "▲ +" : r.change < 0 ? "▼ −" : ""}${fmt(Math.abs(r.change))}`);
      c.appendChild(sp);
    }
    tr.appendChild(c);

    const u = el("td", "num");
    if (inactive && r.status === "unavailable") u.appendChild(el("span", "badge", "inactive"));
    else if (r.stale) {
      const b = el("span", "badge stale", staleLabel(r));
      b.title = staleTitle(r);
      u.appendChild(b);
    }
    else u.textContent = ageText(r.age_days);
    tr.appendChild(u);

    tb.appendChild(tr);
  }
  t.appendChild(tb);
  wrap.appendChild(t);
  return wrap;
}

function renderRates() {
  const host = $("#rateTables");
  host.innerHTML = "";
  const rates = state.data.rates || [];
  const groups = [
    ["Tankers — crude and refined products", "tanker"],
    ["Dry bulk", "dry"],
    ["Gas — LNG and LPG", "gas"]
  ];
  for (const [title, seg] of groups) {
    const rows = rates.filter(r => r.segment === seg);
    if (rows.length) host.appendChild(table(title, rows));
  }
  // Daily freight signals that ARE free, so the section is never just grey rows.
  const proxies = state.data.metrics.filter(m => m.group === "rates" || m.group === "proxy");
  if (proxies.length) host.appendChild(table("Daily freight signals (free sources)", proxies));
  linkify(host);
}

function renderEnergy() {
  const host = $("#energyTables");
  host.innerHTML = "";
  for (const [title, grp] of [
    ["Crude oil and refined products", "energy"],
    ["Natural gas and LNG", "gas"],
    ["Bunker fuel — what ships actually pay", "bunkers"]
  ]) {
    const rows = state.data.metrics.filter(m => m.group === grp);
    if (rows.length) host.appendChild(table(title, rows, { firstCol: "Price" }));
  }
  linkify(host);
}

function renderLearn() {
  const cloud = $("#termCloud"), grid = $("#coGrid");
  const q = ($("#search").value || "").trim().toLowerCase();

  const terms = state.glossary.filter(t =>
    !q || t.term.toLowerCase().includes(q) || (t.short + " " + t.long).toLowerCase().includes(q) ||
    (t.aliases || []).some(a => a.toLowerCase().includes(q)));
  cloud.innerHTML = "";
  for (const t of terms.slice().sort((a, b) => a.term.localeCompare(b.term))) {
    const b = el("button", "term", t.term);
    b.type = "button";
    b.dataset.term = t.term;
    cloud.appendChild(b);
  }
  $("#termCount").textContent = `${terms.length} of ${state.glossary.length}`;

  const cos = state.companies.filter(c =>
    !q || c.name.toLowerCase().includes(q) || c.segment.toLowerCase().includes(q) ||
    c.tickers.some(t => t.toLowerCase().includes(q)) || c.what.toLowerCase().includes(q));
  grid.innerHTML = "";
  for (const c of cos.slice().sort((a, b) => a.name.localeCompare(b.name))) {
    const card = el("button", "co-card");
    card.type = "button";
    card.dataset.company = c.name;
    card.appendChild(el("div", "t", c.name));
    card.appendChild(el("div", "s", `${c.segment} · ${c.tickers[0]}`));
    grid.appendChild(card);
  }
  $("#coCount").textContent = `${cos.length} of ${state.companies.length}`;
}

function renderBrief() {
  const host = $("#briefBox");
  const b = state.data.brief;
  if (!b || !b.text) { host.hidden = true; return; }
  host.hidden = false;
  host.innerHTML = "";

  const head = el("div", "brief-head");
  head.appendChild(el("span", "brief-label", "Today's brief"));
  const tag = el("span", "tag ai", "AI-written");
  tag.title = `Written by ${b.model} from only the data on this page. ` +
              "Everything else on the dashboard is collected directly from its source.";
  head.appendChild(tag);
  host.appendChild(head);

  for (const para of b.text.split(/\n{2,}/)) {
    if (para.trim()) host.appendChild(el("p", null, para.trim()));
  }
  host.appendChild(el("div", "brief-foot",
    "Written from the numbers and headlines on this page only — it has no other " +
    "sources and is told to say so when the data does not explain a move. Check it " +
    "against the figures below."));
  linkify(host);
}

function renderDiagnostics() {
  const d = state.data.diagnostics || {};
  $("#diagSummary").textContent =
    `Data sources and status — ${d.metrics_fresh ?? "?"} of ${d.metrics_total ?? "?"} prices fresh, ` +
    `${d.rates_active ?? 0} of ${d.rates_total ?? 0} freight routes active`;
  const body = $("#diagBody");
  body.innerHTML = "";
  const p = el("p", null,
    "Every number on this page carries its own source and timestamp. Anything that " +
    "could not be refreshed keeps its previous value and is marked stale rather than " +
    "being shown as zero or blank. Nothing here is estimated or generated.");
  body.appendChild(p);
  if ((d.errors || []).length) {
    body.appendChild(el("div", "rel-label", "Sources that failed on the last run"));
    const ul = el("ul");
    for (const e of d.errors) ul.appendChild(el("li", null, e));
    body.appendChild(ul);
  }
}

/* ---------------------------------------------------------------------- boot */
async function boot() {
  const bust = `?v=${Date.now()}`;
  try {
    const [data, glossary, companies, links] = await Promise.all([
      fetch("data/latest.json" + bust).then(r => r.json()),
      fetch("content/glossary.json").then(r => r.json()),
      fetch("content/companies.json").then(r => r.json()),
      fetch("content/links.json").then(r => r.json())
    ]);
    state.data = data;
    state.glossary = glossary.terms;
    state.companies = companies.companies;
    state.links = links.links;
  } catch (err) {
    $("#tone").textContent =
      "Could not load the brief data. If you are opening this file directly, " +
      "serve the folder over HTTP instead — browsers block local file reads.";
    console.error(err);
    return;
  }

  buildTermIndex();

  const gen = new Date(state.data.generated_at);
  $("#briefDate").textContent = gen.toLocaleDateString("en-GB",
    { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  $("#asof").textContent = "· updated " + timeText(state.data.generated_at);
  $("#tone").textContent = state.data.tone || "";

  renderBrief();
  renderKpis();
  renderNews();
  renderRates();
  renderEnergy();
  renderLearn();
  renderDiagnostics();
  linkify($("#tone"));

  $("#search").addEventListener("input", renderLearn);

  const eb = $("#explainBtn");
  const explainOn = localStorage.getItem("explain") === "1";
  const setExplain = (on) => {
    document.body.classList.toggle("explain", on);
    eb.setAttribute("aria-pressed", String(on));
    try { localStorage.setItem("explain", on ? "1" : "0"); } catch (e) {}
  };
  setExplain(explainOn);
  eb.onclick = () => setExplain(!document.body.classList.contains("explain"));

  const tb = $("#themeBtn");
  const saved = (() => { try { return localStorage.getItem("theme"); } catch (e) { return null; } })();
  if (saved) document.documentElement.dataset.theme = saved;
  tb.onclick = () => {
    const cur = document.documentElement.dataset.theme;
    const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const next = cur ? (cur === "dark" ? "light" : "dark") : (prefersDark ? "light" : "dark");
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("theme", next); } catch (e) {}
  };
}

boot();
