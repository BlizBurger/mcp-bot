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
        # fixe explicitement les réglages dont les scénarios dépendent, pour
        # que les tests ne cassent pas quand les défauts de config.yaml évoluent
        cfg = load_config()
        cfg["strategy_name"] = "sweep_bos"
        cfg["sweep_bos"] = {**cfg["sweep_bos"], "min_sweep_pips": 1.0,
                            "min_score": 0,
                            "entry_zone_kinds": ["FVG", "OB", "IFVG", "Breaker"],
                            **overrides}
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


class TestPartialTake:
    """Prise partielle : +1R atteint puis retour à l'entrée -> on garde la
    moitié du 1R encaissé au lieu d'un breakeven à ~0."""

    def test_partial_then_breakeven(self):
        import pandas as pd
        from smc.backtest import simulate_all

        h4, m15 = build_scenario()
        cfg = TestSweepBosSetup().cfg()
        cfg["scanner"] = {"history_bars_ltf": 300, "history_bars_htf": 120,
                          "history_bars_d1": 60}
        cfg["backtest"] = {"max_trades_per_day": 1, "zone_fill_timeout_bars": 16,
                           "spread_pips": {"default": 0.0}}
        cfg["exits"] = {"breakeven_after_r": 1.0, "max_holding_bars": 0,
                        "partial_at_r": 1.0, "partial_fraction": 0.5}

        # entrée attendue ~1.0044, risque ~71 pips -> +1R ~ 1.0115
        # chemin : retour dans la zone (remplissage du limite), montée au-delà
        # de +1R, puis retour sous l'entrée (breakeven)
        last_t = m15["time"].iloc[-1]
        path = [1.0050, 1.0043]                                  # remplissage
        path += [1.0043 + 0.0008 * k for k in range(1, 12)]      # jusqu'à ~1.0131
        path += [1.0131 - 0.0009 * k for k in range(1, 12)]      # retour sous 1.0044
        rows = [{"time": last_t + pd.Timedelta(minutes=15 * k), "open": p,
                 "high": p + 0.0003, "low": p - 0.0003, "close": p,
                 "tick_volume": 100} for k, p in enumerate(path, start=1)]
        m15x = pd.concat([m15, pd.DataFrame(rows)], ignore_index=True)

        df = simulate_all(cfg, {"EURUSD": {"pip": 0.0001, "htf": h4,
                                           "ltf": m15x, "d1": None}})
        assert not df.empty
        t = df.iloc[0]
        assert t["exit_kind"] == "breakeven"
        # moitié encaissée à +1R, moitié rendue à breakeven => ~ +0.5R
        assert t["result_r"] == pytest.approx(0.5, abs=0.05)


class TestAmdBonus:
    """Détecteur AMD : accumulation (compression ATR) -> manipulation avec
    rejet confirmé."""

    @staticmethod
    def amd_df(rejection=True, side="low"):
        """20 bougies volatiles (ATR large), 12 bougies d'accumulation serrée
        1.0000-1.0004, puis manipulation sous/au-dessus du range."""
        import pandas as pd
        from datetime import datetime
        rows = []
        # phase volatile : ranges de 40 pips (gonfle l'ATR)
        for i in range(20):
            base = 1.0000 + (0.0040 if i % 2 == 0 else -0.0040)
            rows.append((base, base + 0.0020, base - 0.0020, 1.0000))
        # accumulation : 12 bougies dans 4 pips
        for i in range(12):
            rows.append((1.0001, 1.0004, 1.0000, 1.0002))
        if side == "low":
            if rejection:
                # mèche sous 1.0000, clôture revenue dans le range
                rows.append((1.0001, 1.0003, 0.9988, 1.0002))
            else:
                # cassure franche sans rejet : clôtures sous le range
                rows.append((1.0001, 1.0002, 0.9985, 0.9987))
                for i in range(12):
                    rows.append((0.9987, 0.9989, 0.9980, 0.9984))
        else:
            rows.append((1.0003, 1.0016, 1.0001, 1.0002))
        base_t = datetime(2026, 7, 6, 0, 0)
        return pd.DataFrame([
            {"time": base_t + pd.Timedelta(minutes=15 * i), "open": o,
             "high": h, "low": l, "close": c, "tick_volume": 100}
            for i, (o, h, l, c) in enumerate(rows)])

    CFG_AMD = {"amd_atr_mult": 0.5, "amd_window_min": 8, "amd_window_max": 25,
               "amd_rejection_delay_bars": 10,
               "amd_rejection_tolerance_pct": 10,
               "amd_candidate_expiry_bars": 35}

    def test_amd_low_sweep_confirmed(self):
        from smc.strategies.sweep_bos import find_amd_pattern
        assert find_amd_pattern(self.amd_df(rejection=True, side="low"),
                                "low", self.CFG_AMD) is True

    def test_amd_wrong_side(self):
        from smc.strategies.sweep_bos import find_amd_pattern
        assert find_amd_pattern(self.amd_df(rejection=True, side="low"),
                                "high", self.CFG_AMD) is False

    def test_amd_high_sweep_confirmed(self):
        from smc.strategies.sweep_bos import find_amd_pattern
        assert find_amd_pattern(self.amd_df(rejection=True, side="high"),
                                "high", self.CFG_AMD) is True

    def test_amd_no_rejection_invalidated(self):
        from smc.strategies.sweep_bos import find_amd_pattern
        assert find_amd_pattern(self.amd_df(rejection=False, side="low"),
                                "low", self.CFG_AMD) is False

    def test_amd_never_blocks_setup(self):
        # règle n°5 : AMD absent => le setup sweep+BOS reste valide
        h4, m15 = build_scenario()
        cfg = TestSweepBosSetup().cfg(amd_enabled=True)
        setup = get_strategy("sweep_bos")("EURUSD", {"htf": h4, "ltf": m15},
                                          cfg, 0.0001)
        assert setup is not None  # avec ou sans bonus, jamais bloqué

    def test_amd_level_calibration(self):
        from smc.strategies.sweep_bos import amd_bonus_level
        h4, _ = build_scenario()
        m15 = self.amd_df(rejection=True, side="low")
        cfg = {**self.CFG_AMD, "amd_timeframes": ["M15"]}
        level = amd_bonus_level(m15, h4, "low", cfg)
        assert level > 0          # un pattern existe à un des niveaux
        assert level in (0.5, 1.0, 1.5, 2.0, 3.0)
        # côté opposé : aucun niveau ne confirme
        assert amd_bonus_level(m15, h4, "high", cfg) == 0.0


class TestWeekendForceClose:
    """Clôture forcée du vendredi soir : la position ouverte est coupée au
    marché ~30 min avant la clôture hebdo, gagnante ou perdante."""

    def test_force_close_friday_evening(self):
        from smc.backtest import simulate_all

        h4, m15 = build_scenario()
        # décaler le scénario d'un jour en arrière : le setup se produit le
        # vendredi matin (2026-07-03), la position court vers le soir
        h4 = h4.copy(); m15 = m15.copy()
        h4["time"] -= pd.Timedelta(days=1)
        m15["time"] -= pd.Timedelta(days=1)

        cfg = TestSweepBosSetup().cfg()
        cfg["scanner"] = {"history_bars_ltf": 300, "history_bars_htf": 120,
                          "history_bars_d1": 60}
        cfg["backtest"] = {"max_trades_per_day": 1, "zone_fill_timeout_bars": 16,
                           "spread_pips": {"default": 0.0}}
        cfg["exits"] = {"breakeven_after_r": 0, "max_holding_bars": 0,
                        "partial_at_r": 0}
        cfg["market_hours"] = {"week_close_friday": "23:55",
                               "force_close_minutes_before": 30,
                               "week_open_blackout_minutes": 60,
                               "rollover_blackout": ""}

        # remplissage immédiat puis prix plat jusqu'au vendredi soir
        last_t = m15["time"].iloc[-1]   # vendredi ~05:30
        rows = [{"time": last_t + pd.Timedelta(minutes=15 * k), "open": 1.0043,
                 "high": 1.0047, "low": 1.0040, "close": 1.0043,
                 "tick_volume": 100} for k in range(1, 76)]  # jusqu'à ~00:15 samedi
        m15x = pd.concat([m15, pd.DataFrame(rows)], ignore_index=True)

        df = simulate_all(cfg, {"EURUSD": {"pip": 0.0001, "htf": h4,
                                           "ltf": m15x, "d1": None}})
        assert not df.empty
        t = df.iloc[0]
        assert t["exit_kind"] == "week_close"
        assert t["close_time"].weekday() == 4          # un vendredi
        assert (t["close_time"].hour, t["close_time"].minute) >= (23, 25)
