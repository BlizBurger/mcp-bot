"""Logique métier pure du modèle AMD (Accumulation - Manipulation - Distribution).

Toutes les fonctions de ce module sont pures : elles prennent des DataFrames
pandas (colonnes: time, open, high, low, close, tick_volume) et des paramètres,
et ne touchent ni à MT5, ni au réseau, ni au disque. C'est ce qui permet de les
tester unitairement et de partager exactement la même logique entre le scanner
live et le backtest.

Colonnes attendues : time (datetime), open, high, low, close, tick_volume.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta
from typing import Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Structures de données
# ---------------------------------------------------------------------------

@dataclass
class Zone:
    """Zone d'intérêt (FVG ou Order Block)."""
    kind: str            # "FVG" ou "OB"
    direction: str       # "bullish" ou "bearish"
    top: float
    bottom: float
    index: int           # index (position) de la bougie qui crée la zone

    def contains(self, price: float) -> bool:
        return self.bottom <= price <= self.top


@dataclass
class Sweep:
    """Sweep de liquidité : mèche au-delà d'un niveau puis clôture à l'intérieur."""
    index: int
    side: str            # "high" (sweep des hauts) ou "low" (sweep des bas)
    level: float         # niveau balayé (equal high/low ou borne du range asiatique)
    extreme: float       # extrême de la mèche (le point derrière lequel poser le SL)
    level_kind: str      # "equal" ou "asian"


@dataclass
class Setup:
    """Setup complet prêt à être alerté / simulé."""
    pair: str
    direction: str       # "long" ou "short"
    zone: Zone
    sweep: Optional[Sweep]
    entry: float
    sl: float
    tp: float
    rr: float
    time: datetime
    entry_is_limit: bool = False   # True : `entry` est un ordre limite suggéré
    score: int = 0                 # score de confluence (stratégies à scoring)
    amd: bool = False              # pattern AMD confirmé (bonus de score)
    amd_level: float = 0.0         # compression la plus stricte où l'AMD se
                                   # confirme (x ATR ; 0 = aucun pattern)
    strategy: str = "amd_asian"    # nom de la stratégie qui a produit le setup
    invalidation: float | None = None  # niveau qui invalide le setup s'il clôture au-delà
    comments: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Indicateurs de base
# ---------------------------------------------------------------------------

def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range classique (moyenne mobile simple du true range)."""
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


def swing_points(df: pd.DataFrame, k: int = 2) -> tuple[list[int], list[int]]:
    """Indices des swing highs et swing lows (extrême local sur k bougies de
    chaque côté)."""
    highs, lows = [], []
    h, l = df["high"].values, df["low"].values
    for i in range(k, len(df) - k):
        if h[i] == max(h[i - k:i + k + 1]) and h[i] > max(np.delete(h[i - k:i + k + 1], k)):
            highs.append(i)
        if l[i] == min(l[i - k:i + k + 1]) and l[i] < min(np.delete(l[i - k:i + k + 1], k)):
            lows.append(i)
    return highs, lows


# ---------------------------------------------------------------------------
# Détection FVG / Order Blocks / liquidité
# ---------------------------------------------------------------------------

def find_fvgs(df: pd.DataFrame, min_size: float = 0.0) -> list[Zone]:
    """Fair Value Gaps sur 3 bougies.

    Bullish : low de la bougie i strictement au-dessus du high de la bougie i-2.
    Bearish : high de la bougie i strictement en-dessous du low de la bougie i-2.
    """
    zones: list[Zone] = []
    h, l = df["high"].values, df["low"].values
    for i in range(2, len(df)):
        if l[i] > h[i - 2] and (l[i] - h[i - 2]) >= min_size:
            zones.append(Zone("FVG", "bullish", top=l[i], bottom=h[i - 2], index=i))
        elif h[i] < l[i - 2] and (l[i - 2] - h[i]) >= min_size:
            zones.append(Zone("FVG", "bearish", top=l[i - 2], bottom=h[i], index=i))
    return zones


def find_order_blocks(df: pd.DataFrame, lookback: int = 10) -> list[Zone]:
    """Order Blocks : dernière bougie opposée avant une impulsion qui casse la
    structure.

    Bullish OB : dernière bougie baissière avant une bougie haussière dont la
    clôture dépasse le plus haut des `lookback` bougies précédentes.
    Bearish OB : symétrique.
    """
    zones: list[Zone] = []
    o, h, l, c = (df[x].values for x in ("open", "high", "low", "close"))
    for i in range(lookback, len(df)):
        prior_high = h[i - lookback:i].max()
        prior_low = l[i - lookback:i].min()
        if c[i] > o[i] and c[i] > prior_high:  # impulsion haussière cassant la structure
            for j in range(i - 1, max(i - lookback - 1, -1), -1):
                if c[j] < o[j]:
                    zones.append(Zone("OB", "bullish", top=h[j], bottom=l[j], index=j))
                    break
        elif c[i] < o[i] and c[i] < prior_low:  # impulsion baissière
            for j in range(i - 1, max(i - lookback - 1, -1), -1):
                if c[j] > o[j]:
                    zones.append(Zone("OB", "bearish", top=h[j], bottom=l[j], index=j))
                    break
    return zones


def find_equal_levels(df: pd.DataFrame, tolerance: float, k: int = 2) -> dict[str, list[float]]:
    """Equal highs / equal lows : au moins 2 swings au même niveau (à
    `tolerance` près, en prix).

    Retourne {"highs": [niveaux...], "lows": [niveaux...]} où chaque niveau est
    l'extrême du groupe (le plus haut des equal highs, le plus bas des equal
    lows) — c'est là que repose la liquidité.
    """
    sh, sl_ = swing_points(df, k)
    out: dict[str, list[float]] = {"highs": [], "lows": []}

    def group(values: list[float], pick) -> list[float]:
        levels = []
        used = set()
        for a in range(len(values)):
            if a in used:
                continue
            grp = [values[a]]
            for b in range(a + 1, len(values)):
                if b not in used and abs(values[b] - values[a]) <= tolerance:
                    grp.append(values[b])
                    used.add(b)
            if len(grp) >= 2:
                levels.append(pick(grp))
        return levels

    out["highs"] = group([df["high"].iloc[i] for i in sh], max)
    out["lows"] = group([df["low"].iloc[i] for i in sl_], min)
    return out


def detect_sweep(candle: pd.Series, level: float, side: str,
                 level_kind: str = "equal", index: int = -1) -> Optional[Sweep]:
    """Une bougie sweep un niveau si sa mèche dépasse le niveau mais que sa
    clôture revient à l'intérieur."""
    if side == "high" and candle["high"] > level and candle["close"] < level:
        return Sweep(index=index, side="high", level=level,
                     extreme=float(candle["high"]), level_kind=level_kind)
    if side == "low" and candle["low"] < level and candle["close"] > level:
        return Sweep(index=index, side="low", level=level,
                     extreme=float(candle["low"]), level_kind=level_kind)
    return None


# ---------------------------------------------------------------------------
# Sessions / range asiatique
# ---------------------------------------------------------------------------

def _parse_window(win: str) -> tuple[dtime, dtime]:
    start, end = win.split("-")
    h1, m1 = map(int, start.split(":"))
    h2, m2 = map(int, end.split(":"))
    return dtime(h1, m1), dtime(h2, m2)


def in_window(ts: datetime, window: str) -> bool:
    start, end = _parse_window(window)
    t = ts.time()
    if start <= end:
        return start <= t < end
    return t >= start or t < end  # fenêtre à cheval sur minuit


def asian_range(df: pd.DataFrame, day: datetime, window: str) -> Optional[tuple[float, float]]:
    """(high, low) de la session asiatique du jour `day` (comparaison sur la
    date des bougies). None si aucune bougie dans la fenêtre."""
    mask = (df["time"].dt.date == day.date()) & df["time"].apply(lambda t: in_window(t, window))
    sub = df[mask]
    if sub.empty:
        return None
    return float(sub["high"].max()), float(sub["low"].min())


# ---------------------------------------------------------------------------
# Biais HTF
# ---------------------------------------------------------------------------

def htf_bias(df: pd.DataFrame, k: int = 2) -> str:
    """Biais H4 par structure de marché : deux derniers swings.

    HH + HL -> "bullish" ; LH + LL -> "bearish" ; sinon "neutral".
    """
    sh, sl_ = swing_points(df, k)
    if len(sh) < 2 or len(sl_) < 2:
        return "neutral"
    h1, h2 = df["high"].iloc[sh[-2]], df["high"].iloc[sh[-1]]
    l1, l2 = df["low"].iloc[sl_[-2]], df["low"].iloc[sl_[-1]]
    if h2 > h1 and l2 > l1:
        return "bullish"
    if h2 < h1 and l2 < l1:
        return "bearish"
    return "neutral"


# ---------------------------------------------------------------------------
# Filtres de confluence
# ---------------------------------------------------------------------------

def volume_ok(df: pd.DataFrame, index: int, mult: float = 1.2, period: int = 20) -> bool:
    """tick_volume de la bougie `index` >= mult × moyenne des `period` bougies
    précédentes. NB : le tick_volume MT5 n'est PAS un vrai volume centralisé."""
    if index < period:
        return False
    avg = df["tick_volume"].iloc[index - period:index].mean()
    return bool(df["tick_volume"].iloc[index] >= mult * avg)


def correlation(close_a: pd.Series, close_b: pd.Series, window: int = 50) -> float:
    """Corrélation de Pearson des rendements sur les `window` dernières bougies."""
    ra = close_a.pct_change().dropna().tail(window)
    rb = close_b.pct_change().dropna().tail(window)
    n = min(len(ra), len(rb))
    if n < 5:
        return 0.0
    c = np.corrcoef(ra.tail(n).values, rb.tail(n).values)[0, 1]
    return 0.0 if np.isnan(c) else float(c)


# ---------------------------------------------------------------------------
# Filtre news
# ---------------------------------------------------------------------------

@dataclass
class NewsEvent:
    time: dtime
    currency: str
    name: str
    impact: str

    @property
    def is_high(self) -> bool:
        s = self.impact.lower()
        return s.startswith("high") or "élevé" in s or "eleve" in s or "rouge" in s


def parse_news(text: str) -> list[NewsEvent]:
    """Parse le format manuel : `HH:MM DEVISE Nom de l'événement Impact`.

    L'impact est le dernier mot de la ligne. Les lignes vides ou commençant
    par '#' sont ignorées ; une ligne mal formée est ignorée (le chargeur
    appelant est responsable de logger un warning).
    """
    events = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        try:
            h, m = map(int, parts[0].split(":"))
        except ValueError:
            continue
        events.append(NewsEvent(time=dtime(h, m), currency=parts[1].upper(),
                                name=" ".join(parts[2:-1]), impact=parts[-1]))
    return events


def news_blackout(events: list[NewsEvent], pair: str, now: datetime,
                  window_minutes: int = 30) -> Optional[NewsEvent]:
    """Retourne l'événement bloquant si `now` est à moins de `window_minutes`
    d'une news High impact touchant une des deux devises de la paire."""
    base, quote = pair[:3].upper(), pair[3:6].upper()
    for ev in events:
        if not ev.is_high or ev.currency not in (base, quote):
            continue
        ev_dt = now.replace(hour=ev.time.hour, minute=ev.time.minute,
                            second=0, microsecond=0)
        if abs((now - ev_dt).total_seconds()) <= window_minutes * 60:
            return ev
    return None


# ---------------------------------------------------------------------------
# Bougie de rejet / calendrier
# ---------------------------------------------------------------------------

def is_rejection_candle(candle: pd.Series, direction: str) -> bool:
    """Bougie de rejet : corps dans le sens du trade ET clôture dans la bonne
    moitié de sa range (le prix a visité la zone puis a été repoussé)."""
    rng = float(candle["high"] - candle["low"])
    if rng <= 0:
        return False
    if direction == "long":
        return bool(candle["close"] > candle["open"] and
                    (candle["close"] - candle["low"]) / rng >= 0.5)
    return bool(candle["close"] < candle["open"] and
                (candle["high"] - candle["close"]) / rng >= 0.5)


def market_entry_blocked(now: datetime, mh_cfg: Optional[dict]) -> Optional[str]:
    """Blocage des NOUVELLES entrées lié aux heures de marché forex :
    blackout après l'ouverture hebdo (lundi 00:00 serveur) et pendant le
    rollover quotidien. Ne concerne pas les positions déjà ouvertes."""
    if not mh_cfg:
        return None
    ob = int(mh_cfg.get("week_open_blackout_minutes", 0) or 0)
    if ob and now.weekday() == 0 and (now.hour * 60 + now.minute) < ob:
        return "blackout ouverture hebdomadaire"
    win = mh_cfg.get("rollover_blackout") or ""
    if win and in_window(now, win):
        return "rollover quotidien"
    return None


def calendar_blocked(now: datetime, cal_cfg: Optional[dict]) -> Optional[str]:
    """Raison du blocage calendrier (jours morts, vendredi après-midi), ou
    None si rien ne bloque."""
    if not cal_cfg:
        return None
    if now.strftime("%m-%d") in (cal_cfg.get("skip_dates") or []):
        return f"date évitée ({now.strftime('%m-%d')})"
    fri = cal_cfg.get("skip_friday_after") or ""
    if fri and now.weekday() == 4:
        h, m = map(int, fri.split(":"))
        if now.time() >= dtime(h, m):
            return f"vendredi après {fri} (heure serveur)"
    return None


# ---------------------------------------------------------------------------
# SL / TP
# ---------------------------------------------------------------------------

def compute_sl_tp(direction: str, entry: float, protected_level: float,
                  buffer_pips: float, pip_size: float, rr: float) -> tuple[float, float]:
    """SL derrière le niveau protégé (extrême du sweep, ou borne de la zone si
    pas de sweep) + buffer en pips ; TP = risque × rr."""
    buffer = buffer_pips * pip_size
    if direction == "long":
        sl = protected_level - buffer
        risk = entry - sl
        tp = entry + rr * risk
    else:
        sl = protected_level + buffer
        risk = sl - entry
        tp = entry - rr * risk
    if risk <= 0:
        raise ValueError(f"Risque non positif : entry={entry} sl={sl}")
    return sl, tp


# ---------------------------------------------------------------------------
# Orchestration : détection d'un setup AMD complet
# ---------------------------------------------------------------------------

def find_amd_setup(pair: str, htf_df: pd.DataFrame, ltf_df: pd.DataFrame,
                   cfg: dict, pip_size: float,
                   now: Optional[datetime] = None,
                   d1_df: Optional[pd.DataFrame] = None) -> Optional[Setup]:
    """Cherche un setup AMD complet sur la dernière bougie LTF close.

    Étapes (mêmes pour le scanner live et le backtest) :
      1. Biais H4 par structure — si neutre, pas de setup.
      2. Accumulation : range de la session asiatique du jour.
      3. Manipulation : sweep (asian range ou equal high/low) pendant une
         killzone, dans le sens opposé au biais (on achète après un sweep des
         lows, on vend après un sweep des highs).
      4. Distribution : le prix revient dans un FVG ou OB aligné avec le biais,
         formé après le sweep, dont la bougie impulsive passe le filtre volume.

    Les filtres corrélation et news sont appliqués par l'appelant (ils ont
    besoin de données d'autres paires / du fichier news).
    """
    s = cfg["strategy"]
    if len(ltf_df) < 30 or len(htf_df) < 30:
        return None
    now = now or ltf_df["time"].iloc[-1]

    # Filtres calendrier + heures de marché (jours morts, vendredi après-midi,
    # ouverture hebdo, rollover)
    if calendar_blocked(now, cfg.get("calendar")) or \
            market_entry_blocked(now, cfg.get("market_hours")):
        return None

    bias = htf_bias(htf_df, k=s.get("swing_k", 2))
    if bias == "neutral":
        return None
    direction = "long" if bias == "bullish" else "short"

    # Double alignement : le biais Daily doit confirmer le biais H4
    if s.get("require_d1_alignment") and d1_df is not None:
        if htf_bias(d1_df, k=s.get("swing_k", 2)) != bias:
            return None

    # Accumulation : range asiatique du jour
    ar = asian_range(ltf_df, now, cfg["sessions"]["asian"])
    if ar is None:
        return None
    asian_high, asian_low = ar

    # On ne travaille que pendant les killzones Londres / NY
    if not any(in_window(now, w) for w in cfg["sessions"]["killzones"].values()):
        return None

    # Manipulation : chercher le sweep le plus récent dans les bougies du jour
    tol = s["equal_level_tolerance_pips"] * pip_size
    today = ltf_df[ltf_df["time"].dt.date == now.date()]
    eq = find_equal_levels(ltf_df.tail(s.get("liquidity_lookback", 120)), tolerance=tol)

    sweep: Optional[Sweep] = None
    side = "low" if direction == "long" else "high"
    levels = ([("asian", asian_low)] + [("equal", lv) for lv in eq["lows"]]) if side == "low" \
        else ([("asian", asian_high)] + [("equal", lv) for lv in eq["highs"]])
    for pos in range(len(today)):
        candle = today.iloc[pos]
        if not any(in_window(candle["time"], w) for w in cfg["sessions"]["killzones"].values()):
            continue
        for kind, lv in levels:
            found = detect_sweep(candle, lv, side, level_kind=kind,
                                 index=today.index[pos])
            if found:
                sweep = found  # on garde le plus récent
    if sweep is None:
        return None

    # Profondeur minimale du sweep : une mèche de 2 pips n'a rien "nettoyé"
    depth = (sweep.level - sweep.extreme) if sweep.side == "low" \
        else (sweep.extreme - sweep.level)
    if depth < s.get("min_sweep_depth_pips", 0.0) * pip_size:
        return None

    # Distribution : FVG ou OB aligné avec le biais, formé après le sweep
    after = ltf_df.loc[sweep.index:]
    zones = [z for z in find_fvgs(after, min_size=s["fvg_min_pips"] * pip_size)
             if z.direction == bias]
    zones += [z for z in find_order_blocks(after, lookback=s.get("ob_lookback", 10))
              if z.direction == bias]
    if not zones:
        return None

    price = float(ltf_df["close"].iloc[-1])
    candidates = []
    for z in zones:
        # index de zone relatif à `after` -> repasser en positionnel global
        gidx = after.index[min(z.index, len(after) - 1)]
        ipos = ltf_df.index.get_loc(gidx)
        if not volume_ok(ltf_df, ipos, mult=s["volume_mult"],
                         period=s.get("volume_period", 20)):
            continue
        if z.contains(price):
            candidates.append(z)
    if not candidates:
        return None
    zone = candidates[-1]

    # Bougie de rejet : le retour en zone doit montrer un rejet, pas une traversée
    if s.get("require_rejection_candle") and \
            not is_rejection_candle(ltf_df.iloc[-1], direction):
        return None

    protected = sweep.extreme if sweep else (zone.bottom if direction == "long" else zone.top)

    # Entrée : prix courant, ou ordre limite dans la zone (meilleur prix moyen,
    # au risque de ne jamais être rempli)
    entry_is_limit = bool(s.get("entry_at_zone_edge"))
    if entry_is_limit:
        frac = float(s.get("entry_zone_depth", 0.5))
        span = zone.top - zone.bottom
        entry = zone.top - frac * span if direction == "long" \
            else zone.bottom + frac * span
    else:
        entry = price
    try:
        sl, tp = compute_sl_tp(direction, entry, protected,
                               s["sl_buffer_pips"], pip_size, s["risk_reward"])
    except ValueError:
        # prix déjà repassé de l'autre côté du niveau protégé : setup caduc
        return None
    # Risque minimum : un SL collé à l'entrée (sweep déjà "consommé") donne un
    # trade intradable en réel — le spread mangerait tout le risque.
    if abs(entry - sl) < s.get("min_risk_pips", 0.0) * pip_size:
        return None
    return Setup(pair=pair, direction=direction, zone=zone, sweep=sweep,
                 entry=entry, sl=sl, tp=tp, rr=s["risk_reward"], time=now,
                 entry_is_limit=entry_is_limit,
                 comments=[f"biais H4 {bias}",
                           f"sweep {sweep.side} @{sweep.level:.5f} ({sweep.level_kind})",
                           f"zone {zone.kind} [{zone.bottom:.5f} ; {zone.top:.5f}]"])
