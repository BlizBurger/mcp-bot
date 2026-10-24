/* XAUUSD OF — interface (lecture seule). */
const C = { cyan: "#00e5ff", magenta: "#ff2bd6", gold: "#ffcf33", green: "#00ffa3", red: "#ff3b6b", grey: "#8b9bb4" };
const $ = (id) => document.getElementById(id);
const fmt = (x, d = 2) => (x === null || x === undefined || Number.isNaN(x) ? "—" : Number(x).toFixed(d));
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const sign = (x, d = 0) => (x > 0 ? "+" : "") + fmt(x, d);

let state = null, tf = 1, bars = [], priceLines = [], journalTrades = [];

/* ---------------------------------------------------------------- graphiques */
const LW = LightweightCharts;
const base = {
  layout: { background: { type: "solid", color: "transparent" }, textColor: "#8fa3c0", fontFamily: "JetBrains Mono" },
  grid: { vertLines: { color: "rgba(0,229,255,0.05)" }, horzLines: { color: "rgba(0,229,255,0.05)" } },
  rightPriceScale: { borderColor: "rgba(0,229,255,0.15)" },
  timeScale: { borderColor: "rgba(0,229,255,0.15)", timeVisible: true, secondsVisible: false },
  crosshair: { mode: LW.CrosshairMode.Normal },
};
const chart = LW.createChart($("chart"), { ...base, autoSize: true });
chart.timeScale().applyOptions({ rightOffset: 6 });
const candles = chart.addCandlestickSeries({
  upColor: C.green, downColor: C.red, borderUpColor: C.green, borderDownColor: C.red,
  wickUpColor: C.green, wickDownColor: C.red,
  // l'échelle inclut toujours le niveau d'hier (ou la borne d'IB) le plus proche au-dessus et en dessous
  autoscaleInfoProvider: (original) => {
    const r = original();
    if (!r || !state || !state.prix) return r;
    const p = state.prix.bid, lv = (state.niveaux || []).map((l) => l.prix);
    if (state.ib) lv.push(state.ib.low, state.ib.high);
    const above = lv.filter((x) => x > p), below = lv.filter((x) => x < p);
    const pad = 0.6;
    if (above.length) r.priceRange.maxValue = Math.max(r.priceRange.maxValue, Math.min(...above) + pad);
    if (below.length) r.priceRange.minValue = Math.min(r.priceRange.minValue, Math.max(...below) - pad);
    return r;
  },
});
const of = LW.createChart($("ofchart"), { ...base, autoSize: true, rightPriceScale: { visible: true, borderColor: "rgba(0,229,255,0.15)" },
  leftPriceScale: { visible: false } });
const deltaS = of.addHistogramSeries({ priceScaleId: "left", priceLineVisible: false });
const cvdS = of.addLineSeries({ color: C.magenta, lineWidth: 2, priceLineVisible: false });
let syncing = false, framed = false;
const sync = (a, b) => a.timeScale().subscribeVisibleTimeRangeChange((r) => {
  if (syncing || !r) return; syncing = true;
  try { b.timeScale().setVisibleRange(r); } catch (e) { /* données pas encore chargées */ }
  syncing = false; drawOverlay();
});
sync(chart, of); sync(of, chart);

/* ---------------------------------------------------------------- calque zones / bandes */
const canvas = $("overlay");
function nearestTime(t) {
  if (!bars.length) return null;
  let best = bars[0].time;
  for (const b of bars) { if (b.time <= t) best = b.time; else break; }
  return best;
}
function drawOverlay() {
  const ctx = canvas.getContext("2d");
  const w = canvas.clientWidth, h = canvas.clientHeight, dpr = window.devicePixelRatio || 1;
  canvas.width = w * dpr; canvas.height = h * dpr; ctx.scale(dpr, dpr); ctx.clearRect(0, 0, w, h);
  if (!state || !bars.length) return;
  const y = (p) => candles.priceToCoordinate(p);
  const plotW = w - 70;
  const band = (lo, hi, fill, stroke) => {
    const y1 = y(hi), y2 = y(lo); if (y1 === null || y2 === null) return;
    ctx.fillStyle = fill; ctx.fillRect(0, y1, plotW, Math.max(1, y2 - y1));
    if (stroke) { ctx.strokeStyle = stroke; ctx.setLineDash([4, 4]); ctx.strokeRect(0, y1, plotW, Math.max(1, y2 - y1)); ctx.setLineDash([]); }
  };
  for (const l of state.niveaux || []) {
    if (l.type === "HVN") band(l.bas, l.haut, "rgba(255,207,51,0.07)");
    if (l.type === "SP") band(l.bas, l.haut, "rgba(0,229,255,0.07)");
  }
  if (state.ib) band(state.ib.low, state.ib.high, "rgba(0,229,255,0.04)", "rgba(0,229,255,0.35)");
  const ts = chart.timeScale();
  for (const z of state.zones || []) {
    const x0 = ts.timeToCoordinate(nearestTime(z.depuis)); const y1 = y(z.haut), y2 = y(z.bas);
    if (x0 === null || y1 === null || y2 === null) continue;
    const bull = z.sens > 0;
    const grad = ctx.createLinearGradient(x0, 0, plotW, 0);
    grad.addColorStop(0, bull ? "rgba(0,255,163,0.35)" : "rgba(255,59,107,0.35)");
    grad.addColorStop(1, bull ? "rgba(0,255,163,0.06)" : "rgba(255,59,107,0.06)");
    ctx.fillStyle = grad; ctx.fillRect(x0, y1, plotW - x0, Math.max(2, y2 - y1));
    ctx.fillStyle = bull ? C.green : C.red; ctx.font = "10px JetBrains Mono";
    ctx.fillText(`${z.type} ${bull ? "▲" : "▼"}`, x0 + 4, y1 - 3);
  }
  drawProfiles();
}

/* profils volume + TPO sur le côté droit (hier : vrai volume GC ; jour : ticks MT5) */
let profMode = (() => { try { return localStorage.getItem("xauof-prof") || "hier"; } catch (e) { return "hier"; } })();
function drawProfiles() {
  // colonne à droite de l'échelle des prix : mêmes coordonnées verticales que le graphique
  const cv = $("profside"), panel = document.querySelector(".chart-panel");
  const pr = profMode !== "off" && state && state.profils ? state.profils[profMode] : null;
  panel.classList.toggle("no-prof", profMode === "off");
  if (profMode === "off") return;
  const w = cv.clientWidth, h = cv.clientHeight, dpr = window.devicePixelRatio || 1;
  cv.width = w * dpr; cv.height = h * dpr;
  const ctx = cv.getContext("2d"); ctx.scale(dpr, dpr); ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#8fa3c0"; ctx.font = "10px JetBrains Mono";
  if (!pr) { ctx.fillText(profMode === "hier" ? "profil d'hier absent" : "pas encore de séance", 8, 14); return; }
  const y = (p) => candles.priceToCoordinate(p), gap = 8, colW = (w - gap - 12) / 2, x1 = 6, x2 = x1 + colW + gap;
  const col = (pts, va, x0, inC, pocC, label) => {
    ctx.fillStyle = "#8fa3c0"; ctx.fillText(label, x0, 12);
    if (!pts || !pts.length) return;
    const vmax = Math.max(...pts.map((p) => p[1]));
    for (const [p, v] of pts) {
      const ya = y(p + 0.25), yb = y(p - 0.25);
      if (ya === null || yb === null || yb < 18 || ya > h) continue;
      const inside = va && p >= va.val && p <= va.vah;
      ctx.fillStyle = va && Math.abs(p - va.poc) < 0.26 ? pocC : inside ? inC : "rgba(120,130,150,0.45)";
      ctx.fillRect(x0, ya, Math.max(1, (v / vmax) * colW), Math.max(1, yb - ya - 0.5));
    }
  };
  col(pr.volume, pr.va_volume, x1, "rgba(240,180,41,0.85)", "#ffcf33", profMode === "hier" ? "VOLUME" : "ACTIVITÉ");
  col(pr.tpo, pr.va_tpo, x2, "rgba(88,166,255,0.85)", "#9ccaff", "TPO");
  const py = state.prix ? y(state.prix.bid) : null;          // prix actuel en repère
  if (py !== null) { ctx.strokeStyle = "rgba(255,43,214,0.8)"; ctx.setLineDash([3, 3]); ctx.beginPath(); ctx.moveTo(0, py); ctx.lineTo(w, py); ctx.stroke(); ctx.setLineDash([]); }
}
document.querySelectorAll(".prof-btn").forEach((b) => {
  b.classList.toggle("active", b.dataset.prof === profMode);
  b.onclick = () => {
    profMode = b.dataset.prof;
    try { localStorage.setItem("xauof-prof", profMode); } catch (e) { /* stockage indisponible */ }
    document.querySelectorAll(".prof-btn").forEach((x) => x.classList.toggle("active", x === b));
    drawOverlay();
  };
});
new ResizeObserver(drawOverlay).observe($("chart"));

function setLevelLines() {
  priceLines.forEach((l) => candles.removePriceLine(l)); priceLines = [];
  for (const l of state.niveaux || []) {
    let opt = null;
    if (l.nom === "POC vol") opt = { color: C.gold, lineWidth: 2, lineStyle: LW.LineStyle.Solid };
    else if (l.nom === "VAH vol" || l.nom === "VAL vol") opt = { color: C.gold, lineWidth: 1, lineStyle: LW.LineStyle.Dashed };
    else if (l.type === "POC" || l.type === "VAH" || l.type === "VAL") opt = { color: C.cyan, lineWidth: 1, lineStyle: LW.LineStyle.Dotted };
    else if (l.type === "LVN") opt = { color: C.grey, lineWidth: 1, lineStyle: LW.LineStyle.SparseDotted };
    if (opt) priceLines.push(candles.createPriceLine({ price: l.prix, axisLabelVisible: true, title: l.nom, ...opt }));
  }
}

/* ---------------------------------------------------------------- rendu de l'état */
function render(s) {
  state = s;
  $("source").textContent = s.source; $("live-dot").className = "dot on";
  $("clock-ny").textContent = s.heure_ny || "--:--"; $("clock-paris").textContent = s.heure_paris || "--:--";
  if (s.erreur) { banner(s.erreur, "warn"); return; }
  const p = s.prix;
  $("bid").textContent = fmt(p.bid); $("ask").textContent = `ask ${fmt(p.ask)}`;
  $("spread").textContent = `${fmt(p.spread_pts, 0)} pts`;
  $("spread-state").innerHTML = s.spread_bloque ? `<span class="down">${s.spread_bloque}</span>` : `<span class="up">normal</span>`;
  $("context").textContent = s.position_va || "—";
  $("context").className = "big " + (s.position_va === "au-dessus de la VA" ? "up" : s.position_va === "sous la VA" ? "down" : "");
  $("nearest").textContent = s.niveau_proche ? `${s.niveau_proche[0]} ${fmt(s.niveau_proche[1])} (${sign(p.bid - s.niveau_proche[1], 2)} $)` : "niveaux d'hier absents";
  const d5 = s.orderflow.delta_5min;
  $("delta5").textContent = sign(d5); $("delta5").className = "big mono " + (d5 > 0 ? "up" : d5 < 0 ? "down" : "");
  $("cvd").textContent = `CVD séance ${s.orderflow.cvd === null ? "—" : sign(s.orderflow.cvd)} · ${s.orderflow.ticks_5min} ticks`;
  const n = (s.news || [])[0];
  $("news-count").textContent = n ? (n.dans_min >= 60 ? `${Math.floor(n.dans_min / 60)} h ${n.dans_min % 60}` : `${n.dans_min} min`) : "—";
  $("news-name").textContent = n ? `${n.annonce} · ${n.heure}` : "aucune annonce connue";
  $("window").textContent = "· " + s.fenetre;
  const a = s.compte;
  if (a) {
    const lim = s.limites.arret_interne, loss = Math.max(0, a.perte_jour);
    $("risk-fill").style.width = `${Math.min(100, (loss / s.limites.ftmo_jour) * 100)}%`;
    $("risk-mark").style.left = `${(lim / s.limites.ftmo_jour) * 100}%`;
    const wk = a.perte_semaine === null || a.perte_semaine === undefined ? ""
      : ` · semaine <b class="${a.perte_semaine <= 0 ? "up" : "down"}">${sign(-a.perte_semaine, 2)} $</b> (stop à −${s.limites.semaine} $)`;
    $("risk-text").innerHTML = `résultat du jour <b class="${a.perte_jour <= 0 ? "up" : "down"}">${sign(-a.perte_jour, 2)} $</b> · reste <b>${fmt(a.reste_arret_interne)} $</b> avant ton arrêt (${lim} $)`
      + ` · trades <b>${a.trades_jour} / ${a.max_trades_jour}</b>${wk}`;
  }
  const msgs = [];
  if (s.news_cloture) msgs.push(`⚠ Clôture avant annonce majeure : ${s.news_cloture}`);
  else if (s.news_blackout) msgs.push(`⚠ Fenêtre news : ${s.news_blackout}`);
  for (const m of s.alertes_systeme || []) if (!m.startsWith("Spread")) msgs.push(m.startsWith("STOP") ? `⛔ ${m}` : m);
  msgs.length ? banner(msgs.join(" · "), a && a.stop_interne_atteint ? "" : "warn") : $("banner").classList.add("hidden");

  $("setups").innerHTML = (s.setups || []).length ? s.setups.map((x) => `
    <div class="card ${x.zone.sens > 0 ? "buy" : "sell"} ${x.etat === "prix dans la zone" ? "hot" : ""}">
      <div class="t">${x.zone.sens > 0 ? "▲ ACHAT" : "▼ VENTE"} · ${x.zone.type} ${fmt(x.zone.bas)}–${fmt(x.zone.haut)}</div>
      <div class="d">${x.etat} · ${x.contexte}</div>
      <div>${x.niveaux.map((l) => `<span class="chip">${l}</span>`).join("")}</div>
      ${planHtml(x.plan)}
    </div>`).join("") : `<div class="empty">aucun setup A+ pour l'instant</div>`;
  $("alerts").innerHTML = (s.alertes || []).length ? s.alertes.map((x) =>
    `<div class="it ${x.niveau}"><time>${new Date(x.t).toLocaleTimeString("fr-FR", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit" })}</time>${x.texte}</div>`).join("")
    : `<div class="empty">aucune alerte</div>`;
  $("levels-src").textContent = s.niveaux_source ? `séance du ${s.niveaux_source}` : "";
  const rows = (s.niveaux || []).map((l) => ({ ...l, cls: l.type === "TPO" ? "TPO" : l.type }));
  rows.push({ nom: "▶ prix actuel", prix: p.bid, cls: "price", distance: 0 });
  rows.sort((x, y) => y.prix - x.prix);
  $("levels").innerHTML = rows.length > 1 ? rows.map((l) => `
    <div class="lv ${l.nom.includes("TPO") ? "TPO" : l.cls}" data-help="${l.aide || ""}">
      <span>${l.nom}</span><span class="p">${fmt(l.prix)}</span><span class="dist">${l.cls === "price" ? "" : sign(-l.distance, 2)}</span></div>`).join("")
    : `<div class="empty">lance « python -m xauof.assistant.update » le matin</div>`;
  $("ib").innerHTML = s.ib ? `${fmt(s.ib.low)} – ${fmt(s.ib.high)}<div class="small muted">${s.ib.definitive ? "définitive" : "en formation"} · ${fmt(s.ib.high - s.ib.low)} $</div>` : "après 08:20";
  $("zones").innerHTML = (s.zones || []).length ? s.zones.map((z) => `<div class="${z.sens > 0 ? "up" : "down"}">${z.sens > 0 ? "▲" : "▼"} ${z.type} ${fmt(z.bas)}–${fmt(z.haut)}</div>`).join("") : "aucune";
  $("news").innerHTML = (s.news || []).map((x) => `<div class="it"><time>${x.heure}</time>${x.annonce}</div>`).join("") || `<div class="empty">—</div>`;
  setLevelLines(); drawOverlay();
}
function planHtml(p) {
  if (!p) return "";
  if (p.refus) return `<div class="plan muted">Plan : aucun (${p.refus})</div>`;
  return `<div class="plan mono"><span>ENTRÉE <b>${fmt(p.entree)}</b> limite</span><span class="down">SL <b>${fmt(p.sl)}</b></span>
    <span class="up">TP <b>${fmt(p.tp)}</b> <i>avant ${p.cible}</i></span><span>${fmt(p.rr, 1)} R · <b>${fmt(p.lots)}</b> lot ≈ ${fmt(p.risque_usd, 0)} $</span>
    ${p.obstacles.length ? `<span class="muted">obstacles : ${p.obstacles.join(", ")}</span>` : ""}<span class="muted">non validé : à toi de confirmer</span></div>`;
}
function banner(text, cls) { const b = $("banner"); b.textContent = text; b.className = "banner " + (cls || ""); }

/* ---------------------------------------------------------------- boucles */
async function getJSON(url, opt) { const r = await fetch(url, opt); if (!r.ok) throw new Error(r.status); return r.json(); }
async function loadState() {
  let s;
  try { s = await getJSON("/api/state"); } catch (e) {
    if (e.message === "401") { location.href = "/login"; return; }          // session expirée (mode --phone)
    $("live-dot").className = "dot off";
    banner(e.message === "Failed to fetch" || e.name === "TypeError" ? "Serveur injoignable : l'interface est-elle lancée sur le PC ?"
      : `Erreur du serveur (${e.message}) : regarde le terminal PowerShell`, "");
    return;
  }
  try { render(s); } catch (e) { banner(`Erreur d'affichage : ${e.message}`, "warn"); }
}
async function loadBars() {
  try {
    bars = await getJSON(`/api/bars?tf=${tf}`);
    const ofd = await getJSON(`/api/orderflow?tf=${tf}`);         // même unité de temps que les bougies
    // emplacements vides après la dernière bougie (dans les DEUX graphiques, pour rester synchronisés) :
    // les dernières bougies s'arrêtent avant les étiquettes des niveaux, à droite
    const step = tf === 1 ? 60 : 300, bs = chart.timeScale().options().barSpacing || 6;
    const nWs = Math.max(8, Math.ceil(110 / bs));
    const last = Math.max(bars.length ? bars[bars.length - 1].time : 0, ofd.length ? ofd[ofd.length - 1].time : 0);
    const ws = last ? Array.from({ length: nWs }, (_, k) => ({ time: last + step * (k + 1) })) : [];
    candles.setData([...bars, ...ws]);
    if (!framed && bars.length) {                       // cadrage initial : les dernières bougies
      const n = bars.length, show = tf === 1 ? 150 : 72;
      chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - show), to: n + nWs }); framed = true;
    }
    deltaS.setData([...ofd.map((x) => ({ time: x.time, value: x.delta, color: x.delta >= 0 ? "rgba(0,255,163,0.6)" : "rgba(255,59,107,0.6)" })), ...ws]);
    cvdS.setData([...ofd.filter((x) => x.cvd !== null).map((x) => ({ time: x.time, value: x.cvd })), ...ws]);
    drawOverlay();
  } catch (e) { /* le prochain tour réessaie */ }
}
document.querySelectorAll(".tf-btn").forEach((b) => b.onclick = () => {
  document.querySelectorAll(".tf-btn").forEach((x) => x.classList.remove("active")); b.classList.add("active");
  tf = +b.dataset.tf; framed = false; loadBars();
});

/* ---------------------------------------------------------------- infobulles */
const tip = $("tooltip");
document.addEventListener("mousemove", (e) => {
  const el = e.target.closest("[data-help]");
  if (el && el.dataset.help) { tip.textContent = el.dataset.help; tip.style.left = e.clientX + 14 + "px"; tip.style.top = e.clientY + 14 + "px"; tip.classList.remove("hidden"); }
  else tip.classList.add("hidden");
});

/* ---------------------------------------------------------------- journal */
let criteria = [];
async function loadJournal() {
  const j = await getJSON("/api/journal"); criteria = j.criteres; journalTrades = j.trades;
  const g = j.stats.global;
  const card = (label, val, cls = "") => `<div class="kpi"><label>${label}</label><div class="big mono ${cls}">${val}</div></div>`;
  $("jstats").innerHTML = card("TRADES", g.n) + card("GAGNANTS", g.n ? `${Math.round(g.gagnants * 100)} %` : "—")
    + card("PNL", `${sign(g.pnl, 2)} $`, g.pnl >= 0 ? "up" : "down") + card("R MOYEN", g.r_moyen === null ? "—" : sign(g.r_moyen, 2), (g.r_moyen || 0) >= 0 ? "up" : "down")
    + card("A+ (R MOYEN)", j.stats.a_plus ? (j.stats.a_plus.avec.r_moyen ?? "—") : "—") + `<div class="kpi wide"><label>AVERTISSEMENT</label><div class="small">${j.stats.avertissement || "échantillon suffisant pour comparer"}</div></div>`;
  $("jgroups").innerHTML = Object.entries(j.stats.par_critere || {}).map(([c, v]) =>
    `<div class="panel small"><b>${c}</b><div class="muted">avec : ${v.avec.n} trades · R ${v.avec.r_moyen ?? "—"} · ${sign(v.avec.pnl, 2)} $</div><div class="muted">sans : ${v.sans.n} trades · R ${v.sans.r_moyen ?? "—"}</div></div>`).join("");
  $("jtable").innerHTML = `<tr><th>OUVERTURE</th><th>SENS</th><th>LOTS</th><th>ENTRÉE → SORTIE</th><th>PNL</th><th>R</th><th>SETUP</th><th>CRITÈRES</th><th>NOTE</th></tr>` +
    j.trades.slice().reverse().map((t) => `<tr data-id="${t.id}">
      <td class="mono">${new Date(t.ouverture).toLocaleString("fr-FR", { timeZone: "America/New_York" })}
        <div><button class="btn mini j-card" data-id="${t.id}">Visuel</button></div></td>
      <td class="${t.sens === "achat" ? "up" : "down"}">${t.sens}</td><td class="mono">${t.lots}</td>
      <td class="mono">${fmt(t.prix_entree)} → ${fmt(t.prix_sortie)}${t.sl_initial ? `<div class="muted">SL ${fmt(t.sl_initial)}</div>` : ""}</td>
      <td class="mono ${t.pnl >= 0 ? "up" : "down"}">${sign(t.pnl, 2)}</td><td class="mono">${t.r ?? "—"}</td>
      <td><input type="text" class="j-setup" value="${esc(t.setup)}" placeholder="ex. FVG sur VAL"></td>
      <td>${criteria.map((c) => `<label><input type="checkbox" class="j-crit" value="${c}" ${(t.criteres || []).includes(c) ? "checked" : ""}> ${c}</label>`).join("")}</td>
      <td class="note-cell"><textarea class="j-note" rows="3" placeholder="ta lecture, ton ressenti…">${esc(t.note)}</textarea></td></tr>`).join("");
  document.querySelectorAll("#jtable tr[data-id]").forEach((tr) => tr.querySelectorAll("input, textarea").forEach((inp) => inp.onchange = async () => {
    const body = { setup: tr.querySelector(".j-setup").value, note: tr.querySelector(".j-note").value,
      criteres: [...tr.querySelectorAll(".j-crit:checked")].map((x) => x.value) };
    await getJSON(`/api/journal/${tr.dataset.id}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    loadJournal();
  }));
}
$("sync").onclick = async () => { $("sync").textContent = "…"; try { await getJSON("/api/journal/sync", { method: "POST" }); } finally { $("sync").textContent = "⟳ Importer depuis MT5"; loadJournal(); } };
document.querySelectorAll(".tab").forEach((b) => b.onclick = () => {
  document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active")); b.classList.add("active");
  for (const t of ["live", "journal", "3d"]) $(`tab-${t}`).classList.toggle("hidden", b.dataset.tab !== t);
  document.dispatchEvent(new CustomEvent("tab", { detail: b.dataset.tab }));
  if (b.dataset.tab === "journal") loadJournal(); else if (b.dataset.tab === "live") drawOverlay();
});

loadState(); loadBars();
setInterval(loadState, 3000); setInterval(loadBars, 5000);
