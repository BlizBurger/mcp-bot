/* Paysage 3D de la séance : temps × prix × activité (ticks MT5), niveaux d'hier, IB, zones, prix.
   Vue de plaisir, aucune logique de trading ici. */
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";

const W = 64, D = 40, H = 11;                       // largeur (temps), profondeur (prix), hauteur (activité)
const SESSION_BUCKETS = 62;                          // 08:20 → 13:30 en tranches de 5 min
const COL = { cyan: 0x00e5ff, magenta: 0xff2bd6, gold: 0xffcf33, green: 0x00ffa3, red: 0xff3b6b, grey: 0x8b9bb4 };

const box = document.getElementById("scene3d");
const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
box.appendChild(renderer.domElement);
const scene = new THREE.Scene();
scene.fog = new THREE.FogExp2(0x04060c, 0.0085);
{                                                   // fond : halo bleu nuit vers le noir
  const c = document.createElement("canvas"); c.width = c.height = 512;
  const g = c.getContext("2d"), r = g.createRadialGradient(256, 170, 20, 256, 256, 360);
  r.addColorStop(0, "#10224a"); r.addColorStop(0.55, "#070d1d"); r.addColorStop(1, "#04060c");
  g.fillStyle = r; g.fillRect(0, 0, 512, 512);
  const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace;
  scene.background = tex;
}
const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 500);
camera.position.set(W * 0.62, 30, D * 1.05);
const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(0, 2, 0);
controls.enableDamping = true;
controls.autoRotate = true;
controls.autoRotateSpeed = 0.45;
controls.maxPolarAngle = Math.PI * 0.49;
controls.minDistance = 20;
controls.maxDistance = 140;
const composer = new EffectComposer(renderer);
composer.addPass(new RenderPass(scene, camera));
const bloom = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.55, 0.5, 0.32);
composer.addPass(bloom);
composer.addPass(new OutputPass());

const floor = new THREE.GridHelper(W + 12, 38, 0x0b3a4a, 0x08202c);
floor.position.y = -0.02;
scene.add(floor);

let world = new THREE.Group(), labels = [], active = false, pulse = null, framed = false, hover = null;
scene.add(world);

function dispose(obj) {
  obj.traverse((o) => { o.geometry?.dispose(); (Array.isArray(o.material) ? o.material : [o.material]).forEach((m) => m?.dispose()); });
}
function label(text, pos, color) {
  const el = document.createElement("div");
  el.className = "scene-label"; el.textContent = text; el.style.color = color;
  box.appendChild(el); labels.push({ el, pos });
}
function line(points, color, opacity = 1) {
  const g = new THREE.BufferGeometry().setFromPoints(points);
  return new THREE.Line(g, new THREE.LineBasicMaterial({ color, transparent: opacity < 1, opacity }));
}
const lerp3 = (a, b, t) => new THREE.Color(a).lerp(new THREE.Color(b), t);
function heat(t) {                                  // cyan → magenta → or, assombri pour garder les couleurs sous le bloom
  const c = t < 0.5 ? lerp3(COL.cyan, COL.magenta, t * 2) : lerp3(COL.magenta, COL.gold, (t - 0.5) * 2);
  return c.multiplyScalar(0.45 + 0.4 * t);
}

function build(d) {
  dispose(world); scene.remove(world); world = new THREE.Group(); scene.add(world);
  labels.forEach((l) => l.el.remove()); labels = [];
  const g = d.grille, nT = g.length, nP = g[0].length;
  const span = Math.max(SESSION_BUCKETS, Math.ceil(d.maintenant) + 1, nT);
  const X = (t) => (t / span) * W - W / 2;
  const Z = (p) => D / 2 - ((p - d.prix_bas) / (d.pas_prix * (nP - 1))) * D;      // prix haut = au fond
  const pMax = d.prix_bas + d.pas_prix * (nP - 1);

  // lissage léger sur l'axe des prix, puis hauteur en racine (les petits reliefs restent visibles)
  const k = [1, 2, 3, 2, 1];
  const sm = g.map((row) => row.map((_, j) => k.reduce((s, w, i) => s + w * (row[j + i - 2] ?? 0), 0) / 9));
  const vmax = Math.max(1, ...sm.flat());
  const hgt = sm.map((row) => row.map((v) => Math.sqrt(v / vmax) * H));

  // surface (sommets temps × prix) + crêtes lumineuses par tranche de temps
  const pos = [], col = [], idx = [];
  for (let i = 0; i < nT; i++) for (let j = 0; j < nP; j++) {
    const p = d.prix_bas + j * d.pas_prix, h = hgt[i][j];
    pos.push(X(i + 0.5), h, Z(p)); const c = heat(h / H); col.push(c.r, c.g, c.b);
  }
  for (let i = 0; i < nT - 1; i++) for (let j = 0; j < nP - 1; j++) {
    const a = i * nP + j, b = a + nP;
    idx.push(a, b, a + 1, b, b + 1, a + 1);
  }
  const sg = new THREE.BufferGeometry();
  sg.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  sg.setAttribute("color", new THREE.Float32BufferAttribute(col, 3));
  sg.setIndex(idx);
  const surface = new THREE.Mesh(sg, new THREE.MeshBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.14,
    side: THREE.DoubleSide, depthWrite: false }));
  world.add(surface);
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(W, D).rotateX(-Math.PI / 2),
    new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false }));    // cible invisible du survol
  world.add(ground);
  hover = { d, nT, nP, span, X, Z, hgt, surface, ground };
  for (let i = 0; i < nT; i++) {
    const pts = [], cols = [];
    for (let j = 0; j < nP; j++) {
      pts.push(new THREE.Vector3(X(i + 0.5), hgt[i][j], Z(d.prix_bas + j * d.pas_prix)));
      const c = heat(hgt[i][j] / H); cols.push(c.r, c.g, c.b);
    }
    const lg = new THREE.BufferGeometry().setFromPoints(pts);
    lg.setAttribute("color", new THREE.Float32BufferAttribute(cols, 3));
    world.add(new THREE.Line(lg, new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.7 })));
  }

  // niveaux d'hier situés dans la plage de prix du jour (± 3 $)
  const inRange = (p) => p >= d.prix_bas - 3 && p <= pMax + 3;
  const clampZ = (p) => Z(Math.min(pMax, Math.max(d.prix_bas, p)));
  const vah = d.niveaux.find((l) => l.nom === "VAH vol"), val = d.niveaux.find((l) => l.nom === "VAL vol");
  if (vah && val && val.prix < pMax && vah.prix > d.prix_bas) {    // value area d'hier : plan doré
    const z1 = clampZ(vah.prix), z2 = clampZ(val.prix);
    const pg = new THREE.PlaneGeometry(W, Math.abs(z2 - z1));
    pg.rotateX(-Math.PI / 2); pg.translate(0, 0.03, (z1 + z2) / 2);
    world.add(new THREE.Mesh(pg, new THREE.MeshBasicMaterial({ color: COL.gold, transparent: true, opacity: 0.07, depthWrite: false })));
  }
  let lastZ = null, stack = 0;
  for (const l of [...d.niveaux].sort((a, b) => b.prix - a.prix)) {
    if (l.type === "HVN" || l.type === "SP" || !inRange(l.prix)) continue;
    const color = l.nom.includes("TPO") ? COL.cyan : l.type === "LVN" ? COL.grey : COL.gold;
    const z = clampZ(l.prix), y = l.type === "POC" ? 0.15 : 0.08;
    world.add(line([new THREE.Vector3(-W / 2, y, z), new THREE.Vector3(W / 2, y, z)], color, l.type === "LVN" ? 0.6 : 1));
    stack = lastZ !== null && Math.abs(z - lastZ) < 1.6 ? stack + 1 : 0;     // étiquettes empilées si niveaux proches
    lastZ = z;
    label(`${l.nom} ${l.prix.toFixed(2)}`, new THREE.Vector3(X(d.maintenant) + 7, y + stack * 1.6, z),
      `#${color.toString(16).padStart(6, "0")}`);
  }

  // IB : cage cyan
  if (d.ib && d.ib_fin) {
    const z1 = Z(d.ib.high), z2 = Z(d.ib.low), x2 = X(Math.min(d.ib_fin, d.maintenant));
    const bg = new THREE.BoxGeometry(x2 - X(0), H * 0.55, Math.abs(z2 - z1));
    bg.translate((X(0) + x2) / 2, H * 0.275, (z1 + z2) / 2);
    world.add(new THREE.LineSegments(new THREE.EdgesGeometry(bg), new THREE.LineBasicMaterial({ color: COL.cyan, transparent: true, opacity: 0.55 })));
    label("IB", new THREE.Vector3(X(0), H * 0.6, z1), "#00e5ff");
  }

  // zones FVG / OB actives : blocs translucides
  for (const zn of d.zones) {
    const color = zn.sens > 0 ? COL.green : COL.red, x1 = X(zn.depuis), x2 = X(d.maintenant);
    const z1 = Z(zn.haut), z2 = Z(zn.bas);
    const bg = new THREE.BoxGeometry(Math.max(0.4, x2 - x1), 1.4, Math.max(0.15, Math.abs(z2 - z1)));
    bg.translate((x1 + x2) / 2, 0.7, (z1 + z2) / 2);
    world.add(new THREE.Mesh(bg, new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.18, depthWrite: false })));
    world.add(new THREE.LineSegments(new THREE.EdgesGeometry(bg), new THREE.LineBasicMaterial({ color })));
  }

  // prix actuel : sphère qui pulse + faisceau
  const iNow = Math.min(nT - 1, Math.max(0, Math.floor(d.maintenant)));
  const jNow = Math.min(nP - 1, Math.max(0, Math.round((d.prix - d.prix_bas) / d.pas_prix)));
  const yNow = hgt[iNow][jNow] + 0.8, xNow = X(d.maintenant), zNow = Z(d.prix);
  pulse = new THREE.Mesh(new THREE.SphereGeometry(0.55, 32, 16), new THREE.MeshBasicMaterial({ color: COL.magenta }));
  pulse.position.set(xNow, yNow, zNow);
  world.add(pulse);
  world.add(line([new THREE.Vector3(xNow, 0, zNow), new THREE.Vector3(xNow, H + 5, zNow)], COL.magenta, 0.7));
  label(d.prix.toFixed(2), new THREE.Vector3(xNow, H + 5.8, zNow), "#ff2bd6");

  // repères de temps (heure de New York) et de prix
  for (let i = 0; i <= span; i += 12) {
    const t = new Date((d.debut + i * d.pas_min * 60) * 1000);
    label(`${String(t.getUTCHours()).padStart(2, "0")}:${String(t.getUTCMinutes()).padStart(2, "0")}`,
      new THREE.Vector3(X(i), 0, D / 2 + 2.5), "#6b7a93");
  }
  const stepP = (pMax - d.prix_bas) > 40 ? 10 : 5;
  for (let p = Math.ceil(d.prix_bas / stepP) * stepP; p <= pMax; p += stepP)
    label(p.toFixed(0), new THREE.Vector3(-W / 2 - 2.5, 0, Z(p)), "#6b7a93");
  if (!framed) { controls.target.set(X(nT / 2), 2, 0); framed = true; }
  document.getElementById("scene-info").textContent =
    `${nT * d.pas_min} min de séance · ${g.flat().reduce((a, b) => a + b, 0).toLocaleString("fr-FR")} ticks`;
}

// --- survol : info-bulle sur le point visé -------------------------------------------------
const tip = document.createElement("div");
tip.className = "scene-tip hidden";
box.appendChild(tip);
const ray = new THREE.Raycaster(), ptr = new THREE.Vector2();
const marker = new THREE.Group();
const mDot = new THREE.Mesh(new THREE.SphereGeometry(0.32, 16, 8), new THREE.MeshBasicMaterial({ color: 0xffffff }));
const mPole = line([new THREE.Vector3(0, 0, 0), new THREE.Vector3(0, 1, 0)], 0xffffff, 0.6);
const mSlice = line([new THREE.Vector3(0, 0, -D / 2), new THREE.Vector3(0, 0, D / 2)], 0xffffff, 0.35);
marker.add(mDot, mPole, mSlice);
marker.visible = false;
scene.add(marker);

const hhmm = (sec, tz) => new Date(sec * 1000).toLocaleTimeString("fr-FR", { timeZone: tz, hour: "2-digit", minute: "2-digit" });
const sgn = (x, n = 2) => (x > 0 ? "+" : "") + Number(x).toFixed(n);

function describe(i, j, price) {
  const { d, nT } = hover, t = d.debut_utc + i * d.pas_min * 60;
  const head = `<b>${hhmm(t, "America/New_York")}–${hhmm(t + d.pas_min * 60, "America/New_York")} NY</b>`
    + ` <span class="muted">· ${hhmm(t, "Europe/Paris")} Paris</span>`;
  const tr = i < nT ? d.tranches[i] : null;
  if (!tr) return `${head}<div class="muted">Pas de cotation à cette heure (pas encore jouée ou marché calme).</div>`;
  const row = d.grille[i], ticks = row[j] ?? 0, rowMax = Math.max(1, ...row);
  const out = [head, `<div>Prix visé : <b>${price.toFixed(2)}</b> · activité ${ticks} ticks (${Math.round(100 * ticks / rowMax)} % du max de la tranche)</div>`,
    `<div>Bougie 5 min : O ${tr.o.toFixed(2)} · H ${tr.h.toFixed(2)} · B ${tr.l.toFixed(2)} · C <b>${tr.c.toFixed(2)}</b>`
    + ` <span class="${tr.c >= tr.o ? "up" : "down"}">(${sgn(tr.c - tr.o)} $)</span></div>`,
    `<div>Pression : <b class="${tr.delta >= 0 ? "up" : "down"}">${sgn(tr.delta, 0)}</b> ${tr.delta >= 0 ? "acheteurs" : "vendeurs"}`
    + ` · CVD ${sgn(tr.cvd, 0)} <span class="muted">(estimation ticks)</span></div>`];
  // POC du jour jusqu'à cette tranche (prix le plus actif depuis le début de séance)
  const acc = d.grille[0].map((_, k) => d.grille.slice(0, i + 1).reduce((s, r) => s + r[k], 0));
  const kPoc = acc.indexOf(Math.max(...acc));
  out.push(`<div>POC du jour à cette heure : <b>${(d.prix_bas + kPoc * d.pas_prix).toFixed(2)}</b></div>`);
  const vah = d.niveaux.find((l) => l.nom === "VAH vol"), val = d.niveaux.find((l) => l.nom === "VAL vol");
  if (vah && val) out.push(`<div>Value area d'hier : ${price > vah.prix ? "<b>au-dessus</b>" : price < val.prix ? "<b>en dessous</b>" : "<b>dedans</b>"}`
    + ` <span class="muted">(${val.prix.toFixed(2)}–${vah.prix.toFixed(2)})</span></div>`);
  const near = d.niveaux.filter((l) => l.type !== "HVN" && l.type !== "SP")
    .sort((a, b) => Math.abs(a.prix - price) - Math.abs(b.prix - price))[0];
  if (near) out.push(`<div>Niveau d'hier le plus proche : <b>${near.nom} ${near.prix.toFixed(2)}</b> (${sgn(near.prix - price)} $)</div>`);
  if (d.ib && d.ib_fin && i + 1 > d.ib_fin) out.push(`<div>IB : ${price > d.ib.high ? "au-dessus du haut" : price < d.ib.low ? "sous le bas" : "dedans"}`
    + ` <span class="muted">(${d.ib.low.toFixed(2)}–${d.ib.high.toFixed(2)})</span></div>`);
  const zn = d.zones.find((z) => z.depuis <= i + 1 && price >= z.bas - 0.25 && price <= z.haut + 0.25);
  if (zn) out.push(`<div class="${zn.sens > 0 ? "up" : "down"}">Dans une zone ${zn.sens > 0 ? "d'achat" : "de vente"} FVG/OB (${zn.bas.toFixed(2)}–${zn.haut.toFixed(2)})</div>`);
  return out.join("");
}

function onPointer(e) {
  if (!hover) return;
  const r = renderer.domElement.getBoundingClientRect();
  ptr.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
  ray.setFromCamera(ptr, camera);
  const hit = ray.intersectObject(hover.surface)[0] || ray.intersectObject(hover.ground)[0];
  if (!hit) { tip.classList.add("hidden"); marker.visible = false; return; }
  hovering = true; controls.autoRotate = false;
  const { d, nP, span, X, Z, hgt } = hover, p = hit.point;
  const i = Math.max(0, Math.min(span - 1, Math.floor(((p.x + W / 2) / W) * span)));
  const j = Math.max(0, Math.min(nP - 1, Math.round(((D / 2 - p.z) / D) * (nP - 1))));
  const price = d.prix_bas + j * d.pas_prix, y = i < hover.nT ? hgt[i][j] : 0;
  marker.visible = true;
  mDot.position.set(X(i + 0.5), y + 0.35, Z(price));
  mPole.position.set(X(i + 0.5), 0, Z(price)); mPole.scale.set(1, y + 0.35, 1);
  mSlice.position.set(X(i + 0.5), 0.05, 0);
  tip.innerHTML = describe(i, j, price);
  tip.classList.remove("hidden");
  const bx = box.getBoundingClientRect(), x = e.clientX - bx.left, yy = e.clientY - bx.top;
  tip.style.left = `${Math.min(x + 16, bx.width - tip.offsetWidth - 8)}px`;
  tip.style.top = `${Math.min(yy + 16, bx.height - tip.offsetHeight - 8)}px`;
}
renderer.domElement.addEventListener("pointermove", onPointer);
renderer.domElement.addEventListener("pointerdown", onPointer);           // tactile : un appui affiche l'info
renderer.domElement.addEventListener("pointerleave", () => {
  tip.classList.add("hidden"); marker.visible = false; hovering = false; controls.autoRotate = autoSpin;
});

function resize() {
  const w = box.clientWidth, h = box.clientHeight;
  if (!w || !h) return;
  renderer.setSize(w, h); composer.setSize(w, h); bloom.setSize(w, h);
  camera.aspect = w / h; camera.updateProjectionMatrix();
}
new ResizeObserver(resize).observe(box);

const v = new THREE.Vector3();
function placeLabels() {
  const w = box.clientWidth, h = box.clientHeight;
  for (const { el, pos } of labels) {
    v.copy(pos).project(camera);
    const hidden = v.z > 1 || Math.abs(v.x) > 1.05 || Math.abs(v.y) > 1.05;
    el.style.display = hidden ? "none" : "block";
    el.style.left = `${(v.x * 0.5 + 0.5) * w}px`; el.style.top = `${(-v.y * 0.5 + 0.5) * h}px`;
  }
}

function loop(t) {
  if (!active) return;
  requestAnimationFrame(loop);
  controls.update();
  if (pulse) { const s = 1 + 0.25 * Math.sin(t / 260); pulse.scale.setScalar(s); }
  composer.render();
  placeLabels();
}

async function refresh() {
  if (!active) return;
  try {
    const r = await fetch("/api/terrain");
    const d = await r.json();
    if (d.vide) { document.getElementById("scene-info").textContent = "pas encore de ticks pour la séance"; return; }
    build(d);
  } catch (e) { document.getElementById("scene-info").textContent = "serveur injoignable"; }
}

let autoSpin = true, hovering = false;               // la rotation s'arrête pendant le survol
document.getElementById("scene-spin").onclick = () => { autoSpin = !autoSpin; controls.autoRotate = autoSpin && !hovering; };
document.addEventListener("tab", (e) => {
  const was = active;
  active = e.detail === "3d";
  if (active && !was) { resize(); refresh(); requestAnimationFrame(loop); }
});
setInterval(refresh, 10000);
