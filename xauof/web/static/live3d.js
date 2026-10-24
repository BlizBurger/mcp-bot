/* Bas de l'onglet live : skyline 3D de l'order flow (minute par minute) + réacteur de discipline
   (limite de perte du jour). Vue de plaisir, aucune logique de trading ici. */
import * as THREE from "three";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";

const N = 90, SW = 52, BAR_H = 8;                   // minutes affichées, largeur de la ville, hauteur max
const COL = { cyan: 0x00e5ff, green: 0x00ffa3, red: 0xff3b6b, magenta: 0xff2bd6, gold: 0xffcf33, orange: 0xff8a1f };

const box = document.getElementById("live3d");
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
box.appendChild(renderer.domElement);
const scene = new THREE.Scene();
{
  const c = document.createElement("canvas"); c.width = c.height = 256;
  const g = c.getContext("2d"), r = g.createLinearGradient(0, 0, 0, 256);
  r.addColorStop(0, "#0d1a38"); r.addColorStop(0.6, "#060b18"); r.addColorStop(1, "#04060c");
  g.fillStyle = r; g.fillRect(0, 0, 256, 256);
  const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace; scene.background = tex;
}
const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 400);
const composer = new EffectComposer(renderer);
composer.addPass(new RenderPass(scene, camera));
const bloom = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.7, 0.5, 0.25);
composer.addPass(bloom);
composer.addPass(new OutputPass());

// --- skyline ------------------------------------------------------------------------------
const city = new THREE.Group();
scene.add(city);
const glass = new THREE.Mesh(new THREE.PlaneGeometry(SW + 4, 10),
  new THREE.MeshBasicMaterial({ color: COL.cyan, transparent: true, opacity: 0.05, side: THREE.DoubleSide, depthWrite: false }));
glass.rotation.x = -Math.PI / 2;
city.add(glass);
const grid = new THREE.GridHelper(SW + 4, 28, 0x0b3a4a, 0x08202c);
grid.scale.z = 10 / (SW + 4);
city.add(grid);
const bars = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshBasicMaterial({ toneMapped: false }), N);
bars.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
bars.count = 0;
city.add(bars);
let ribbon = null;
const X = (i) => -SW / 2 + (i + 0.5) * (SW / N);

// --- réacteur -----------------------------------------------------------------------------
const reactor = new THREE.Group();
scene.add(reactor);
const ringBase = new THREE.Mesh(new THREE.TorusGeometry(4.2, 0.12, 12, 120),
  new THREE.MeshBasicMaterial({ color: 0x16304a }));
reactor.add(ringBase);
let ringFill = null;
const ftmoRing = new THREE.Mesh(new THREE.TorusGeometry(5.4, 0.05, 8, 120),
  new THREE.MeshBasicMaterial({ color: 0x1d2a3d }));
reactor.add(ftmoRing);
let ftmoFill = null;
const coreMat = new THREE.MeshBasicMaterial({ color: COL.cyan, wireframe: true, toneMapped: false });
const core = new THREE.Mesh(new THREE.IcosahedronGeometry(1.7, 1), coreMat);
reactor.add(core);
const innerMat = new THREE.MeshBasicMaterial({ color: COL.cyan, transparent: true, opacity: 0.35 });
const inner = new THREE.Mesh(new THREE.SphereGeometry(1.1, 32, 16), innerMat);
reactor.add(inner);
let risk = 0, coreColor = new THREE.Color(COL.cyan);

function arc(radius, tube, frac, color) {
  const m = new THREE.Mesh(new THREE.TorusGeometry(radius, tube, 12, 120, Math.max(0.001, frac) * Math.PI * 2),
    new THREE.MeshBasicMaterial({ color, toneMapped: false }));
  m.rotation.z = Math.PI / 2;                        // départ en haut
  m.scale.x = -1;                                    // sens des aiguilles d'une montre
  return m;
}
function riskColor(u) {
  return u < 0.5 ? new THREE.Color(COL.cyan).lerp(new THREE.Color(COL.orange), u * 2)
    : new THREE.Color(COL.orange).lerp(new THREE.Color(COL.red), (u - 0.5) * 2);
}

// --- mise en page selon la largeur ----------------------------------------------------------
let narrow = false;
const view = { x: 0, y: 2 };                         // centre visé par la caméra
function layout() {
  const w = box.clientWidth, h = box.clientHeight;
  if (!w || !h) return;
  renderer.setSize(w, h); composer.setSize(w, h); bloom.setSize(w, h);
  camera.aspect = w / h;
  narrow = w / h < 1.3;
  let worldW, worldH;
  if (narrow) {                                      // téléphone : réacteur au-dessus de la ville
    city.position.set(0, -6, 0); city.scale.setScalar(0.62);
    reactor.position.set(0, 9, 0); reactor.scale.setScalar(0.85);
    camera.fov = 50; view.x = 0; view.y = 1.5; worldW = SW * 0.62 + 4; worldH = 30;
  } else {
    city.position.set(-7, -1, 0); city.scale.setScalar(1);
    reactor.position.set(SW / 2 + 6, 3, 0); reactor.scale.setScalar(1);
    camera.fov = 40; view.x = 2; view.y = 2; worldW = SW + 46; worldH = 2 * BAR_H + 10;
  }
  // recul de la caméra pour que tout tienne, quelle que soit la taille du panneau
  const vf = THREE.MathUtils.degToRad(camera.fov) / 2, hf = Math.atan(Math.tan(vf) * camera.aspect);
  const dist = 1.08 * Math.max(worldW / 2 / Math.tan(hf), worldH / 2 / Math.tan(vf));
  camera.position.set(view.x, view.y + dist * 0.16, dist);
  camera.updateProjectionMatrix();
}
new ResizeObserver(layout).observe(box);

// --- données ------------------------------------------------------------------------------
const m4 = new THREE.Matrix4(), q = new THREE.Quaternion(), sc = new THREE.Vector3(), ps = new THREE.Vector3();
function buildCity(of) {
  const rows = of.slice(-N), n = rows.length;
  const dmax = Math.max(1, ...rows.map((r) => Math.abs(r.delta)));
  bars.count = n;
  rows.forEach((r, i) => {
    const h = Math.max(0.08, Math.sqrt(Math.abs(r.delta) / dmax) * BAR_H), up = r.delta >= 0;
    ps.set(X(i + N - n), up ? h / 2 : -h / 2, 0); sc.set((SW / N) * 0.62, h, 1.2);
    m4.compose(ps, q, sc); bars.setMatrixAt(i, m4);
    const c = new THREE.Color(up ? COL.green : COL.red).multiplyScalar(i === n - 1 ? 1.6 : 0.35 + 0.5 * (i / n));
    bars.setColorAt(i, c);
  });
  bars.instanceMatrix.needsUpdate = true;
  if (bars.instanceColor) bars.instanceColor.needsUpdate = true;
  if (ribbon) { city.remove(ribbon); ribbon.geometry.dispose(); ribbon.material.dispose(); ribbon = null; }
  const pts = rows.map((r, i) => ({ i, v: r.cvd })).filter((p) => p.v !== null);
  if (pts.length > 2) {
    const lo = Math.min(...pts.map((p) => p.v)), hi = Math.max(...pts.map((p) => p.v)), span = Math.max(1, hi - lo);
    const curve = new THREE.CatmullRomCurve3(pts.map((p) => new THREE.Vector3(X(p.i + N - n), BAR_H + 1.5 + ((p.v - lo) / span) * 4, -2)));
    ribbon = new THREE.Mesh(new THREE.TubeGeometry(curve, Math.min(400, pts.length * 4), 0.09, 8, false),
      new THREE.MeshBasicMaterial({ color: COL.cyan, toneMapped: false }));
    city.add(ribbon);
  }
}

function buildReactor(st) {
  const a = st && st.compte, lim = st && st.limites;
  const text = document.getElementById("reactor-text");
  for (const m of [ringFill, ftmoFill]) if (m) { reactor.remove(m); m.geometry.dispose(); m.material.dispose(); }
  ringFill = ftmoFill = null;
  if (!a || !lim) { risk = 0; coreColor.set(COL.cyan); text.textContent = "compte indisponible"; return; }
  const loss = a.perte_jour;                         // < 0 = gain
  risk = Math.min(1, Math.max(0, loss / lim.arret_interne));
  const ftmo = Math.min(1, Math.max(0, loss / lim.ftmo_jour));
  coreColor = loss < 0 ? new THREE.Color(COL.gold) : riskColor(risk);
  if (risk > 0) { ringFill = arc(4.2, 0.22, risk, riskColor(risk)); reactor.add(ringFill); }
  if (ftmo > 0) { ftmoFill = arc(5.4, 0.08, ftmo, COL.red); reactor.add(ftmoFill); }
  const res = -loss;
  text.textContent = `résultat du jour ${res > 0 ? "+" : ""}${res.toFixed(2)} $ · limite interne utilisée ${Math.round(risk * 100)} %`
    + (risk >= 1 ? " · STOP pour aujourd'hui" : "");
}

let active = false, visible = true;
async function refresh() {
  if (!active || !visible) return;
  try {
    const [of, st] = await Promise.all([fetch("/api/orderflow?tf=1").then((r) => r.json()),
      fetch("/api/state").then((r) => r.json())]);
    if (Array.isArray(of) && of.length) buildCity(of);
    buildReactor(st);
  } catch (e) { /* le prochain tour réessaie */ }
}

function loop(t) {
  if (!active || !visible) return;
  requestAnimationFrame(loop);
  const s = t / 1000, speed = 1.2 + 4 * risk;
  core.rotation.y = s * 0.6; core.rotation.x = s * 0.25;
  const pulse = 1 + 0.12 * Math.sin(s * speed * Math.PI);
  core.scale.setScalar(pulse); inner.scale.setScalar(pulse * 0.95);
  coreMat.color.copy(coreColor); innerMat.color.copy(coreColor);
  reactor.rotation.y = Math.sin(s * 0.3) * 0.35;
  camera.position.x = view.x + Math.sin(s * 0.12) * 3;     // léger mouvement de caméra
  camera.lookAt(view.x, view.y, 0);
  composer.render();
}

function update(newActive, newVisible) {
  const was = active && visible;
  active = newActive; visible = newVisible;
  if (active && visible && !was) { layout(); refresh(); requestAnimationFrame(loop); }
}
new IntersectionObserver((e) => update(active, e[0].isIntersecting)).observe(box);
document.addEventListener("tab", (e) => update(e.detail === "live", visible));
update(!document.getElementById("tab-live").classList.contains("hidden"), true);
setInterval(refresh, 5000);
