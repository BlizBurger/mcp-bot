"""Stratégie « Sweep 4H + BOS M15 » (v2, spec utilisateur).

Séquence :
  1. Niveaux de liquidité H4 (50 bougies) : swings k=3, equal highs/lows
     (tolérance en % du prix), highs/lows de la session précédente.
  2. Sweep H4 : mèche au-delà du niveau (≥ min_sweep_pips), clôture de retour
     à l'intérieur, mèche ≥ 60% du range de la bougie (rejet net).
  3. BOS M15 : dans les `bos_window_m15` bougies suivant la clôture H4 du
     sweep, une clôture M15 (pas une mèche) casse un swing M15 (k=2) dans le
     sens opposé au sweep. Invalidation : clôture M15 au-delà du niveau
     sweepé (dans le sens du sweep) avant le BOS.
  4. Confluences bonus (score 0-6) dans la jambe du BOS : FVG non comblé,
     IFVG, Order Block, Breaker Block, zone d'équilibre (discount/premium),
     zone Fib 61.8-79% (OTE).
  5. Entrée : ordre limite sur la confluence la plus proche du point de BOS
     (FVG/OB en priorité), sinon retest du swing cassé.
  6. SL : au-delà de l'extrême de la mèche du sweep + buffer en % du prix.
  7. TP : prochain niveau de liquidité opposé s'il offre ≥ min_rr, sinon
     min_rr fixe.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from smc.core import Setup, Sweep, Zone, atr, calendar_blocked, find_fvgs, \
    find_order_blocks, in_window, swing_points


# ---------------------------------------------------------------------------
# 1. Niveaux de liquidité H4
# ---------------------------------------------------------------------------

def h4_liquidity_levels(h4: pd.DataFrame, m15: Optional[pd.DataFrame],
                        cfg_s: dict, sessions: dict,
                        ref_price: float) -> dict[str, list[float]]:
    """Niveaux au-dessus / en-dessous du prix, triés du plus proche au plus
    loin, limités à `max_levels_per_side` de chaque côté."""
    levels: list[float] = []
    k = cfg_s.get("swing_k_h4", 3)

    # Swings H4 significatifs (k bougies de chaque côté)
    sh, sl = swing_points(h4, k=k)
    levels += [float(h4["high"].iloc[i]) for i in sh]
    levels += [float(h4["low"].iloc[i]) for i in sl]

    # Equal highs / lows (tolérance relative en % du prix)
    tol = ref_price * cfg_s.get("equal_tolerance_pct", 0.05) / 100.0
    for vals, pick in ((sorted(float(h4["high"].iloc[i]) for i in sh), max),
                       (sorted(float(h4["low"].iloc[i]) for i in sl), min)):
        grp: list[float] = []
        for v in vals:
            if grp and abs(v - grp[-1]) > tol:
                if len(grp) >= 2:
                    levels.append(pick(grp))
                grp = []
            grp.append(v)
        if len(grp) >= 2:
            levels.append(pick(grp))

    # Highs / lows de la session précédente (asian / london / ny) sur M15
    if m15 is not None and len(m15):
        last_day = m15["time"].iloc[-1].date()
        windows = {"asian": sessions.get("asian")}
        windows.update(sessions.get("killzones", {}))
        for win in windows.values():
            if not win:
                continue
            for day_offset in (0, 1):  # session du jour si finie, sinon la veille
                day = last_day - timedelta(days=day_offset)
                mask = (m15["time"].dt.date == day) & \
                    m15["time"].apply(lambda t: in_window(t, win))
                sub = m15[mask]
                if not sub.empty:
                    levels += [float(sub["high"].max()), float(sub["low"].min())]
                    break

    n = cfg_s.get("max_levels_per_side", 5)
    above = sorted({lv for lv in levels if lv > ref_price})[:n]
    below = sorted({lv for lv in levels if lv < ref_price}, reverse=True)[:n]
    return {"above": above, "below": below}


# ---------------------------------------------------------------------------
# 2. Sweep H4 avec rejet net
# ---------------------------------------------------------------------------

def is_h4_sweep(candle: pd.Series, level: float, side: str,
                min_depth: float, min_wick_ratio: float) -> bool:
    """Mèche au-delà du niveau (≥ min_depth), clôture de retour à l'intérieur,
    mèche de rejet ≥ min_wick_ratio du range total."""
    rng = float(candle["high"] - candle["low"])
    if rng <= 0:
        return False
    body_low = min(candle["open"], candle["close"])
    body_high = max(candle["open"], candle["close"])
    if side == "low":
        return (level - candle["low"]) >= min_depth and \
            candle["close"] > level and \
            (body_low - candle["low"]) / rng >= min_wick_ratio
    return (candle["high"] - level) >= min_depth and \
        candle["close"] < level and \
        (candle["high"] - body_high) / rng >= min_wick_ratio


# ---------------------------------------------------------------------------
# 4. Confluences (score 0-6)
# ---------------------------------------------------------------------------

def _confluences(leg: pd.DataFrame, direction: str, pre_leg: pd.DataFrame,
                 min_size: float = 0.0) -> tuple[dict, list[tuple[str, Zone]]]:
    """Confluences dans la jambe du BOS. Retourne (drapeaux, zones candidates
    pour l'entrée). `pre_leg` = bougies avant la jambe (pour les breakers).
    `min_size` = taille minimale (en prix) d'un FVG/IFVG pour compter — durci
    pour que le score discrimine au lieu de saturer."""
    is_long = direction == "long"
    with_bias = "bullish" if is_long else "bearish"
    against = "bearish" if is_long else "bullish"
    flags = {"fvg": False, "ifvg": False, "ob": False, "breaker": False}
    zones: list[tuple[str, Zone]] = []

    lows, highs = leg["low"].values, leg["high"].values
    closes = leg["close"].values

    # FVG non comblé dans la jambe (taille >= min_size)
    for z in find_fvgs(leg, min_size=min_size):
        if z.direction != with_bias:
            continue
        after = lows[z.index:] if is_long else highs[z.index:]
        filled = (after.min() < z.bottom) if is_long else (after.max() > z.top)
        if not filled:
            flags["fvg"] = True
            zones.append(("FVG", z))

    # IFVG : FVG opposé (taille >= min_size) traversé en clôture (il s'inverse)
    for z in find_fvgs(leg, min_size=min_size):
        if z.direction != against:
            continue
        broken = (closes[z.index:].max() > z.top) if is_long \
            else (closes[z.index:].min() < z.bottom)
        if broken:
            flags["ifvg"] = True
            zones.append(("IFVG", z))

    # Order Block : dernière bougie opposée avant l'impulsion du BOS —
    # compté seulement s'il n'a pas déjà été violé (clôture au travers)
    for z in reversed(find_order_blocks(leg, lookback=5)):
        if z.direction != with_bias:
            continue
        violated = (closes[z.index:].min() < z.bottom) if is_long \
            else (closes[z.index:].max() > z.top)
        if not violated:
            flags["ob"] = True
            zones.append(("OB", z))
        break  # on ne considère que l'OB le plus récent

    # Breaker Block : OB opposé (avant la jambe) traversé par le BOS
    if len(pre_leg) >= 6:
        last_close = closes[-1]
        for z in find_order_blocks(pre_leg, lookback=5):
            if z.direction != against:
                continue
            if (is_long and last_close > z.top) or \
               (not is_long and last_close < z.bottom):
                flags["breaker"] = True
                zones.append(("Breaker", Zone("Breaker", with_bias,
                                              top=z.top, bottom=z.bottom,
                                              index=z.index)))
                break
    return flags, zones


# ---------------------------------------------------------------------------
# Détection principale
# ---------------------------------------------------------------------------

def find_setup(pair: str, data: dict, cfg: dict, pip: float,
               now: Optional[datetime] = None) -> Optional[Setup]:
    s = cfg["sweep_bos"]
    h4: pd.DataFrame = data["htf"]
    m15: pd.DataFrame = data["ltf"]
    # minimum d'historique : assez de H4 pour des swings k=3 + 20 de contexte
    # (liquidity_lookback_h4 est un plafond, pas un plancher)
    if len(h4) < 30 or len(m15) < 60:
        return None
    now = now or m15["time"].iloc[-1]
    if calendar_blocked(now, cfg.get("calendar")):
        return None

    window = s.get("bos_window_m15", 20)
    price = float(m15["close"].iloc[-1])

    # --- 1+2. Chercher la bougie H4 de sweep la plus récente encore active ---
    h4_done = h4[h4["time"] + pd.Timedelta(hours=4) <= now]  # bougies H4 closes
    sweep: Optional[Sweep] = None
    sweep_close_time = None
    for idx in range(len(h4_done) - 1, max(len(h4_done) - 12, 0), -1):
        candle = h4_done.iloc[idx]
        close_time = candle["time"] + pd.Timedelta(hours=4)
        # fenêtre BOS : now doit être dans les `window` bougies M15 après la clôture
        if close_time > now or now > close_time + pd.Timedelta(minutes=15 * window):
            continue
        hist = h4_done.iloc[:idx].tail(s.get("liquidity_lookback_h4", 50))
        if len(hist) < 20:
            continue
        m15_hist = m15[m15["time"] < candle["time"]]
        lv = h4_liquidity_levels(hist, m15_hist, s, cfg["sessions"],
                                 ref_price=float(candle["close"]))
        min_depth = s.get("min_sweep_pips", 1.0) * pip
        wick = s.get("min_wick_ratio", 0.6)
        # si la mèche balaie plusieurs niveaux, retenir le plus profond
        # (le plus significatif — c'est lui qui sert de référence d'invalidation)
        low_hits = [lv_ for lv_ in lv["below"]
                    if is_h4_sweep(candle, lv_, "low", min_depth, wick)]
        high_hits = [lv_ for lv_ in lv["above"]
                     if is_h4_sweep(candle, lv_, "high", min_depth, wick)]
        if low_hits:
            sweep = Sweep(index=idx, side="low", level=min(low_hits),
                          extreme=float(candle["low"]), level_kind="h4")
            sweep_close_time = close_time
            break
        if high_hits:
            sweep = Sweep(index=idx, side="high", level=max(high_hits),
                          extreme=float(candle["high"]), level_kind="h4")
            sweep_close_time = close_time
            break
    if sweep is None:
        return None
    direction = "long" if sweep.side == "low" else "short"
    is_long = direction == "long"

    # --- 3. BOS M15 : clôture au-delà d'un swing M15, maintenant précisément --
    after = m15[m15["time"] > sweep_close_time]
    if after.empty or len(after) > window:
        return None
    # Invalidation : clôture M15 au-delà du niveau sweepé (sens du sweep)
    pre_bos = after.iloc[:-1]
    if len(pre_bos):
        if is_long and pre_bos["close"].min() < sweep.level:
            return None
        if not is_long and pre_bos["close"].max() > sweep.level:
            return None
    last_close = float(m15["close"].iloc[-1])
    prev_close = float(m15["close"].iloc[-2])
    k = s.get("bos_swing_k", 2)
    sh, sl = swing_points(m15.iloc[:-1], k=k)  # pivots confirmés avant la bougie courante
    sweep_open_time = h4_done.iloc[sweep.index]["time"]
    bos_level = None
    pivots = sh if is_long else sl
    for i in reversed(pivots):
        if m15["time"].iloc[i] < sweep_open_time:
            break  # on ne casse que la structure formée depuis le sweep
        lvl = float(m15["high"].iloc[i]) if is_long else float(m15["low"].iloc[i])
        broke = (prev_close <= lvl < last_close) if is_long \
            else (prev_close >= lvl > last_close)
        if broke:
            bos_level = lvl
            break
    if bos_level is None:
        return None

    # --- 4. Confluences dans la jambe du BOS ---------------------------------
    leg_start = m15.index[m15["time"] >= sweep_open_time][0]
    leg = m15.loc[leg_start:].reset_index(drop=True)
    pre_leg = m15.loc[:leg_start].tail(40).reset_index(drop=True)
    # taille minimale des FVG/IFVG en fraction d'ATR M15 (anti-saturation du score)
    atr_val = float(atr(m15, 14).iloc[-1] or 0.0)
    min_size = atr_val * s.get("confluence_min_size_atr", 0.5)
    flags, zones = _confluences(leg, direction, pre_leg, min_size=min_size)

    # --- 5. Entrée : confluence la plus proche du BOS, sinon retest du BOS ---
    prio = {"FVG": 0, "OB": 0, "IFVG": 1, "Breaker": 1}
    allowed = set(s.get("entry_zone_kinds", ["FVG", "OB", "IFVG", "Breaker"]))
    usable = []
    for kind, z in zones:
        if kind not in allowed:
            continue
        edge = z.top if is_long else z.bottom      # bord proximal (prix au-dessus/en-dessous)
        if (is_long and edge < last_close) or (not is_long and edge > last_close):
            usable.append((prio[kind], abs(edge - bos_level), kind, z, edge))
    if usable:
        usable.sort(key=lambda x: (x[0], x[1]))
        _, _, entry_kind, zone, entry = usable[0]
    else:
        entry_kind, entry = "retest BOS", bos_level
        zone = Zone("BOS", "bullish" if is_long else "bearish",
                    top=bos_level, bottom=bos_level, index=0)

    # Zone d'équilibre + OTE (dépendent du prix d'entrée)
    leg_low = min(float(leg["low"].min()), sweep.extreme) if is_long else float(leg["low"].min())
    leg_high = float(leg["high"].max()) if is_long else max(float(leg["high"].max()), sweep.extreme)
    rng = leg_high - leg_low
    if rng > 0:
        retr = (leg_high - entry) / rng if is_long else (entry - leg_low) / rng
        flags["equilibrium"] = retr >= 0.5
        flags["ote"] = 0.618 <= retr <= 0.79
    else:
        flags["equilibrium"] = flags["ote"] = False
    score = sum(1 for v in flags.values() if v)
    if score < s.get("min_score", 0):
        return None

    # --- 6+7. SL au-delà de la mèche du sweep + buffer % ; TP liquidité/minRR
    buffer = entry * s.get("sl_buffer_pct", 0.12) / 100.0
    sl_price = sweep.extreme - buffer if is_long else sweep.extreme + buffer
    risk = (entry - sl_price) if is_long else (sl_price - entry)
    if risk <= 0 or risk < cfg["strategy"].get("min_risk_pips", 0.0) * pip:
        return None
    min_rr = s.get("min_rr", 2.0)
    lv_now = h4_liquidity_levels(h4_done.tail(s.get("liquidity_lookback_h4", 50)),
                                 m15, s, cfg["sessions"], ref_price=entry)
    targets = lv_now["above"] if is_long else lv_now["below"]
    tp = None
    for t in targets:
        if abs(t - entry) >= min_rr * risk:
            tp = t
            break
    if tp is None:
        tp = entry + min_rr * risk if is_long else entry - min_rr * risk
    rr = abs(tp - entry) / risk

    active = [name for name, v in flags.items() if v]
    return Setup(
        pair=pair, direction=direction, zone=zone, sweep=sweep,
        entry=float(entry), sl=float(sl_price), tp=float(tp),
        rr=round(rr, 2), time=now, entry_is_limit=True,  # toujours un ordre limite (zone ou retest)
        score=score, strategy="sweep_bos", invalidation=sweep.level,
        comments=[f"sweep H4 {sweep.side} @{sweep.level:.5f} (rejet mèche)",
                  f"BOS M15 @{bos_level:.5f}",
                  f"entrée: {entry_kind}",
                  f"score {score}/6 ({', '.join(active) if active else 'aucune confluence'})"])
