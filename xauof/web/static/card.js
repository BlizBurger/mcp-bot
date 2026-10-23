/* Visuel Instagram d'un trade du journal : image de fond choisie par l'utilisateur (reste
   dans le navigateur), carte néon aux couleurs de l'interface, export PNG 1080 × 1350. */
(() => {
  const W = 1080, H = 1350;
  const K = { bg: "#04060c", cyan: "#00e5ff", gold: "#ffcf33", green: "#00ffa3", red: "#ff3b6b", text: "#d7e3f4", muted: "#8494ad" };
  const cv = $("card-canvas"), ctx = cv.getContext("2d");
  let trade = null, number = 0, bg = null;

  const store = { get: (k) => { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set: (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* stockage indisponible */ } } };
  $("card-handle").value = store.get("xauof-handle") || "";

  const num = (x, d = 2) => (x === null || x === undefined ? "—" : Number(x).toFixed(d));
  const signed = (x, d = 2) => (x > 0 ? "+" : "") + num(x, d);
  const nyDate = (iso) => new Date(iso).toLocaleDateString("en-GB", { timeZone: "America/New_York" });
  const nyTime = (iso) => new Date(iso).toLocaleTimeString("en-GB", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit" });
  function duration(a, b) {
    const m = Math.max(0, Math.round((new Date(b) - new Date(a)) / 60000));
    return m < 60 ? `${m} min` : `${Math.floor(m / 60)}h${String(m % 60).padStart(2, "0")}`;
  }

  function rr(x, y, w, h, r) {
    ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
  }
  function text(s, x, y, font, color, align = "left", spacing = 0, glow = 0) {
    ctx.save(); ctx.font = font; ctx.fillStyle = color; ctx.textAlign = align; ctx.textBaseline = "alphabetic";
    if ("letterSpacing" in ctx) ctx.letterSpacing = `${spacing}px`;
    if (glow > 0) { ctx.shadowColor = color; ctx.shadowBlur = glow; }
    if (glow < 0) { ctx.shadowColor = "rgba(0,0,0,0.9)"; ctx.shadowBlur = -glow; }   // lisible sur une image claire
    ctx.fillText(s, x, y); ctx.restore();
  }
  function fit(s, font, max) {
    ctx.save(); ctx.font = font; let out = s;
    while (out.length > 1 && ctx.measureText(out).width > max) out = out.slice(0, -2) + "…";
    ctx.restore(); return out;
  }

  function background() {
    if (!bg) {                                   // fond par défaut : grille de l'interface
      ctx.fillStyle = K.bg; ctx.fillRect(0, 0, W, H);
      ctx.strokeStyle = "rgba(0,229,255,0.06)"; ctx.lineWidth = 1;
      for (let x = 0; x <= W; x += 54) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke(); }
      for (let y = 0; y <= H; y += 54) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke(); }
      return;
    }
    const s = Math.max(W / bg.width, H / bg.height), w = bg.width * s, h = bg.height * s;
    const focus = Number($("card-focus").value);
    ctx.drawImage(bg, (W - w) / 2, (H - h) * focus, w, h);
  }

  function shade(cardY, cardH) {                // voile sombre derrière la carte, image visible ailleurs
    const mid = cardY + cardH / 2, g = ctx.createLinearGradient(0, 0, 0, H);
    const stop = (y, a) => g.addColorStop(Math.min(1, Math.max(0, y / H)), `rgba(4,6,12,${a})`);
    stop(0, 0.55); stop(170, 0.05); stop(mid - cardH, 0.05); stop(mid, 0.7); stop(mid + cardH, 0.05); stop(H - 120, 0.1); stop(H, 0.6);
    ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
  }

  function brand() {
    ctx.save(); ctx.beginPath(); ctx.arc(102, 104, 42, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(4,6,12,0.85)"; ctx.fill();
    ctx.lineWidth = 5; ctx.strokeStyle = K.gold; ctx.shadowColor = K.gold; ctx.shadowBlur = 22; ctx.stroke(); ctx.restore();
    text("Au", 102, 118, "900 36px Orbitron", K.gold, "center", 1, 10);
    text("XAUUSD", 168, 118, "900 44px Orbitron", K.text, "left", 4, -16);
    ctx.save(); ctx.font = "900 44px Orbitron"; if ("letterSpacing" in ctx) ctx.letterSpacing = "4px";
    const w = ctx.measureText("XAUUSD").width; ctx.restore();
    text("·OF", 168 + w + 4, 118, "900 44px Orbitron", K.cyan, "left", 4, 18);
  }

  function corners(x, y, w, h) {                // équerres néon aux coins, comme un viseur
    ctx.save(); ctx.strokeStyle = K.cyan; ctx.lineWidth = 6; ctx.shadowColor = K.cyan; ctx.shadowBlur = 14;
    const L = 46, o = 14;
    for (const [cx, cy, dx, dy] of [[x - o, y - o, 1, 1], [x + w + o, y - o, -1, 1], [x - o, y + h + o, 1, -1], [x + w + o, y + h + o, -1, -1]]) {
      ctx.beginPath(); ctx.moveTo(cx, cy + dy * L); ctx.lineTo(cx, cy); ctx.lineTo(cx + dx * L, cy); ctx.stroke();
    }
    ctx.restore();
  }

  function draw() {
    if (!trade) return;
    const t = trade, short = t.sens === "vente", win = t.pnl >= 0, res = win ? K.green : K.red;
    const setup = (t.setup || "").replace(/^(perso|a\+)\s*[–-]\s*/i, "").trim();
    const cw = 940, ch = setup ? 690 : 640, cx = (W - cw) / 2;
    const pos = $("card-pos").value, cy = pos === "top" ? 200 : pos === "middle" ? (H - ch) / 2 + 40 : H - ch - 110;
    background(); shade(cy, ch); brand();

    ctx.save(); rr(cx, cy, cw, ch, 30);
    ctx.fillStyle = "rgba(6,10,20,0.80)"; ctx.fill();
    ctx.lineWidth = 3; ctx.strokeStyle = K.cyan; ctx.shadowColor = K.cyan; ctx.shadowBlur = 28; ctx.stroke(); ctx.restore();
    corners(cx, cy, cw, ch);

    const px = cx + 56, right = cx + cw - 56;
    text(`TRADE #${number}`, px, cy + 78, "700 30px Orbitron", K.cyan, "left", 4, 12);
    text(`${nyDate(t.ouverture)} · ${nyTime(t.ouverture)} NY`, right, cy + 78, "400 28px 'JetBrains Mono'", K.muted, "right");

    const side = `${short ? "SHORT" : "LONG"} XAUUSD`;
    text(side, px, cy + 160, "900 56px Orbitron", short ? K.red : K.green, "left", 3, 22);
    text(`${num(t.lots)} LOT`, right, cy + 160, "600 34px 'JetBrains Mono'", K.text, "right");

    const big = t.r !== null && t.r !== undefined ? `${signed(t.r, 2)} R` : `${signed(t.pnl, 2)} $`;
    text(big, px, cy + 300, "900 118px Orbitron", res, "left", 2, 34);
    if ($("card-usd").checked && t.r !== null && t.r !== undefined)
      text(`${signed(t.pnl, 2)} $`, right, cy + 300, "600 40px 'JetBrains Mono'", res, "right");

    const cells = [["ENTRY", num(t.prix_entree)], ["EXIT", num(t.prix_sortie)],
      ["STOP LOSS", t.sl_initial ? num(t.sl_initial) : "—"], ["DURATION", duration(t.ouverture, t.fermeture)]];
    const gw = (cw - 112 - 24) / 2, gh = 112;
    cells.forEach(([label, val], i) => {
      const x = px + (i % 2) * (gw + 24), y = cy + 350 + Math.floor(i / 2) * (gh + 18);
      ctx.save(); rr(x, y, gw, gh, 16); ctx.fillStyle = "rgba(0,229,255,0.05)"; ctx.fill();
      ctx.lineWidth = 1.5; ctx.strokeStyle = "rgba(0,229,255,0.28)"; ctx.stroke(); ctx.restore();
      text(label, x + 24, y + 40, "700 20px Orbitron", K.muted, "left", 3);
      text(val, x + 24, y + 90, "600 42px 'JetBrains Mono'", K.text);
    });

    if (setup) text(fit(setup.toUpperCase(), "600 26px Inter", cw - 112), px, cy + ch - 46, "600 26px Inter", K.gold, "left", 2);

    const handle = $("card-handle").value.trim();
    text(handle, 60, H - 48, "600 28px 'JetBrains Mono'", K.text, "left", 0, -14);
    text("NOT FINANCIAL ADVICE", W - 60, H - 48, "600 20px Orbitron", K.text, "right", 3, -14);
  }

  function caption() {
    const t = trade, short = t.sens === "vente";
    const result = t.r !== null && t.r !== undefined ? `${signed(t.r, 2)} R` : `${signed(t.pnl, 2)} $`;
    return [
      `Trade #${number} · ${nyDate(t.ouverture)}`,
      `${short ? "Short" : "Long"} XAUUSD · ${num(t.lots)} lot`, "",
      `Entry: ${num(t.prix_entree)}`, `SL: ${t.sl_initial ? num(t.sl_initial) : "—"}`,
      `Exit: ${num(t.prix_sortie)} → ${result}`, "",
      "Why I took it:", t.note ? t.note : "[2-3 lines: context, level, what the order flow showed]", "",
      "What I learned:", "[1-2 honest lines]", "",
      "Not financial advice. Just my journal.",
      "#XAUUSD #gold #orderflow #tradingjournal #FTMO",
    ].join("\n");
  }

  async function open(id) {
    const sorted = [...journalTrades].sort((a, b) => a.ouverture.localeCompare(b.ouverture));
    trade = sorted.find((t) => String(t.id) === String(id)); number = sorted.indexOf(trade) + 1;
    if (!trade) return;
    await Promise.all(["900 40px Orbitron", "700 20px Orbitron", "600 40px 'JetBrains Mono'", "400 28px 'JetBrains Mono'", "600 26px Inter"]
      .map((f) => document.fonts.load(f).catch(() => null)));
    $("card-caption").value = caption();
    $("card-modal").classList.remove("hidden");
    draw();
  }

  document.addEventListener("click", (e) => {
    const b = e.target.closest(".j-card");
    if (b) open(b.dataset.id);
  });
  $("card-close").onclick = () => $("card-modal").classList.add("hidden");
  $("card-modal").addEventListener("click", (e) => { if (e.target.id === "card-modal") $("card-modal").classList.add("hidden"); });
  $("card-bg").onchange = (e) => {
    const f = e.target.files[0];
    if (!f) return;
    const img = new Image();
    img.onload = () => { bg = img; draw(); };
    img.src = URL.createObjectURL(f);
  };
  for (const id of ["card-focus", "card-pos", "card-usd"]) $(id).onchange = draw;
  $("card-handle").oninput = () => { store.set("xauof-handle", $("card-handle").value); draw(); };
  $("card-dl").onclick = () => cv.toBlob((blob) => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = `xauusd-of-trade-${number}.png`; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }, "image/png");
  $("card-copy").onclick = async () => {
    try { await navigator.clipboard.writeText($("card-caption").value); $("card-copy").textContent = "Copié ✓"; }
    catch (e) { $("card-caption").select(); document.execCommand("copy"); $("card-copy").textContent = "Copié ✓"; }
    setTimeout(() => { $("card-copy").textContent = "Copier la légende"; }, 1500);
  };
})();
