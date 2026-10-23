"""Moteur de stratégie : traite les bougies XAUUSD M1 une par une, dans l'ordre.

À la bougie j (début s, clôture s + 1 min), dans cet ordre :
  1. ordre en attente (décidé à la clôture précédente) → exécution à l'OUVERTURE de j,
     après revérification de tous les garde-fous au prix exécutable ;
  2. gestion de la position sur j (clôtures forcées à l'ouverture, SL/TP, limites d'equity) ;
  3. zones : premier contact pendant j, puis à la clôture : invalidations, confirmations ;
  4. si s + 1 min est une clôture M5 : nouvelles zones, scénarios, ATR.
Le moteur ne lit jamais une donnée postérieure à l'instant de décision ; le test
« données tronquées » le vérifie (mêmes décisions avec ou sans le futur).

Convention de remplissage (bougies M1 bid + spread, pas de ticks) :
- achat au ask = bid + spread, vente au bid ; slippage `costs.slippage_price` sur
  les ordres au marché et les stops, pas sur le TP (ordre limite) ;
- si SL et TP sont atteints dans la même bougie M1 : on compte le SL (conservateur) ;
- si la bougie ouvre au-delà du SL : sortie au prix d'ouverture (gap).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from xauof.indicators import orderflow
from xauof.indicators.smc import atr as smc_atr
from xauof.news.calendar import Calendar
from xauof.strategy import context as ctx
from xauof.strategy.instrument import Instrument, size_position
from xauof.strategy.risk import Account, spread_blocked
from xauof.strategy.scenarios import ScenarioTracker
from xauof.strategy.zones import ZoneBook, ZoneDetector

VARIANTS = {
    "A": {"score": False, "trigger": "price"},
    "B": {"score": True, "trigger": "price"},
    "C": {"score": True, "trigger": "delta"},
    "C_ABS": {"score": True, "trigger": "absorption"},
}


@dataclass
class DayData:
    """Tout ce que le moteur peut lire pour une séance (index UTC, bougies = début de minute)."""
    day: pd.Timestamp                    # journée de trading (date New York)
    xau: pd.DataFrame                    # M1 bid : open, high, low, close, spread (points)
    gc: pd.DataFrame                     # M1 GC : open..close, volume, buy, sell, delta, instrument_id
    basis: pd.DataFrame                  # bridge : basis, basis_ref, bruit, contract (minutes alignées)
    levels: Optional[ctx.SessionLevels]
    levels_reason: str = ""


@dataclass
class Position:
    zone_id: str
    scenario: str
    direction: int
    lots: float
    entry: float
    sl: float
    tp: float
    opened_at: pd.Timestamp
    risk_usd: float
    rr_net: float
    target_name: str
    exit: Optional[float] = None
    closed_at: Optional[pd.Timestamp] = None
    exit_reason: str = ""
    pnl: float = 0.0
    commission: float = 0.0
    best: Optional[float] = None      # prix de sortie le plus favorable atteint (MFE)
    worst: Optional[float] = None     # prix de sortie le plus défavorable atteint (MAE)

    def excursion_r(self, price: Optional[float]) -> Optional[float]:
        """Excursion en R (distance entrée → SL = 1 R)."""
        if price is None:
            return None
        return self.direction * (price - self.entry) / abs(self.entry - self.sl)


@dataclass
class Pending:
    zone: object
    scenario: str
    decided_at: pd.Timestamp
    trigger_info: dict


class SimExecutor:
    """Exécution simulée. `fail_stop=True` simule l'échec de placement du SL (test)."""

    def __init__(self, fail_stop: bool = False):
        self.fail_stop = fail_stop

    def place_stop(self, position: Position) -> bool:
        return not self.fail_stop


@dataclass
class Engine:
    cfg: dict
    variant: str
    inst: Instrument
    account: Account
    calendar: Calendar
    executor: SimExecutor = field(default_factory=SimExecutor)
    journal: list = field(default_factory=list)
    trades: list = field(default_factory=list)

    def __post_init__(self):
        if self.variant not in VARIANTS:
            raise ValueError(f"variante inconnue {self.variant} (A, B, C, C_ABS)")
        if self.cfg["strategy"]["london_enabled"]:
            raise ValueError("strategy.london_enabled : session de Londres non implémentée en V1")
        if self.variant == "C_ABS" and not self.cfg["strategy"]["trigger"]["absorption_enabled"]:
            raise ValueError("variante C_ABS : activer strategy.trigger.absorption_enabled")
        self.v = VARIANTS[self.variant]
        self.s = self.cfg["strategy"]
        self.tz = self.s["timezone"]

    # ------------------------------------------------------------------ journal
    def log(self, t, event_type: str, /, **fields) -> None:
        self.journal.append({"t": str(t), "type": event_type, "variant": self.variant} | fields)

    def _ny(self, day: pd.Timestamp, hhmm: str) -> pd.Timestamp:
        return (pd.Timestamp(day.date()) + pd.Timedelta(hhmm + ":00")).tz_localize(self.tz).tz_convert("UTC")

    # ------------------------------------------------------------------ séance
    def run_day(self, d: DayData) -> None:
        s = self.s
        self.d = d
        if d.xau.empty:
            self.log(d.day, "jour_bloque", motif="aucune donnée XAUUSD pour cette séance")
            return
        self.t_start = self._ny(d.day, s["entry_start"])
        self.t_end = self._ny(d.day, s["entry_end"])
        self.t_flat = self._ny(d.day, s["flat_time"])
        self.ib_end = self.t_start + pd.Timedelta(minutes=self.cfg["sessions"]["initial_balance_min"])
        self.tf = pd.Timedelta(s["zone_tf"])
        self.book = ZoneBook()
        self.detector = ZoneDetector(self.cfg, self.t_start, f"{d.day:%Y-%m-%d}")
        self.position: Optional[Position] = None
        self.pending: Optional[Pending] = None
        self.confirming: dict[str, dict] = {}
        self.entries_today = 0
        self.atr_now = np.nan
        self.ib = None
        self.expired = False
        self.xau_m5 = d.xau[["open", "high", "low", "close"]].resample(self.tf).agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
        self.gc_vol_m5 = d.gc["volume"].resample(self.tf).sum()
        self.absorb = None
        if self.v["trigger"] == "absorption":
            ab = self.cfg["orderflow"]["absorption"]
            self.absorb = orderflow.absorption(d.gc, ab["lookback"], ab["vol_mult"], ab["delta_frac"], ab["max_move_atr"])
        self.scen = None
        if d.levels is not None:
            val, _, vah = d.levels.value_area(s["value_area_source"])
            self.scen = ScenarioTracker(self.cfg, val, vah, self.t_start)
        else:
            self.log(self.t_start, "jour_bloque", motif=f"niveaux de la séance précédente indisponibles : {d.levels_reason}")
        self.cal_ok = self.calendar.covers(d.xau.index[0], self.t_flat)
        if not self.cal_ok:
            mode = self.cfg["news"]["missing_calendar"]
            self.log(self.t_start, "calendrier_absent", mode=mode,
                     motif="calendrier économique indisponible pour cette journée")
        self.day_meta = {"levels": None if d.levels is None else d.levels.as_dict()}

        idx = d.xau.index
        for j in range(len(idx)):
            self.step(j)
        if self.position is not None:            # plus de données : sortie au dernier prix connu
            last = d.xau.iloc[-1]
            self._close(idx[-1] + pd.Timedelta(minutes=1), self._exit_price(self.position.direction, last["close"],
                                                                           last["spread"]), "fin des données")
        for log_t, msg in (self.scen.log if self.scen else []):
            self.log(log_t, "scenario", message=msg)
        for z in self.book.zones:
            self.log(z.active_from, "zone", zone_id=z.id, kind=z.kind, direction=z.direction,
                     low=z.low, high=z.high, state=z.state,
                     history=[(str(t), st, r) for t, st, r in z.history], meta=z.meta)

    def step(self, j: int) -> None:
        d, inst = self.d, self.inst
        bar = d.xau.iloc[j]
        s = d.xau.index[j]
        close_t = s + pd.Timedelta(minutes=1)
        self.account.roll(s)

        if self.pending is not None:
            self._try_entry(j, s, bar)
        if self.position is not None:
            self._manage(j, s, bar)

        if self.t_start <= s < self.t_end and not self.expired:
            ask_high = bar["high"] + bar["spread"] * inst.point
            for z in self.book.contacts(s, bar["low"], ask_high):
                self._on_contact(z, s, j)
            for z in self.book.invalidate(close_t, bar["close"]):
                self.confirming.pop(z.id, None)
                if self.pending is not None and self.pending.zone is z:
                    self.pending = None
            self._confirmations(j, close_t)

        if close_t >= self.t_end and not self.expired:
            self.expired = True
            self.book.expire(close_t, "fin de la fenêtre d'entrée")
            for zid in list(self.confirming):
                self.confirming.pop(zid)
            if self.pending is not None:
                self.pending.zone.set(close_t, "consumed", "fin de la fenêtre d'entrée")
                self.log(close_t, "refus", zone_id=self.pending.zone.id, motif="fin de la fenêtre d'entrée")
                self.pending = None

        if close_t.value % self.tf.value == 0:
            self._on_m5_close(close_t, bar)

    # ------------------------------------------------------------------ M5
    def _on_m5_close(self, close_t: pd.Timestamp, bar) -> None:
        m5 = self.xau_m5[self.xau_m5.index + self.tf <= close_t]
        if m5.empty or m5.index[-1] + self.tf != close_t:
            return
        a = smc_atr(m5, self.s["atr_period"])
        self.atr_now = float(a.iloc[-1]) if len(m5) >= self.s["atr_period"] else np.nan
        spread_price = bar["spread"] * self.inst.point
        for z in self.detector.on_m5_close(m5, spread_price):
            self.book.add(z)
            if self.expired and z.state == "eligible":
                z.set(close_t, "expired", "détectée après la fin de la fenêtre d'entrée")
        if self.scen is not None:
            self.scen.on_m5_close(m5, self.gc_vol_m5)
        if self.ib is None and close_t >= self.ib_end:
            self.ib = ctx.ib_from_gc(self.d.gc, self.t_start, self.ib_end)
            if self.ib:
                self.log(close_t, "ib", high_gc=self.ib["high"], low_gc=self.ib["low"], contract=self.ib["contract"])

    # ------------------------------------------------------------------ garde-fous
    def _basis_now(self, t: pd.Timestamp) -> tuple[Optional[dict], str]:
        """Dernière minute alignée CLÔTURÉE avant t (index <= t - 1 min)."""
        b = self.d.basis
        k = b.index.searchsorted(t - pd.Timedelta(minutes=1), side="right") - 1
        if k < 0:
            return None, "aucune minute GC/XAUUSD alignée"
        row = b.iloc[k]
        age = (t - (b.index[k] + pd.Timedelta(minutes=1))) / pd.Timedelta(minutes=1)
        g = self.s["data_guards"]
        if age > g["max_gc_staleness_min"]:
            return None, f"données GC/XAUUSD périmées ({age:.0f} min)"
        if np.isnan(row["basis_ref"]):
            return None, "basis de référence indisponible (roulement récent ou historique court)"
        if np.isnan(row["bruit"]) or row["bruit"] > self.cfg["bridge"]["max_noise"]:
            return None, f"basis instable (bruit {row['bruit']:.2f} $)"
        return {"basis": float(row["basis_ref"]), "noise": float(row["bruit"]),
                "contract": int(row["contract"])}, ""

    def _guards(self, t: pd.Timestamp, spread_pts: float) -> list[str]:
        """Motifs empêchant une nouvelle entrée à l'instant t (liste vide = autorisé)."""
        a, n, r = self.account, self.cfg["news"], self.cfg["risk"]
        why = []
        if a.total_locked:
            why.append(f"verrouillage total : {a.lock_reason}")
        if a.day_locked:
            why.append(f"verrouillage journalier : {a.lock_reason}")
        if not (self.t_start <= t <= self.t_end):
            why.append("hors fenêtre d'entrée")
        if self.position is not None:
            why.append("une position est déjà ouverte")
        if self.entries_today >= r["max_entries_per_day"]:
            why.append(f"{self.entries_today} entrées déjà exécutées aujourd'hui")
        if self.d.levels is None:
            why.append(f"niveaux GC indisponibles : {self.d.levels_reason}")
        if not self.cal_ok and n["missing_calendar"] != "ignore_backtest_only":
            why.append("calendrier économique indisponible")
        if self.cal_ok:
            bo = self.calendar.blackout(t, n["blackout_before_min"], n["blackout_after_min"], n["currencies"])
            if bo:
                why.append(f"fenêtre news : {bo}")
        sb = spread_blocked(self.d.xau["spread"], t, spread_pts, r["spread_filter_window_min"],
                            r["spread_filter_min_obs"], r["spread_filter_mult"])
        if sb:
            why.append(sb)
        _, reason = self._basis_now(t)
        if reason:
            why.append(reason)
        gc_before = self.d.gc.index[self.d.gc.index < t]
        if len(gc_before) == 0 or (t - gc_before[-1]) / pd.Timedelta(minutes=1) - 1 > self.s["data_guards"]["max_gc_staleness_min"]:
            why.append("flux GC périmé ou absent")
        if np.isnan(self.atr_now):
            why.append("ATR M5 indisponible")
        return why

    def _data_stamp(self, t: pd.Timestamp) -> dict:
        """Dernières données réellement disponibles à l'instant t (pour le journal)."""
        g = self.d.gc.index[self.d.gc.index < t]
        b = self.d.basis.index[self.d.basis.index < t]
        x = self.d.xau.index[self.d.xau.index < t]
        return {"decision": str(t), "derniere_gc": str(g[-1]) if len(g) else None,
                "derniere_xau": str(x[-1]) if len(x) else None,
                "derniere_minute_alignee": str(b[-1]) if len(b) else None}

    def _ib_xau(self, t: pd.Timestamp, basis: Optional[dict]) -> Optional[tuple[float, float]]:
        if self.ib is None or t < self.ib_end or basis is None or basis["contract"] != self.ib["contract"]:
            return None
        return (self.ib["low"] - basis["basis"], self.ib["high"] - basis["basis"])

    # ------------------------------------------------------------------ zones
    def _on_contact(self, z, s: pd.Timestamp, j: int) -> None:
        lv, tol = self.d.levels, self.s["near_atr"] * self.atr_now
        info = {"zone_id": z.id, "kind": z.kind, "direction": z.direction, "low": z.low, "high": z.high}
        why = self._guards(s, float(self.d.xau["spread"].iloc[j]))
        if self.pending is not None and "une position est déjà ouverte" not in why:
            why.append("un ordre est déjà en attente")
        scenario, actifs = None, []
        if lv is not None and self.scen is not None and not np.isnan(tol):
            actifs = self.scen.active(s)
            for st in actifs:
                if st.direction != z.direction:
                    continue
                if (st.name == "breakout" and self.s["breakout"]["require_bound_retest"]
                        and z.distance(st.bound) > tol):
                    continue
                scenario = st
                break
            if scenario is None:
                if not actifs:
                    why.append("aucun scénario compatible : aucun scénario actif")
                elif all(st.direction != z.direction for st in actifs):
                    why.append("aucun scénario compatible : scénario actif dans l'autre sens")
                else:
                    why.append("aucun scénario compatible : cassure active mais zone loin de la borne cassée")
        basis, _ = self._basis_now(s)
        sc = ctx.score(z, lv, self._ib_xau(s, basis), tol) if lv is not None and not np.isnan(tol) else None
        if self.v["score"] and sc is not None and sc["score"] < self.s["min_score_families"]:
            why.append(f"score {sc['score']}/3 < {self.s['min_score_families']} familles")
        info |= {"scenarios_actifs": [{"nom": st.name, "sens": st.direction, "borne": st.bound,
                                       "distance_zone": z.distance(st.bound)} for st in actifs],
                 "scenario": scenario.name if scenario else None,
                 "scenario_info": scenario.info if scenario else None,
                 "score": sc, "atr_m5": self.atr_now, "tol_proche": tol,
                 "basis": basis, "ib_xau": self._ib_xau(s, basis), "donnees": self._data_stamp(s)}
        if why:
            z.set(s, "consumed", " ; ".join(why))
            self.log(s, "contact_refuse", motifs=why, **info)
            return
        z.set(s, "confirming", f"scénario {scenario.name}")
        self.confirming[z.id] = {"zone": z, "contact_j": j, "scenario": scenario.name, "info": info}
        self.log(s, "contact_eligible", **info)

    def _confirmations(self, j: int, close_t: pd.Timestamp) -> None:
        d = self.d
        for zid, c in list(self.confirming.items()):
            z = c["zone"]
            k = j
            if k <= c["contact_j"]:
                continue                                   # la bougie du contact ne confirme pas
            cur, prev = d.xau.iloc[k], d.xau.iloc[k - 1]
            price_ok = cur["close"] > prev["high"] if z.direction > 0 else cur["close"] < prev["low"]
            t_cur, t_prev = d.xau.index[k], d.xau.index[k - 1]
            trig = {"close_m1": float(cur["close"]), "prev_high": float(prev["high"]),
                    "prev_low": float(prev["low"]), "reaction_prix": bool(price_ok)}
            flow_ok = True
            if self.v["trigger"] == "delta":
                share = ctx.unclassified_share(d.gc, close_t)
                dp, dc = d.gc["delta"].get(t_prev), d.gc["delta"].get(t_cur)
                trig |= {"delta_prec": None if dp is None else float(dp),
                         "delta_cour": None if dc is None else float(dc), "part_non_classee": share}
                if np.isnan(share) or share > self.s["data_guards"]["max_unclassified_share"]:
                    z.set(close_t, "consumed", "delta indisponible (côté agresseur manquant)")
                    self.log(close_t, "refus", zone_id=zid, motif="delta GC indisponible", trigger=trig)
                    self.confirming.pop(zid)
                    continue
                flow_ok = dp is not None and dc is not None and z.direction * dp < 0 and z.direction * dc > 0
            elif self.v["trigger"] == "absorption":
                sig = self.absorb.get(t_cur)
                trig["absorption"] = None if sig is None else int(sig)
                flow_ok = sig is not None and int(sig) == z.direction
            if price_ok and flow_ok and self.pending is None and self.position is None:
                z.set(close_t, "ordered", "confirmation")
                self.pending = Pending(z, c["scenario"], close_t, trig)
                self.confirming.pop(zid)
                self.log(close_t, "confirmation", zone_id=zid, scenario=c["scenario"], trigger=trig)
                continue
            if k - c["contact_j"] >= self.s["trigger"]["max_m1_bars"]:
                z.set(close_t, "consumed", "délai de confirmation expiré")
                self.log(close_t, "refus", zone_id=zid, motif="pas de confirmation en "
                         f"{self.s['trigger']['max_m1_bars']} bougies M1", trigger=trig)
                self.confirming.pop(zid)

    # ------------------------------------------------------------------ entrée
    def _try_entry(self, j: int, s: pd.Timestamp, bar) -> None:
        p, inst, a = self.pending, self.inst, self.account
        self.pending = None
        z = p.zone
        spread_pts = float(bar["spread"])
        why = self._guards(s, spread_pts)
        direction = z.direction
        entry = bar["open"] + spread_pts * inst.point + inst.slippage if direction > 0 else bar["open"] - inst.slippage
        margin = max(spread_pts * inst.point, self.s["sl_margin_atr"] * self.atr_now)
        sl = z.low - margin if direction > 0 else z.high + margin
        if direction * (entry - sl) <= 0:
            why.append("prix exécutable déjà au-delà du SL")
        lv = self.d.levels
        extra = ([ctx.measured_move(direction, lv, self.s["value_area_source"])]
                 if p.scenario == "breakout" and lv is not None else None)
        tgt = ctx.target(direction, entry, lv, extra) if lv is not None else None
        if tgt is None:
            why.append("aucun objectif admissible dans le sens du trade")
        if p.scenario == "value_return" and lv is not None:
            poc = lv.value_area(self.s["value_area_source"])[1]
            if direction * (poc - entry) <= 0:
                why.append("POC précédent pas dans le sens du trade (retour vers la valeur)")
        equity = a.balance
        budget = a.risk_budget(equity, self.cfg["risk"]["risk_per_trade_pct"])
        sz = None
        if tgt is not None and not why:
            sz = size_position(inst, direction, entry, sl, tgt[0], budget)
            if sz.refused:
                why.append(sz.refused)
            else:
                if sz.rr_net < self.s["min_rr_net"]:
                    why.append(f"R:R net {sz.rr_net:.2f} < {self.s['min_rr_net']}")
                if sz.risk_usd > a.remaining_daily(equity):
                    why.append(f"perte au SL {sz.risk_usd:.2f} > budget journalier restant {a.remaining_daily(equity):.2f}")
                if sz.risk_usd > a.remaining_total(equity):
                    why.append(f"perte au SL {sz.risk_usd:.2f} > marge avant plancher {a.remaining_total(equity):.2f}")
        detail = {"zone_id": z.id, "scenario": p.scenario, "direction": direction, "spread_pts": spread_pts,
                  "entree": entry, "sl": sl, "tp": tgt[0] if tgt else None, "objectif": tgt[1] if tgt else None,
                  "lots": sz.lots if sz else None, "risque_usd": sz.risk_usd if sz else None,
                  "rr_net": sz.rr_net if sz else None, "budget_trade": budget,
                  "reste_jour": a.remaining_daily(equity), "reste_total": a.remaining_total(equity),
                  "trigger": p.trigger_info, "decide_a": str(p.decided_at), "donnees": self._data_stamp(s),
                  "basis": self._basis_now(s)[0]} | a.ftmo_status(equity)
        if why:
            z.set(s, "consumed", " ; ".join(why))
            self.log(s, "entree_refusee", motifs=why, **detail)
            return
        pos = Position(z.id, p.scenario, direction, sz.lots, entry, sl, tgt[0], s, sz.risk_usd, sz.rr_net, tgt[1])
        half = inst.commission_rt * sz.lots / 2
        a.balance -= half
        pos.commission = half
        self.position = pos
        self.entries_today += 1
        z.set(s, "position", f"{'achat' if direction > 0 else 'vente'} {sz.lots} lot")
        self.log(s, "entree", **detail)
        if not self.executor.place_stop(pos):
            self.log(s, "echec_sl", zone_id=z.id, motif="placement du SL refusé : clôture immédiate")
            self._close(s, self._exit_price(direction, bar["open"], spread_pts), "échec du placement du SL")
            a.lock(s, "échec du placement du SL")

    # ------------------------------------------------------------------ position
    def _exit_price(self, direction: int, bid: float, spread_pts: float) -> float:
        """Sortie au marché : un achat se clôture au bid, une vente à l'ask ; slippage défavorable."""
        if direction > 0:
            return bid - self.inst.slippage
        return bid + spread_pts * self.inst.point + self.inst.slippage

    def _manage(self, j: int, s: pd.Timestamp, bar) -> None:
        pos, inst, a = self.position, self.inst, self.account
        n = self.cfg["news"]
        sp = float(bar["spread"]) * inst.point
        # clôtures forcées à l'ouverture
        forced = None
        if s >= self.t_flat:
            forced = f"clôture de fin de séance ({self.s['flat_time']})"
        elif self.cal_ok:
            due = self.calendar.flatten_due(s, n["flatten_before_min"], n["flatten_kinds"], n["currencies"])
            if due:
                forced = f"clôture avant annonce : {due}"
        if forced:
            self._close(s, self._exit_price(pos.direction, bar["open"], bar["spread"]), forced)
            return
        o, h, lo = bar["open"], bar["high"], bar["low"]
        # excursions (diagnostic MFE / MAE), aux prix de sortie : bid pour un achat, ask pour une vente
        fav, adv = (h, lo) if pos.direction > 0 else (lo + sp, h + sp)
        pos.best = fav if pos.best is None else (max(pos.best, fav) if pos.direction > 0 else min(pos.best, fav))
        pos.worst = adv if pos.worst is None else (min(pos.worst, adv) if pos.direction > 0 else max(pos.worst, adv))
        if pos.direction > 0:                                 # sorties au bid
            if o <= pos.sl:
                return self._close(s, o - inst.slippage, "SL (ouverture au-delà)")
            if lo <= pos.sl:
                return self._close(s, pos.sl - inst.slippage, "SL")
            if h >= pos.tp:
                return self._close(s, pos.tp, "TP")
            worst = lo
        else:                                                 # sorties à l'ask
            ao, ah, al = o + sp, h + sp, lo + sp
            if ao >= pos.sl:
                return self._close(s, ao + inst.slippage, "SL (ouverture au-delà)")
            if ah >= pos.sl:
                return self._close(s, pos.sl + inst.slippage, "SL")
            if al <= pos.tp:
                return self._close(s, pos.tp, "TP")
            worst = ah
        # contrôle permanent de l'equity (pire prix de la bougie, frais compris)
        eq_worst = a.balance + inst.pnl(pos.direction, pos.entry, worst, pos.lots) - inst.commission_rt * pos.lots / 2
        reason = a.breach(eq_worst)
        if reason:
            total = "plancher" in reason
            limit_eq = a.internal_floor if total else a.day_start_balance - a.daily_stop
            need = limit_eq - a.balance + inst.commission_rt * pos.lots / 2
            px = pos.entry + need / (pos.direction * inst.value_per_price * pos.lots)
            start = o if pos.direction > 0 else o + sp
            px = min(px, start) if pos.direction > 0 else max(px, start)
            self._close(s, px - pos.direction * inst.slippage, reason)
            a.lock(s, reason, total=total)

    def _close(self, t: pd.Timestamp, price: float, reason: str) -> None:
        pos, inst, a = self.position, self.inst, self.account
        half = inst.commission_rt * pos.lots / 2
        gross = inst.pnl(pos.direction, pos.entry, price, pos.lots)
        a.balance += gross - half
        pos.commission += half
        pos.exit, pos.closed_at, pos.exit_reason = price, t, reason
        pos.pnl = gross - pos.commission
        self.position = None
        self.trades.append(pos)
        for z in self.book.zones:
            if z.id == pos.zone_id:
                z.set(t, "closed", reason)
        self.log(t, "sortie", zone_id=pos.zone_id, scenario=pos.scenario, prix=price, motif=reason,
                 pnl=round(pos.pnl, 2), r_multiple=round(pos.pnl / pos.risk_usd, 3) if pos.risk_usd else None,
                 solde=round(a.balance, 2))
        breach = a.breach(a.balance)
        if breach and not (a.day_locked or a.total_locked):
            a.lock(t, breach, total="plancher" in breach)
