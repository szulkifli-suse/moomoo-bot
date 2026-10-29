// Darkloner US.TECH Stock dashboard. All text goes through textContent, never innerHTML,
// because log data can include news text pulled from the web.
"use strict";

const $ = (s) => document.querySelector(s);
const REFRESH_MS = 10000;
let currentTab = "overview";
let timer = null;
let tradesCache = [];
let limits = { max_invested_pct: 80, max_positions: 4 };

function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else n.setAttribute(k, v);
  }
  for (const k of kids) if (k != null) n.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return n;
}
const money = (v) => (v == null || v === "" ? "–" : Number(v).toLocaleString(undefined, { style: "currency", currency: "USD" }));
const num = (v, d = 2) => (v == null || v === "" ? "–" : Number(v).toFixed(d));
const signCls = (v) => (Number(v) > 0 ? "pos" : Number(v) < 0 ? "neg" : "");
const sym = (s) => String(s || "").replace(/^US\./, "");

async function api(path, opts = {}) {
  const r = await fetch(path, { credentials: "same-origin", ...opts });
  if (r.status === 401) { showLogin(); throw new Error("login"); }
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

function showLogin() {
  $("#app").classList.add("hidden");
  $("#login").classList.remove("hidden");
  clearInterval(timer);
}
async function loadLimits() {
  try {
    const c = await api("/api/config");
    const m1 = /max_invested_pct:\s*([\d.]+)/.exec(c.config_yaml || ""), m2 = /\bmax_positions:\s*(\d+)/.exec(c.config_yaml || "");
    if (m1) limits.max_invested_pct = Number(m1[1]);
    if (m2) limits.max_positions = Number(m2[1]);
  } catch (err) { /* keep defaults */ }
}
function showApp() {
  loadLimits();
  $("#today").textContent = new Date().toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
  $("#login").classList.add("hidden");
  $("#app").classList.remove("hidden");
  refresh();
  clearInterval(timer);
  timer = setInterval(refresh, REFRESH_MS);
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#login-error").textContent = "";
  try {
    const r = await fetch("/api/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: $("#pw").value }),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || "login failed");
    $("#pw").value = "";
    showApp();
  } catch (err) { $("#login-error").textContent = err.message; }
});
$("#logout-btn").addEventListener("click", async () => {
  await fetch("/api/logout", { method: "POST" });
  showLogin();
});

document.querySelectorAll(".tabs button").forEach((b) =>
  b.addEventListener("click", () => {
    document.querySelectorAll(".tabs button").forEach((x) => x.classList.toggle("active", x === b));
    document.querySelectorAll(".tab").forEach((t) => t.classList.add("hidden"));
    currentTab = b.dataset.tab;
    $(`#tab-${currentTab}`).classList.remove("hidden");
    document.querySelector("main").classList.toggle("wide", currentTab !== "overview");
    refresh();
  }));
document.querySelectorAll("[data-goto]").forEach((b) =>
  b.addEventListener("click", () => document.querySelector(`.tabs button[data-tab="${b.dataset.goto}"]`).click()));
$("#more-btn").addEventListener("click", (e) => {
  e.stopPropagation();
  const open = $("#more-menu").classList.toggle("hidden") === false;
  $("#more-btn").setAttribute("aria-expanded", String(open));
});
document.addEventListener("click", (e) => {
  if (!e.target.closest(".dl-more-wrap")) { $("#more-menu").classList.add("hidden"); $("#more-btn").setAttribute("aria-expanded", "false"); }
});

$("#halt-btn").addEventListener("click", () => $("#halt-dialog").showModal());
$("#halt-dialog").addEventListener("close", async () => {
  if ($("#halt-dialog").returnValue !== "confirm") return;
  try {
    await api("/api/halt", { method: "POST", headers: { "X-Requested-With": "claude-dashboard" } });
    refresh();
  } catch (err) { alert(`Halt failed: ${err.message}`); }
});
["#act-agent", "#act-symbol", "#act-errors"].forEach((s) => $(s).addEventListener("change", refresh));
$("#trade-filter").addEventListener("input", () => renderTrades(tradesCache));

// ---------- charts (plain SVG)
function lineChart(container, points, opts = {}) {
  container.replaceChildren();
  if (!points || points.length < 2) { container.append(el("p", { class: "muted", text: "Not enough data yet." })); return; }
  const W = 800, H = opts.height || 260, P = 36;
  const xs = points.map((p) => new Date(p[0]).getTime());
  const ys = points.map((p) => Number(p[1]));
  const xmin = Math.min(...xs), xmax = Math.max(...xs);
  let ymin = Math.min(...ys), ymax = Math.max(...ys);
  if (opts.zeroTop) { ymax = 0; } if (ymin === ymax) { ymin -= 1; ymax += 1; }
  const sx = (x) => P + ((x - xmin) / (xmax - xmin || 1)) * (W - P - 8);
  const sy = (y) => 8 + (1 - (y - ymin) / (ymax - ymin)) * (H - P);
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("preserveAspectRatio", "none");
  for (let i = 0; i <= 3; i++) {
    const yv = ymin + ((ymax - ymin) * i) / 3;
    const g = document.createElementNS(NS, "line");
    g.setAttribute("class", "grid"); g.setAttribute("x1", P); g.setAttribute("x2", W - 8);
    g.setAttribute("y1", sy(yv)); g.setAttribute("y2", sy(yv)); svg.append(g);
    const t = document.createElementNS(NS, "text");
    t.setAttribute("x", 2); t.setAttribute("y", sy(yv) + 4); t.textContent = opts.fmt ? opts.fmt(yv) : yv.toFixed(0);
    svg.append(t);
  }
  const d = points.map((p, i) => `${i ? "L" : "M"}${sx(xs[i]).toFixed(1)},${sy(ys[i]).toFixed(1)}`).join("");
  const path = document.createElementNS(NS, "path");
  if (opts.area) {
    path.setAttribute("class", "area");
    path.setAttribute("d", `${d}L${sx(xmax)},${sy(0)}L${sx(xmin)},${sy(0)}Z`);
  } else { path.setAttribute("class", "line"); path.setAttribute("d", d); }
  svg.append(path);
  for (const [i, x] of [[0, xmin], [1, xmax]]) {
    const t = document.createElementNS(NS, "text");
    t.setAttribute("x", i ? W - 90 : P); t.setAttribute("y", H - 6);
    t.textContent = new Date(x).toLocaleDateString(); svg.append(t);
  }
  container.append(svg);
}

function drawdownSeries(points) {
  let peak = -Infinity;
  return points.map(([t, v]) => { peak = Math.max(peak, v); return [t, peak ? ((v - peak) / peak) * 100 : 0]; });
}

function table(headers, rows) {
  const t = el("table");
  const tr = el("tr");
  headers.forEach(([h, cls]) => tr.append(el("th", { class: cls || "", text: h })));
  t.append(el("thead", {}, tr));
  const tb = el("tbody");
  rows.forEach((r) => {
    const row = el("tr");
    r.forEach((c) => {
      if (c instanceof Node) row.append(el("td", {}, c));
      else if (c && typeof c === "object") row.append(el("td", { class: c.cls || "", text: c.text ?? "" }));
      else row.append(el("td", { text: c ?? "" }));
    });
    tb.append(row);
  });
  t.append(tb);
  return t;
}
const kpi = (label, value, cls = "") => el("div", { class: "kpi" }, el("div", { class: "label", text: label }), el("div", { class: `value ${cls}`, text: value }));
const chainDetails = (chain) => {
  const d = el("details");
  d.append(el("summary", { text: "reason chain" }), el("pre", { text: JSON.stringify(chain || {}, null, 2) }));
  return d;
};

// ---------- renderers
function renderHeader(st) {
  const b = $("#mode-badge");
  b.textContent = st.available ? st.mode : "BOT OFFLINE";
  b.className = `badge ${st.available ? st.mode.toLowerCase() : ""}`;
  const a = $("#alerts"); a.replaceChildren();
  if (!st.available) return;
  if (st.halted) a.append(el("span", { class: "chip bad", text: "Kill switch ON" }));
  if (st.drawdown_halt) a.append(el("span", { class: "chip bad", text: "Drawdown halt" }));
  if (st.daily_loss_halt) a.append(el("span", { class: "chip warn", text: "Daily loss limit" }));
  if (!st.opend_logged_in) a.append(el("span", { class: "chip bad", text: "OpenD not logged in" }));
  if (st.stale_seconds > 180) a.append(el("span", { class: "chip warn", text: `State ${Math.round(st.stale_seconds / 60)} min old` }));
  if (st.regime === "weak") a.append(el("span", { class: "chip warn", text: "Regime: weak" }));
}

const signed = (v, d = 2) => (v == null || v === "" ? "–" : (Number(v) > 0 ? "+" : Number(v) < 0 ? "−" : "") + Math.abs(Number(v)).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }));
const plain = (v) => (v == null || v === "" ? "–" : Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const TINTS = ["sky", "peach", "mint"];

function renderOverview(st) {
  if (!st.available) {
    $("#status-line").textContent = "No state yet";
    $("#pos-rows").replaceChildren(el("li", { class: "dl-empty", text: "The bot has not published any state yet." }));
    return;
  }
  // hero
  $("#hero-equity").textContent = plain(st.equity);
  const start = Number(st.equity) - Number(st.total_pnl);
  const pct = start ? (Number(st.total_pnl) / start) * 100 : 0;
  $("#hero-delta").textContent = `${pct > 0 ? "+" : pct < 0 ? "−" : ""}${Math.abs(pct).toFixed(1)}%`;
  const inv = Number(st.invested_pct) || 0;
  $("#hero-fill").style.width = `${Math.max(0, Math.min(100, (inv / limits.max_invested_pct) * 100))}%`;
  $("#hero-bar").setAttribute("aria-label", `Invested ${inv.toFixed(1)}% of equity, cap ${limits.max_invested_pct}%`);
  $("#hero-invested").textContent = `${inv.toFixed(1)}%`;
  $("#hero-cap").textContent = `of ${limits.max_invested_pct}% cap · ${st.positions.length} of ${limits.max_positions} slots`;
  const hp = $("#hero-pending");
  hp.classList.toggle("hidden", !st.pending.length);
  hp.textContent = `${st.pending.length} pending order${st.pending.length === 1 ? "" : "s"}`;
  // positions
  $("#pos-count").textContent = `· ${signed(st.day_pnl)} today`;
  $("#pos-rows").replaceChildren(...(st.positions.length ? st.positions.map((p, i) => el("li", { class: "dl-rowi" },
    el("div", { class: `dl-tile ${TINTS[i % 3]}`, text: sym(p.symbol).slice(0, 5) }),
    el("div", { class: "dl-main" }, el("div", { class: "dl-title", text: sym(p.symbol) }),
      el("div", { class: "dl-sub", text: `${num(p.qty, 4).replace(/\.?0+$/, "")} @ ${num(p.entry)} · stop ${num(p.stop)} · day ${p.days_held}${p.partial_taken ? " · half sold" : ""}` })),
    el("div", { class: `dl-amt ${signCls(p.pnl_usd)}`, text: signed(p.pnl_usd) })))
    : [el("li", { class: "dl-empty", text: "No open positions." })]));
  // next scan + P&L
  $("#scan-label").textContent = st.next_scan_et ? `Next scan · ${st.next_scan_et} ET` : "Next scan · next trading day";
  $("#scan-universe").textContent = String(st.universe_size ?? "–");
  const times = st.scan_times_et || [];
  const done = st.next_scan_et ? Math.max(0, times.indexOf(st.next_scan_et)) : times.length;
  $("#scan-dots").replaceChildren(...times.map((_, i) => el("i", { class: i < done ? "on" : "" })));
  $("#scan-dots").setAttribute("aria-label", `${done} of ${times.length} scans done today`);
  const tot = $("#stat-total");
  tot.textContent = signed(st.total_pnl); tot.className = `dl-fig ${signCls(st.total_pnl)}`;
  $("#stat-day").textContent = signed(st.day_pnl);
  $("#stat-dd").textContent = `${num(st.drawdown_pct, 1)}%`;
  // funnel bars
  const f = st.funnel_today || {};
  const rows = [];
  if (f.scout) rows.push(["Scanned", f.scout.in]);
  for (const k of ["scout", "filter", "analyst", "risk", "executor"]) if (f[k]) rows.push([k[0].toUpperCase() + k.slice(1), f[k].out]);
  const max = Math.max(1, ...rows.map((r) => Number(r[1]) || 0));
  const bars = [];
  rows.forEach(([name, n], i) => {
    const b = el("span", { class: `dl-bar ${i === 0 ? "lead" : ""}` });
    b.style.width = `${Math.max(4, ((Number(n) || 0) / max) * 100)}%`;
    bars.push(el("span", { text: name }), b, el("span", { class: "dl-val", text: String(n ?? 0) }));
  });
  $("#funnel-bars").replaceChildren(...(bars.length ? bars : [el("span", { class: "dl-empty", text: "No scans yet today." })]));
  const top = (st.top_rejections_today || [])[0];
  $("#funnel-note").textContent = top ? `Top reject: ${top[0]}` : "";
  // equity
  $("#status-line").textContent = `updated ${new Date(st.updated_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
  lineChart($("#equity-chart"), st.equity_history, { height: 160, fmt: (v) => "$" + v.toFixed(0) });
  lineChart($("#dd-chart"), drawdownSeries(st.equity_history), { height: 96, area: true, zeroTop: true, fmt: (v) => v.toFixed(1) + "%" });
  // action bar
  $("#bar-regime").textContent = `Regime ${st.regime || "–"}`;
  $("#bar-market").textContent = `· US market ${st.market_open ? "open" : "closed"}`;
}

function renderPositions(st) {
  if (!st.available) return;
  $("#positions").replaceChildren(st.positions.length ? table(
    [["Symbol"], ["Qty", "num"], ["Entry", "num"], ["Price", "num"], ["P&L", "num"], ["R", "num"], ["Stop", "num"], ["Days", "num"], ["Partial"], ["Why"]],
    st.positions.map((p) => [sym(p.symbol), { text: num(p.qty, 4).replace(/\.?0+$/, ""), cls: "num" }, { text: num(p.entry), cls: "num" },
      { text: num(p.price), cls: "num" }, { text: `${money(p.pnl_usd)} (${num(p.pnl_pct, 1)}%)`, cls: `num ${signCls(p.pnl_usd)}` },
      { text: num(p.r_multiple), cls: "num" }, { text: num(p.stop), cls: "num" }, { text: p.days_held, cls: "num" },
      p.partial_taken ? "taken" : "–", chainDetails(p.reason_chain)])) : el("p", { class: "muted", text: "No open positions." }));
  $("#pending").replaceChildren(st.pending.length ? table([["Symbol"], ["Qty", "num"], ["Limit", "num"], ["Placed"]],
    st.pending.map((p) => [sym(p.symbol), { text: p.qty, cls: "num" }, { text: num(p.limit), cls: "num" }, new Date(p.placed_at).toLocaleTimeString()]))
    : el("p", { class: "muted", text: "None." }));
}

function renderTrades(rows) {
  const f = $("#trade-filter").value.trim().toUpperCase();
  const shown = rows.filter((t) => !f || `${t.symbol} ${t.exit_reason} ${t.mode}`.toUpperCase().includes(f));
  $("#trades").replaceChildren(shown.length ? table(
    [["Closed"], ["Symbol"], ["Mode"], ["Qty", "num"], ["Entry", "num"], ["Exit", "num"], ["P&L", "num"], ["R", "num"], ["Days", "num"], ["Fees", "num"], ["Reason"]],
    shown.map((t) => [new Date(t.closed_at).toLocaleDateString(), sym(t.symbol), t.mode, { text: t.qty, cls: "num" },
      { text: num(t.entry_price), cls: "num" }, { text: num(t.exit_price), cls: "num" },
      { text: money(t.pnl_usd), cls: `num ${signCls(t.pnl_usd)}` }, { text: t.r_multiple, cls: "num" },
      { text: t.days_held, cls: "num" }, { text: money(t.fees_usd), cls: "num" }, t.exit_reason])) : el("p", { class: "muted", text: "No closed trades yet." }));
}

function funnelText(stages) {
  return Object.entries(stages || {}).map(([k, v]) => `${k} ${v.in}→${v.out}${v.skipped ? " (" + v.skipped + ")" : ""}`).join("  ·  ") || "No scans yet.";
}

function renderFunnel(st, scans) {
  if (st.available) {
    $("#funnel-today").replaceChildren(el("p", { text: funnelText(st.funnel_today) }));
    $("#rejections").replaceChildren(st.top_rejections_today.length ? table([["Rule"], ["Count", "num"]],
      st.top_rejections_today.map(([r, n]) => [r, { text: n, cls: "num" }])) : el("p", { class: "muted", text: "None today." }));
    $("#near").replaceChildren(st.near_misses_today.length ? table([["Symbol"], ["Stage"], ["Rule"]],
      st.near_misses_today.map((n) => [sym(n.symbol), n.stage, n.rule])) : el("p", { class: "muted", text: "None today." }));
  }
  $("#scans").replaceChildren(scans.length ? table([["Time"], ["Slot"], ["Regime"], ["Funnel"]],
    scans.map((s) => [new Date(s.ts).toLocaleString(), s.checkpoint, s.regime, funnelText(s.stages)])) : el("p", { class: "muted", text: "No scans yet." }));
}

function describe(e) {
  const skip = new Set(["ts", "mode", "agent", "kind", "symbol", "chain"]);
  return Object.entries(e).filter(([k]) => !skip.has(k)).map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`).join("  ");
}
function renderActivity(events) {
  const errorsOnly = $("#act-errors").checked;
  const isErr = (e) => /error|fail|halt|mismatch/.test(e.kind);
  const list = errorsOnly ? events.filter(isErr) : events;
  $("#activity").replaceChildren(...(list.length ? list.map((e) => el("div", { class: `ev ${isErr(e) ? "err" : ""}` },
    el("div", { class: "meta", text: `${new Date(e.ts).toLocaleString()} · ${e.agent} · ${e.kind}${e.symbol ? " · " + sym(e.symbol) : ""}` }),
    el("div", { text: describe(e).slice(0, 600) }))) : [el("p", { class: "muted", text: "No activity." })]));
}

function renderBacktest(bt) {
  const box = $("#bt-summary"); box.replaceChildren();
  if (!bt || bt.available === false || !bt.base) {
    box.append(el("div", { class: "card" }, el("p", { text: "No backtest yet. Run scripts/backtest.sh." })));
    $("#bt-chart").replaceChildren(); $("#bt-notes").replaceChildren(); return;
  }
  const rows = ["base", "regime_off", "partial_off"].map((k) => {
    const r = bt[k];
    return [k, { text: r.trades, cls: "num" }, { text: r.win_rate == null ? "–" : r.win_rate + "%", cls: "num" },
      { text: num(r.profit_factor), cls: "num" }, { text: num(r.avg_r), cls: "num" }, { text: r.max_drawdown_pct + "%", cls: "num" },
      { text: money(r.final_equity), cls: "num" }, { text: r.cagr_pct == null ? "–" : r.cagr_pct + "%", cls: "num" },
      { text: money(r.total_fees), cls: "num" }, { text: num(r.avg_holding_days, 1), cls: "num" }];
  });
  const gate = el("ul");
  Object.entries(bt.gate || {}).forEach(([k, ok]) => gate.append(el("li", { class: ok ? "gate-ok" : "gate-no", text: `${ok ? "✓" : "✗"} ${k.replaceAll("_", " ")}` })));
  box.append(el("div", { class: "card" }, el("h2", { text: `Backtest · ${bt.years}y · ${bt.universe_size} symbols · ${new Date(bt.generated_at).toLocaleString()}` }),
    table([["Variant"], ["Trades", "num"], ["Win", "num"], ["PF", "num"], ["Avg R", "num"], ["Max DD", "num"], ["Final", "num"], ["CAGR", "num"], ["Fees", "num"], ["Hold d", "num"]], rows),
    el("h3", { text: "Go-live gate (backtest part)" }), gate));
  lineChart($("#bt-chart"), bt.base.equity_curve, { fmt: (v) => "$" + v.toFixed(0) });
  $("#bt-notes").replaceChildren(...(bt.notes || []).map((n) => el("li", { text: n })));
}

async function refresh() {
  try {
    const st = await api("/api/state");
    renderHeader(st);
    if (currentTab === "overview") renderOverview(st);
    if (currentTab === "positions") renderPositions(st);
    if (currentTab === "trades") {
      const t = await api("/api/trades");
      const s = t.stats;
      $("#trade-stats").replaceChildren(kpi("Trades", s.trades), kpi("Win rate", s.win_rate == null ? "–" : s.win_rate + "%"),
        kpi("Avg win", money(s.avg_win), "pos"), kpi("Avg loss", money(s.avg_loss), "neg"), kpi("Profit factor", num(s.profit_factor)),
        kpi("Total P&L", money(s.total_pnl), signCls(s.total_pnl)), kpi("Fees", money(s.total_fees)));
      tradesCache = t.trades; renderTrades(tradesCache);
    }
    if (currentTab === "funnel") renderFunnel(st, (await api("/api/funnel")).scans);
    if (currentTab === "activity") {
      const q = new URLSearchParams({ agent: $("#act-agent").value, symbol: $("#act-symbol").value.trim() });
      renderActivity((await api(`/api/activity?${q}`)).events);
    }
    if (currentTab === "backtest") renderBacktest(await api("/api/backtest"));
    if (currentTab === "config") {
      const c = await api("/api/config");
      $("#cfg-live").textContent = `LIVE_TRADING = ${c.live_trading}`;
      $("#cfg-yaml").textContent = c.config_yaml;
    }
  } catch (err) { if (err.message !== "login") console.warn(err); }
}

(async () => {
  const r = await fetch("/api/me").then((x) => x.json()).catch(() => ({}));
  if (r.authenticated) showApp(); else showLogin();
})();
