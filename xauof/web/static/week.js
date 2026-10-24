/* « Ma semaine » : progression vers l'objectif de 2 bons trades (depuis le journal) et checklist
   avant d'entrer (cochée dans ce navigateur, remise à zéro chaque jour). */
(() => {
  const GOAL = 2;
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
    const dots = trades.map((t, i) => `<div class="dot3 ${t.pnl >= 0 ? "win" : "loss"} ${i >= GOAL ? "extra" : ""}"
      title="${t.setup || ""}">${t.r !== null && t.r !== undefined ? sign(t.r, 1) : sign(t.pnl, 0)}</div>`);
    for (let i = trades.length; i < GOAL; i++) dots.push(`<div class="dot3 goal">?</div>`);
    document.getElementById("week-dots").innerHTML = dots.join("");
    document.getElementById("week-note").textContent =
      trades.length < GOAL ? `Encore ${GOAL - trades.length} bon${GOAL - trades.length > 1 ? "s" : ""} setup${GOAL - trades.length > 1 ? "s" : ""} à attendre cette semaine. Patience.`
        : trades.length === GOAL ? "Objectif atteint : tu peux t'arrêter pour la semaine."
          : `${trades.length - GOAL} trade${trades.length - GOAL > 1 ? "s" : ""} au-delà de l'objectif (pointillés) : sont-ils vraiment des bons setups ?`;
  }

  checklist();
  week();
  setInterval(week, 60000);
  setInterval(() => { if (!document.querySelector("#checklist")) return; checklist(); }, 300000);   // changement de jour
})();
