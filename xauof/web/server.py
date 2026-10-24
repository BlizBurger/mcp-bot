"""Serveur de l'interface (FastAPI) : API JSON + page web. Lecture seule, aucun ordre.

Écoute par défaut sur 127.0.0.1 (ton PC uniquement). Avec --phone (accès téléphone via
Tailscale), un mot de passe protège toutes les pages et l'API. Mise en ligne publique :
HTTPS en plus (phase 6).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
import pandas as pd
from urllib.parse import parse_qs

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from xauof.assistant.snapshot import ny_time, snapshot
from xauof.indicators.smc import atr as smc_atr
from xauof.news.calendar import Calendar, load_calendar
from xauof.web import journal as jr
from xauof.web.orderflow_proxy import proxy_bars
from xauof.strategy.instrument import load_instrument
from xauof.web.setups import AlertCenter, qualify, trade_plan

STATIC = Path(__file__).parent / "static"
NY = "America/New_York"

EXPLAIN = {
    "POC vol": "Prix le plus échangé hier (GC) : aimant, le marché y revient souvent.",
    "VAH vol": "Haut de la zone de valeur d'hier (70 % du volume) : résistance ou tremplin.",
    "VAL vol": "Bas de la zone de valeur d'hier : support ou tremplin.",
    "POC TPO": "Prix où le marché a passé le plus de TEMPS hier.",
    "VAH TPO": "Haut de la zone de valeur en temps.",
    "VAL TPO": "Bas de la zone de valeur en temps.",
    "LVN": "Peu de volume hier : prix rejeté, le marché le traverse vite ou y rebondit.",
    "HVN": "Beaucoup de volume hier : prix accepté, le marché y ralentit.",
    "single prints": "Zone traversée en une seule période de 30 min : trou à combler ou rejet net.",
}


def chart_time(ts: pd.Timestamp) -> int:
    """Secondes « heure de New York » pour l'axe du graphique (lightweight-charts affiche en UTC)."""
    return int(pd.Timestamp(ts).tz_convert(NY).tz_localize(None).value // 10**9)


def demo_calendar(now: pd.Timestamp) -> Calendar:
    t = now.floor("h") + pd.Timedelta(hours=1)
    ev = pd.DataFrame({"currency": ["USD"], "impact": ["High"], "kind": ["OTHER"], "event": ["Démo : annonce fictive"]},
                      index=pd.DatetimeIndex([t], name="time_utc"))
    return Calendar(ev, [(now - pd.Timedelta(days=2), now + pd.Timedelta(days=2))])


class Hub:
    """État partagé : source, alertes, cache court du dernier état."""

    def __init__(self, cfg: dict, source):
        self.cfg, self.src = cfg, source
        self.alerts = AlertCenter()
        self.inst = load_instrument(cfg)
        self.lock = threading.Lock()
        self._cache = (0.0, None)
        self._cal = (0.0, None)

    def calendar(self, now) -> Calendar:
        if self.src.name.startswith("DÉMO"):
            return demo_calendar(now)
        if time.time() - self._cal[0] > 60:
            self._cal = (time.time(), load_calendar(self.cfg))
        return self._cal[1]

    def day_bars(self, now):
        cfg = self.cfg
        day = now.tz_convert(NY).normalize().tz_localize(None)
        start = ny_time(day, cfg["strategy"]["warmup_start"], NY)
        return day, start, self.src.bars_m1(start, now + pd.Timedelta(minutes=1))

    def state(self) -> dict:
        with self.lock:
            if time.time() - self._cache[0] < 2 and self._cache[1] is not None:
                return self._cache[1]
            st = self._compute()
            self._cache = (time.time(), st)
            return st

    def _compute(self) -> dict:
        cfg, src = self.cfg, self.src
        now = src.now()
        day, start, xau = self.day_bars(now)
        levels = src.levels(day)
        try:
            account = src.account(now)
        except Exception:
            account = None
        snap = snapshot(cfg, xau, levels, self.calendar(now), now, account)
        if "erreur" in snap:
            return {"source": src.name, "maintenant": now.isoformat(), "erreur": snap["erreur"]}
        try:
            tick = src.tick()                      # prix en direct ; la logique reste sur bougies clôturées
        except Exception:
            tick = None
        if tick:
            snap["prix"].update(bid=tick["bid"], ask=tick["ask"],
                                spread_pts=round((tick["ask"] - tick["bid"]) / cfg["broker"]["point"]), direct=True)
        m5 = xau[["open", "high", "low", "close"]].resample("5min").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
        m5 = m5[m5.index + pd.Timedelta(minutes=5) <= now]
        atr = float(smc_atr(m5, cfg["strategy"]["atr_period"]).iloc[-1]) if len(m5) >= cfg["strategy"]["atr_period"] else np.nan
        bid = snap["prix"]["bid"]
        setups = qualify(snap["zones"], levels, bid, atr, cfg["strategy"]["near_atr"])
        equity = account["equity"] if account else cfg["account"]["initial_capital"]
        budget = cfg["risk"]["risk_per_trade_pct"] / 100 * min(equity, cfg["account"]["initial_capital"])
        spread = snap["prix"]["spread_pts"] * cfg["broker"]["point"]
        for st in setups:
            st["plan"] = trade_plan(st["zone"], levels, atr, spread, self.inst, budget)
        t_start = ny_time(day, cfg["strategy"]["entry_start"], NY)
        stopped = bool(snap["compte"] and snap["compte"]["stop_interne_atteint"])
        if t_start <= now <= ny_time(day, cfg["strategy"]["entry_end"], NY) and not stopped:   # ni hors fenêtre, ni après un STOP
            self.alerts.from_setups(setups, now, snap["news_blackout"])

        of_from = t_start if now > t_start else now - pd.Timedelta(hours=1)   # CVD depuis l'ouverture de séance
        of = proxy_bars(src.ticks(of_from, now), cvd_from=t_start)
        last5 = of[of.index >= now - pd.Timedelta(minutes=5)]
        of_sum = {"delta_5min": float(last5["delta"].sum()) if len(last5) else 0.0,
                  "cvd": float(of["cvd"].dropna().iloc[-1]) if of["cvd"].notna().any() else None,
                  "ticks_5min": int(last5["ticks"].sum()) if len(last5) else 0}

        lvls = []
        if levels:
            for name, val in [("POC vol", levels["poc_v"]), ("VAH vol", levels["vah_v"]), ("VAL vol", levels["val_v"]),
                              ("POC TPO", levels["poc_t"]), ("VAH TPO", levels["vah_t"]), ("VAL TPO", levels["val_t"])]:
                lvls.append({"nom": name, "prix": val, "type": name.split()[0], "aide": EXPLAIN[name]})
            lvls += [{"nom": "LVN", "prix": v, "type": "LVN", "aide": EXPLAIN["LVN"]} for v in levels["lvn"]]
            lvls += [{"nom": f"HVN {a:.2f}–{b:.2f}", "prix": (a + b) / 2, "bas": a, "haut": b, "type": "HVN",
                      "aide": EXPLAIN["HVN"]} for a, b in levels["hvn"]]
            lvls += [{"nom": f"Single prints {a:.2f}–{b:.2f}", "prix": (a + b) / 2, "bas": a, "haut": b,
                      "type": "SP", "aide": EXPLAIN["single prints"]} for a, b in levels["singles"]]
            for x in lvls:
                x["distance"] = round(bid - x["prix"], 2)
            lvls.sort(key=lambda x: -x["prix"])

        def zone_json(z):
            return {"id": z.id, "type": z.kind, "sens": z.direction, "bas": z.low, "haut": z.high,
                    "depuis": chart_time(z.active_from), "etat": z.state}

        news = snap["prochaines_news"]
        upcoming = [{"heure": t.tz_convert(NY).strftime("%a %d %H:%M"), "dans_min": int((t - now).total_seconds() // 60),
                     "annonce": e} for t, e in zip(news.index, news["event"])]
        ib = snap["ib"]
        return {
            "source": src.name, "maintenant": now.isoformat(), "heure_ny": snap["heure_ny"],
            "heure_paris": now.tz_convert("Europe/Paris").strftime("%H:%M"),
            "fenetre": snap["fenetre"], "prix": {k: (v.isoformat() if hasattr(v, "isoformat") else v)
                                                 for k, v in snap["prix"].items()},
            "spread_bloque": snap["spread_bloque"], "position_va": snap.get("position_va"),
            "niveau_proche": snap.get("niveau_proche"), "niveaux": lvls,
            "niveaux_source": levels.get("source_day") if levels else None,
            "ib": ib, "zones": [zone_json(z) for z in snap["zones_actives"]],
            "setups": [{"zone": zone_json(s["zone"]), "niveaux": s["niveaux"], "contexte": s["contexte"],
                        "etat": s["etat"], "plan": s["plan"]} for s in setups],
            "orderflow": of_sum, "news": upcoming, "news_blackout": snap["news_blackout"],
            "news_cloture": snap["news_cloture"], "compte": snap["compte"],
            "limites": {"arret_interne": cfg["risk"]["daily_stop_usd"], "ftmo_jour": cfg["ftmo"]["daily_loss_limit_usd"],
                        "semaine": cfg["risk"]["weekly_stop_usd"], "trades_jour": cfg["risk"]["max_entries_per_day"]},
            "alertes_systeme": snap["alertes"], "alertes": self.alerts.feed[:20], "atr_m5": atr,
        }


COOKIE = "xauof_session"
LOGIN_PAGE = """<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>XAUUSD OF</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#04060c;color:#d8e6ff;
font-family:system-ui,sans-serif}form{display:flex;flex-direction:column;gap:12px;width:min(320px,90vw)}
h1{font-size:20px;letter-spacing:3px;color:#ffcf33;margin:0 0 8px}input,button{font-size:16px;padding:12px;
border-radius:8px;border:1px solid #00e5ff55;background:#0a1020;color:#d8e6ff}button{background:#00e5ff22;
color:#00e5ff;cursor:pointer}p{color:#ff3b6b;margin:0}</style></head><body><form method="post" action="/login">
<h1>XAUUSD·OF</h1>%s<input type="password" name="password" placeholder="mot de passe" autofocus
autocomplete="current-password"><button>Entrer</button></form></body></html>"""


def session_token(password: str) -> str:
    return hmac.new(password.encode(), b"xauof-session-v1", hashlib.sha256).hexdigest()


def create_app(cfg: dict, source, alert_loop: bool = True, password: str | None = None) -> FastAPI:
    hub = Hub(cfg, source)

    @asynccontextmanager
    async def lifespan(app):
        task = None
        if alert_loop:
            async def loop():
                while True:
                    try:
                        await asyncio.to_thread(hub.state)        # alertes même sans page ouverte
                    except Exception:
                        pass
                    await asyncio.sleep(20)
            task = asyncio.create_task(loop())
        yield
        if task:
            task.cancel()

    app = FastAPI(title="XAUUSD OF", lifespan=lifespan)
    app.state.hub = hub

    if password:
        token = session_token(password)

        @app.middleware("http")
        async def require_login(request: Request, call_next):
            if request.url.path == "/login" or hmac.compare_digest(request.cookies.get(COOKIE, ""), token):
                return await call_next(request)
            if request.url.path.startswith("/api/"):
                return JSONResponse({"detail": "connexion requise"}, status_code=401)
            return RedirectResponse("/login", status_code=303)

        @app.get("/login")
        def login_page(e: int = 0):
            return HTMLResponse(LOGIN_PAGE % ("<p>Mot de passe incorrect</p>" if e else ""))

        @app.post("/login")
        async def login(request: Request):
            given = parse_qs((await request.body()).decode()).get("password", [""])[0]
            if not hmac.compare_digest(session_token(given), token):
                await asyncio.sleep(1.0)                       # freine les essais en série
                return RedirectResponse("/login?e=1", status_code=303)
            r = RedirectResponse("/", status_code=303)
            r.set_cookie(COOKIE, token, max_age=30 * 24 * 3600, httponly=True, samesite="strict")
            return r
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/state")
    def state():
        return hub.state()

    @app.get("/api/bars")
    def bars(tf: int = 1):
        now = source.now()
        _, _, xau = hub.day_bars(now)
        if xau.empty:
            return []
        if tf == 5:
            xau = xau[["open", "high", "low", "close"]].resample("5min").agg(
                {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
        return [{"time": chart_time(t), "open": r.open, "high": r.high, "low": r.low, "close": r.close}
                for t, r in xau.iterrows()]

    @app.get("/api/terrain")
    def terrain(step_min: int = 5, price_step: float = 0.5):
        """Paysage 3D de la séance : activité (ticks MT5) par tranche de temps et de prix."""
        now = source.now()
        day, _, _ = hub.day_bars(now)
        t0 = ny_time(day, cfg["strategy"]["entry_start"], NY)
        if now <= t0:
            t0 = now - pd.Timedelta(hours=2)
        ticks = source.ticks(t0, now)
        st = hub.state()
        if ticks.empty or "erreur" in st:
            return {"vide": True}
        mid = (ticks["bid"] + ticks["ask"]) / 2
        lo = float(np.floor(mid.min() / price_step) * price_step) - 2 * price_step
        hi = float(np.ceil(mid.max() / price_step) * price_step) + 2 * price_step
        n_p = int(round((hi - lo) / price_step)) + 1
        t_idx = ((mid.index - t0) // pd.Timedelta(minutes=step_min)).astype(int)
        p_idx = np.clip(np.round((mid.to_numpy() - lo) / price_step).astype(int), 0, n_p - 1)
        n_t = int(t_idx.max()) + 1
        grid = np.zeros((n_t, n_p), dtype=int)
        np.add.at(grid, (np.asarray(t_idx), p_idx), 1)
        def t_of(ts):                                   # position (en tranches) d'un instant
            return float((pd.Timestamp(ts) - t0) / pd.Timedelta(minutes=step_min))
        zones = [{"sens": z["sens"], "bas": z["bas"], "haut": z["haut"],
                  "depuis": max(0.0, (z["depuis"] - chart_time(t0)) / (60 * step_min))} for z in st["zones"]]
        return {"vide": False, "debut": chart_time(t0), "pas_min": step_min, "prix_bas": lo, "pas_prix": price_step,
                "grille": grid.tolist(), "maintenant": t_of(now), "prix": st["prix"]["bid"],
                "niveaux": [{"nom": x["nom"], "prix": x["prix"], "type": x["type"]} for x in st["niveaux"]],
                "ib": st["ib"], "ib_fin": t_of(t0 + pd.Timedelta(minutes=cfg["sessions"]["initial_balance_min"]))
                if now > ny_time(day, cfg["strategy"]["entry_start"], NY) else None, "zones": zones}

    @app.get("/api/orderflow")
    def orderflow(tf: int = 1):
        now = source.now()
        day, start, _ = hub.day_bars(now)
        of = proxy_bars(source.ticks(start, now), freq="5min" if tf == 5 else "1min",
                        cvd_from=ny_time(day, cfg["strategy"]["entry_start"], NY))
        return [{"time": chart_time(t), "delta": r.delta, "ticks": int(r.ticks),
                 "cvd": None if pd.isna(r.cvd) else r.cvd} for t, r in of.iterrows()]

    @app.get("/api/journal")
    def journal():
        trades = jr.load(cfg)
        return {"trades": trades, "stats": jr.stats(trades), "criteres": jr.CRITERIA}

    @app.post("/api/journal/sync")
    def journal_sync():
        now = source.now()
        deals, orders = source.journal_raw(now - pd.Timedelta(days=90), now + pd.Timedelta(minutes=1))
        from xauof.strategy.instrument import load_instrument

        fresh = jr.trades_from_deals(deals, orders, cfg["symbols"]["broker"], load_instrument(cfg).value_per_price,
                                     source.server_to_utc)
        trades = jr.merge(jr.load(cfg), fresh)
        jr.save(cfg, trades)
        return {"importes": len(fresh), "total": len(trades)}

    @app.post("/api/journal/{trade_id}")
    def journal_annotate(trade_id: int, body: dict = Body(...)):
        trades = jr.load(cfg)
        try:
            t = jr.annotate(trades, trade_id, body.get("setup"), body.get("criteres"), body.get("note"))
        except StopIteration:
            raise HTTPException(404, "trade inconnu")
        jr.save(cfg, trades)
        return t

    return app
