/* « Ma semaine » : progression vers l'objectif de 2 bons trades (depuis le journal) et checklist
   avant d'entrer (cochée dans ce navigateur, remise à zéro chaque jour). */
(() => {
  const PER_DAY = 2, PER_WEEK = 10;                 // règle d'Alex : 2 trades max par jour
  const ITEMS = [
    "Niveaux d'hier à jour (assistant.update lancé ce matin)",
    "Alerte A+ reçue, ou plan écrit avant d'entrer (entrée, SL, TP)",
    "SL et TP saisis dans la fenêtre d'ordre, avant de valider",
    "Taille calculée : risque ≤ 0,5 % (50 $)",
    "Pas d'annonce importante dans les 15 minutes",
    "Pas un trade de revanche ni « pour tester »",
  ];
  const ny = (d) => new Date(d.toLocaleString("en-US", { timeZone: "America/New_York" }));
  const today = () => new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(new Date());
  const store = { get: (k) => { try { return JSON.parse(localStorage.getItem(k) || "null"); } catch (e) { return null; } },
    set: (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* stockage indisponible */ } } };

  function checklist() {
    const key = `xauof-checklist-${today()}`, done = store.get(key) || [];
    const n = done.length;
    document.getElementById("checklist").innerHTML = ITEMS.map((t, i) =>
      `<label class="${done.includes(i) ? "done" : ""}"><input type="checkbox" data-i="${i}" ${done.includes(i) ? "checked" : ""}> ${t}</label>`).join("")
      + `<div class="ok ${n === ITEMS.length ? "up" : "muted"}">${n === ITEMS.length ? "✓ Tout est coché : la décision t'appartient" : `${n} / ${ITEMS.length}`}</div>`;
    document.querySelectorAll("#checklist input").forEach((c) => c.onchange = () => {
      const s = new Set(store.get(key) || []);
      c.checked ? s.add(+c.dataset.i) : s.delete(+c.dataset.i);
      store.set(key, [...s]); checklist();
    });
  }

  async function week() {
    let j;
    try { j = await (await fetch("/api/journal")).json(); } catch (e) { return; }
    const now = ny(new Date()), monday = new Date(now);
    monday.setHours(0, 0, 0, 0); monday.setDate(monday.getDate() - ((monday.getDay() + 6) % 7));
    const friday = new Date(monday); friday.setDate(monday.getDate() + 4);
    const fmt = (d) => d.toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit" });
    document.getElementById("week-range").textContent = `${fmt(monday)} → ${fmt(friday)}`;
    const trades = (j.trades || []).filter((t) => ny(new Date(t.ouverture)) >= monday)
      .sort((a, b) => a.ouverture.localeCompare(b.ouverture));
    const r = trades.reduce((s, t) => s + (t.r ?? 0), 0), pnl = trades.reduce((s, t) => s + t.pnl, 0);
    const sign = (x, d) => (x > 0 ? "+" : "") + x.toFixed(d);
    document.getElementById("week-sum").innerHTML = trades.length
      ? `<span class="${pnl >= 0 ? "up" : "down"}">${sign(pnl, 2)} $</span> · ${sign(r, 2)} R · ${trades.length} trade${trades.length > 1 ? "s" : ""}`
      : "aucun trade cette semaine";
    const perDay = {};
    const dots = trades.map((t) => {
      const d = ny(new Date(t.ouverture)).toDateString(), k = (perDay[d] = (perDay[d] || 0) + 1);
      return `<div class="dot3 ${t.pnl >= 0 ? "win" : "loss"} ${k > PER_DAY ? "extra" : ""}"
        title="${t.setup || ""}">${t.r !== null && t.r !== undefined ? sign(t.r, 1) : sign(t.pnl, 0)}</div>`;
    });
    for (let i = trades.length; i < PER_WEEK; i++) dots.push(`<div class="dot3 slot"></div>`);
    document.getElementById("week-dots").innerHTML = dots.join("");
    discipline(trades);
    const todayN = perDay[ny(new Date()).toDateString()] || 0;
    document.getElementById("week-note").textContent = `Aujourd'hui : ${todayN} / ${PER_DAY} trades. `
      + (todayN >= PER_DAY ? "Limite du jour atteinte : les alertes continuent pour info, mais plus de trade aujourd'hui."
        : `Semaine : ${trades.length} / ${PER_WEEK} max. Seulement les bons setups.`);
  }

  function discipline(trades) {
    // objectifs de discipline, mesurés sur les trades de la semaine (journal MT5)
    const n = trades.length, days = {};
    for (const t of trades) { const d = ny(new Date(t.ouverture)).toDateString(); days[d] = (days[d] || 0) + 1; }
    const nd = Object.keys(days).length;
    const rows = [
      ["SL saisi dès l'entrée", trades.filter((t) => t.sl_initial).length, n],
      ["Risque ≤ 0,5 % (≤ 55 $)", trades.filter((t) => t.risque_usd !== null && t.risque_usd !== undefined && t.risque_usd <= 55).length, n],
      ["Jours à 2 trades maximum", Object.values(days).filter((k) => k <= 2).length, nd],
      ["Trades notés dans le journal (setup rempli)", trades.filter((t) => (t.setup || "").trim()).length, n],
    ];
    document.getElementById("week-disc").innerHTML = n ? rows.map(([label, ok, tot]) =>
      `<div class="${ok === tot ? "up" : "down"}">${ok === tot ? "✓" : "✗"} ${label} : ${ok} / ${tot}</div>`).join("")
      : `<div class="muted">rien à mesurer pour l'instant</div>`;
  }

  checklist();
  week();
  setInterval(week, 60000);
  setInterval(() => { if (!document.querySelector("#checklist")) return; checklist(); }, 300000);   // changement de jour
})();
