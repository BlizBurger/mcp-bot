"""Tests de la stratégie sweep_bos (sweep 4H + BOS M15 + scoring)."""

from datetime import datetime

import pandas as pd
import pytest

from smc.config import load_config
from smc.strategies import get_strategy
from smc.strategies.sweep_bos import h4_liquidity_levels, is_h4_sweep


def h4_df(rows, start=datetime(2026, 6, 29, 0, 0)):
    return pd.DataFrame([
        {"time": start + pd.Timedelta(hours=4 * i), "open": o, "high": h,
         "low": l, "close": c, "tick_volume": 100}
        for i, (o, h, l, c) in enumerate(rows)])


class TestH4Sweep:
    def test_valid_low_sweep(self):
        # mèche 12 pips sous 1.0000, clôture au-dessus, mèche = 75% du range
        c = pd.Series({"open": 1.0006, "high": 1.0010, "low": 0.9988, "close": 1.0004})
        # range 22 pips ; mèche basse = min(o,c)-low = 1.0004-0.9988 = 16 pips (73%)
        assert is_h4_sweep(c, 1.0000, "low", min_depth=0.0001, min_wick_ratio=0.6)

    def test_wick_too_small(self):
        # gros corps, petite mèche : rejet pas net -> pas un sweep valide
        c = pd.Series({"open": 1.0030, "high": 1.0032, "low": 0.9995, "close": 1.0002})
        assert not is_h4_sweep(c, 1.0000, "low", 0.0001, 0.6)

    def test_close_beyond_level_fails(self):
        c = pd.Series({"open": 1.0006, "high": 1.0010, "low": 0.9988, "close": 0.9995})
        assert not is_h4_sweep(c, 1.0000, "low", 0.0001, 0.6)

    def test_depth_minimum(self):
        c = pd.Series({"open": 1.0006, "high": 1.0010, "low": 0.99995, "close": 1.0004})
        assert not is_h4_sweep(c, 1.0000, "low", min_depth=0.0005, min_wick_ratio=0.0)

    def test_high_sweep(self):
        c = pd.Series({"open": 1.0094, "high": 1.0115, "low": 1.0090, "close": 1.0096})
        assert is_h4_sweep(c, 1.0100, "high", 0.0001, 0.6)


class TestLiquidityLevels:
    def test_swing_and_sides(self):
        # un swing high net à 1.0100 (3 bougies de chaque côté plus basses)
        rows = [(1.0, 1.0010, 0.9990, 1.0)] * 3
        rows.append((1.0, 1.0100, 0.9995, 1.0050))   # swing high
        rows += [(1.0, 1.0010, 0.9990, 1.0)] * 3
        # un swing low net à 0.9900
        rows += [(1.0, 1.0010, 0.9990, 1.0)] * 2
        rows.append((1.0, 1.0005, 0.9900, 0.9950))   # swing low
        rows += [(1.0, 1.0010, 0.9990, 1.0)] * 3
        cfg_s = {"swing_k_h4": 3, "equal_tolerance_pct": 0.05,
                 "max_levels_per_side": 5}
        lv = h4_liquidity_levels(h4_df(rows), None, cfg_s,
                                 {"asian": "00:00-07:00", "killzones": {}},
                                 ref_price=1.0)
        assert 1.0100 in lv["above"]
        assert 0.9900 in lv["below"]
        assert all(x > 1.0 for x in lv["above"])
        assert all(x < 1.0 for x in lv["below"])


def build_scenario():
    """H4 : range vers 1.0000-1.0100 avec un swing low net à 1.0000, puis une
    bougie de sweep (mèche à 0.9985, clôture 1.0040, rejet net).
    M15 : après la clôture du sweep, pullback puis BOS haussier en clôture."""
    rows = []
    for i in range(30):  # marché en range, swings alternés
        if i % 6 == 3:
            rows.append((1.0040, 1.0050, 1.0000, 1.0045))   # swing lows ~1.0000
        elif i % 6 == 0:
            rows.append((1.0050, 1.0100, 1.0040, 1.0055))   # swing highs ~1.0100
        else:
            rows.append((1.0045, 1.0070, 1.0030, 1.0050))
    # bougie de sweep : mèche 15 pips sous 1.0000, clôture 1.0040, mèche 73%
    rows.append((1.0042, 1.0048, 0.9985, 1.0040))
    h4 = h4_df(rows, start=datetime(2026, 6, 29, 0, 0))
    sweep_close = h4["time"].iloc[-1] + pd.Timedelta(hours=4)

    # M15 : historique plat avant, puis après le sweep : pullback + swing high
    # à 1.0052, puis cassure en clôture à 1.0058
    m15_rows = []
    t0 = sweep_close - pd.Timedelta(hours=20)
    t = t0
    while t < sweep_close:
        m15_rows.append({"time": t, "open": 1.0040, "high": 1.0046,
                         "low": 1.0034, "close": 1.0040, "tick_volume": 100})
        t += pd.Timedelta(minutes=15)
    post = [
        (1.0040, 1.0045, 1.0020, 1.0030),
        (1.0030, 1.0040, 1.0018, 1.0035),
        (1.0035, 1.0052, 1.0030, 1.0048),   # swing high à 1.0052 (pivot k=2)
        (1.0048, 1.0050, 1.0036, 1.0040),
        (1.0040, 1.0044, 1.0032, 1.0038),
        (1.0038, 1.0046, 1.0034, 1.0044),
        (1.0044, 1.0060, 1.0042, 1.0058),   # BOS : clôture > 1.0052
    ]
    for o, h, l, c in post:
        m15_rows.append({"time": t, "open": o, "high": h, "low": l,
                         "close": c, "tick_volume": 100})
        t += pd.Timedelta(minutes=15)
    return h4, pd.DataFrame(m15_rows)


class TestSweepBosSetup:
    def cfg(self, **overrides):
        cfg = load_config()
        cfg["strategy_name"] = "sweep_bos"
        cfg["sweep_bos"] = {**cfg["sweep_bos"], "min_sweep_pips": 1.0,
                            "min_score": 0, **overrides}
        cfg["calendar"] = {"skip_dates": [], "skip_friday_after": ""}
        cfg["strategy"]["min_risk_pips"] = 0.0
        return cfg

    def test_full_long_setup(self):
        h4, m15 = build_scenario()
        setup = get_strategy("sweep_bos")("EURUSD", {"htf": h4, "ltf": m15},
                                          self.cfg(), 0.0001)
        assert setup is not None
        assert setup.direction == "long"
        assert setup.strategy == "sweep_bos"
        assert setup.entry_is_limit
        assert 0 <= setup.score <= 6
        # SL sous l'extrême du sweep (0.9985) moins le buffer
        assert setup.sl < 0.9985
        # TP >= min_rr
        risk = setup.entry - setup.sl
        assert setup.tp >= setup.entry + 2.0 * risk - 1e-9
        assert setup.invalidation == pytest.approx(1.0000)

    def test_min_score_filter(self):
        h4, m15 = build_scenario()
        setup = get_strategy("sweep_bos")("EURUSD", {"htf": h4, "ltf": m15},
                                          self.cfg(min_score=6), 0.0001)
        assert setup is None  # 6/6 quasi impossible sur ce scénario simple

    def test_no_setup_without_bos(self):
        h4, m15 = build_scenario()
        m15 = m15.iloc[:-1]  # on retire la bougie de BOS
        setup = get_strategy("sweep_bos")("EURUSD", {"htf": h4, "ltf": m15},
                                          self.cfg(), 0.0001)
        assert setup is None

    def test_invalidation_close_below_swept_level(self):
        h4, m15 = build_scenario()
        # une clôture M15 SOUS le niveau sweepé (1.0000) avant le BOS
        m15.loc[len(m15) - 4, "close"] = 0.9992
        setup = get_strategy("sweep_bos")("EURUSD", {"htf": h4, "ltf": m15},
                                          self.cfg(), 0.0001)
        assert setup is None

    def test_window_expiry(self):
        h4, m15 = build_scenario()
        setup = get_strategy("sweep_bos")("EURUSD", {"htf": h4, "ltf": m15},
                                          self.cfg(bos_window_m15=3), 0.0001)
        assert setup is None  # BOS à la 7e bougie : hors fenêtre de 3

    def test_entry_zone_kinds_filter(self):
        h4, m15 = build_scenario()
        # le scénario entre normalement sur l'OB ; en interdisant les OB,
        # l'entrée retombe sur le retest du BOS
        setup_ob = get_strategy("sweep_bos")("EURUSD", {"htf": h4, "ltf": m15},
                                             self.cfg(), 0.0001)
        cfg_no_ob = self.cfg(entry_zone_kinds=["FVG", "IFVG", "Breaker"])
        setup_no_ob = get_strategy("sweep_bos")("EURUSD", {"htf": h4, "ltf": m15},
                                                cfg_no_ob, 0.0001)
        assert setup_ob is not None and setup_no_ob is not None
        assert setup_ob.zone.kind == "OB"
        assert setup_no_ob.zone.kind == "BOS"   # repli : retest du swing cassé
        assert setup_no_ob.entry != setup_ob.entry
