"""Tests unitaires des fonctions pures de smc.core avec des bougies
construites à la main — garantissent qu'un futur changement ne casse pas la
logique silencieusement."""

from datetime import datetime, time as dtime

import pandas as pd
import pytest

from smc.core import (
    NewsEvent,
    asian_range,
    compute_sl_tp,
    correlation,
    detect_sweep,
    find_equal_levels,
    find_fvgs,
    find_order_blocks,
    htf_bias,
    in_window,
    news_blackout,
    parse_news,
    volume_ok,
)


def make_df(rows):
    """rows : liste de (open, high, low, close[, tick_volume])."""
    base = datetime(2026, 7, 6, 8, 0)
    data = []
    for i, r in enumerate(rows):
        o, h, l, c = r[:4]
        v = r[4] if len(r) > 4 else 100
        data.append({"time": base + pd.Timedelta(minutes=15 * i),
                     "open": o, "high": h, "low": l, "close": c,
                     "tick_volume": v})
    return pd.DataFrame(data)


# ---------------------------------------------------------------------------
# FVG
# ---------------------------------------------------------------------------

class TestFVG:
    def test_bullish_fvg(self):
        # low de la bougie 2 (1.0060) > high de la bougie 0 (1.0020) => gap
        df = make_df([
            (1.0000, 1.0020, 0.9990, 1.0010),
            (1.0010, 1.0070, 1.0005, 1.0065),
            (1.0065, 1.0100, 1.0060, 1.0095),
        ])
        fvgs = find_fvgs(df)
        assert len(fvgs) == 1
        z = fvgs[0]
        assert z.direction == "bullish"
        assert z.bottom == pytest.approx(1.0020)
        assert z.top == pytest.approx(1.0060)

    def test_bearish_fvg(self):
        df = make_df([
            (1.0100, 1.0110, 1.0080, 1.0090),
            (1.0090, 1.0095, 1.0020, 1.0025),
            (1.0025, 1.0040, 1.0000, 1.0005),  # high 1.0040 < low bougie 0 (1.0080)
        ])
        fvgs = find_fvgs(df)
        assert len(fvgs) == 1
        assert fvgs[0].direction == "bearish"
        assert fvgs[0].top == pytest.approx(1.0080)
        assert fvgs[0].bottom == pytest.approx(1.0040)

    def test_no_fvg_when_overlap(self):
        df = make_df([
            (1.0000, 1.0050, 0.9990, 1.0040),
            (1.0040, 1.0060, 1.0030, 1.0055),
            (1.0055, 1.0070, 1.0045, 1.0065),  # low 1.0045 < high bougie 0
        ])
        assert find_fvgs(df) == []

    def test_min_size_filter(self):
        df = make_df([
            (1.0000, 1.0020, 0.9990, 1.0010),
            (1.0010, 1.0070, 1.0005, 1.0065),
            (1.0065, 1.0100, 1.0025, 1.0095),  # gap de 0.0005 seulement
        ])
        assert find_fvgs(df, min_size=0.0010) == []
        assert len(find_fvgs(df, min_size=0.0004)) == 1


# ---------------------------------------------------------------------------
# Order blocks
# ---------------------------------------------------------------------------

class TestOrderBlock:
    def test_bullish_ob(self):
        # 5 bougies plates, une baissière, puis une impulsion haussière qui
        # casse le plus haut des 10 précédentes -> la baissière devient l'OB
        rows = [(1.0000, 1.0010, 0.9995, 1.0005)] * 9
        rows.append((1.0005, 1.0008, 0.9985, 0.9990))       # bougie baissière (OB attendu)
        rows.append((0.9990, 1.0100, 0.9988, 1.0090))       # impulsion qui casse 1.0010
        df = make_df(rows)
        obs = [z for z in find_order_blocks(df, lookback=10) if z.direction == "bullish"]
        assert len(obs) == 1
        assert obs[0].index == 9
        assert obs[0].top == pytest.approx(1.0008)
        assert obs[0].bottom == pytest.approx(0.9985)

    def test_bearish_ob(self):
        rows = [(1.0000, 1.0010, 0.9990, 1.0005)] * 9
        rows.append((1.0005, 1.0030, 1.0000, 1.0025))       # bougie haussière (OB attendu)
        rows.append((1.0025, 1.0028, 0.9900, 0.9910))       # impulsion sous 0.9990
        df = make_df(rows)
        obs = [z for z in find_order_blocks(df, lookback=10) if z.direction == "bearish"]
        assert len(obs) == 1
        assert obs[0].index == 9

    def test_no_ob_without_structure_break(self):
        rows = [(1.0000, 1.0010, 0.9990, 1.0005)] * 12
        assert find_order_blocks(df := make_df(rows), lookback=10) == []


# ---------------------------------------------------------------------------
# Equal highs/lows + sweep
# ---------------------------------------------------------------------------

class TestLiquidity:
    def test_equal_highs(self):
        # deux swing highs à 1.0100 et 1.0101 (tolérance 2 pips = 0.0002)
        rows = [
            (1.0000, 1.0010, 0.9990, 1.0005),
            (1.0005, 1.0015, 0.9995, 1.0010),
            (1.0010, 1.0100, 1.0005, 1.0080),  # swing high 1
            (1.0080, 1.0085, 1.0040, 1.0050),
            (1.0050, 1.0060, 1.0030, 1.0055),
            (1.0055, 1.0101, 1.0050, 1.0085),  # swing high 2 (quasi égal)
            (1.0085, 1.0090, 1.0045, 1.0050),
            (1.0050, 1.0055, 1.0020, 1.0030),
        ]
        levels = find_equal_levels(make_df(rows), tolerance=0.0002)
        assert levels["highs"] == [pytest.approx(1.0101)]

    def test_no_equal_when_far_apart(self):
        rows = [
            (1.0000, 1.0010, 0.9990, 1.0005),
            (1.0005, 1.0015, 0.9995, 1.0010),
            (1.0010, 1.0100, 1.0005, 1.0080),
            (1.0080, 1.0085, 1.0040, 1.0050),
            (1.0050, 1.0060, 1.0030, 1.0055),
            (1.0055, 1.0150, 1.0050, 1.0085),  # 50 pips plus haut
            (1.0085, 1.0090, 1.0045, 1.0050),
            (1.0050, 1.0055, 1.0020, 1.0030),
        ]
        assert find_equal_levels(make_df(rows), tolerance=0.0002)["highs"] == []

    def test_sweep_high_detected(self):
        candle = pd.Series({"high": 1.0110, "low": 1.0050, "close": 1.0080})
        s = detect_sweep(candle, level=1.0100, side="high")
        assert s is not None
        assert s.extreme == pytest.approx(1.0110)

    def test_no_sweep_if_close_beyond(self):
        # clôture AU-DESSUS du niveau = cassure franche, pas un sweep
        candle = pd.Series({"high": 1.0110, "low": 1.0050, "close": 1.0105})
        assert detect_sweep(candle, level=1.0100, side="high") is None

    def test_sweep_low(self):
        candle = pd.Series({"high": 1.0060, "low": 0.9990, "close": 1.0020})
        s = detect_sweep(candle, level=1.0000, side="low")
        assert s is not None
        assert s.side == "low"
        assert s.extreme == pytest.approx(0.9990)


# ---------------------------------------------------------------------------
# SL / TP
# ---------------------------------------------------------------------------

class TestSLTP:
    def test_long(self):
        # entrée 1.1000, sweep low à 1.0950, buffer 3 pips, R:R 2
        sl, tp = compute_sl_tp("long", 1.1000, 1.0950, 3.0, 0.0001, 2.0)
        assert sl == pytest.approx(1.0947)
        assert tp == pytest.approx(1.1000 + 2 * (1.1000 - 1.0947))

    def test_short_jpy(self):
        # paire JPY : pip = 0.01
        sl, tp = compute_sl_tp("short", 150.00, 150.50, 3.0, 0.01, 2.0)
        assert sl == pytest.approx(150.53)
        assert tp == pytest.approx(150.00 - 2 * 0.53)

    def test_invalid_risk_raises(self):
        with pytest.raises(ValueError):
            # SL au-dessus de l'entrée pour un long => risque négatif
            compute_sl_tp("long", 1.1000, 1.2000, 3.0, 0.0001, 2.0)


# ---------------------------------------------------------------------------
# Filtres
# ---------------------------------------------------------------------------

class TestFilters:
    def test_volume_ok(self):
        rows = [(1.0, 1.0, 1.0, 1.0, 100)] * 20 + [(1.0, 1.0, 1.0, 1.0, 130)]
        df = make_df(rows)
        assert volume_ok(df, 20, mult=1.2, period=20) is True   # 130 >= 120
        rows[-1] = (1.0, 1.0, 1.0, 1.0, 110)
        assert volume_ok(make_df(rows), 20, mult=1.2, period=20) is False

    def test_volume_not_enough_history(self):
        df = make_df([(1.0, 1.0, 1.0, 1.0, 500)] * 5)
        assert volume_ok(df, 4, period=20) is False

    def test_correlation_positive(self):
        a = pd.Series([1.0 + 0.001 * i for i in range(60)])
        assert correlation(a, a * 2, window=50) == pytest.approx(1.0)

    def test_correlation_negative(self):
        # b construit pour que ses rendements soient exactement -1 × ceux de a
        returns = [0.001 * (1 + i % 5) for i in range(60)]
        a_vals, b_vals = [1.0], [2.0]
        for r in returns:
            a_vals.append(a_vals[-1] * (1 + r))
            b_vals.append(b_vals[-1] * (1 - r))
        assert correlation(pd.Series(a_vals), pd.Series(b_vals),
                           window=50) == pytest.approx(-1.0)


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------

class TestNews:
    TEXT = """# news du jour
08:30 USD Non-Farm Payrolls High
10:00 EUR Discours BCE Medium
14:00 GBP BoE Rate Decision High
ligne invalide
"""

    def test_parse(self):
        events = parse_news(self.TEXT)
        assert len(events) == 3
        assert events[0].currency == "USD"
        assert events[0].time == dtime(8, 30)
        assert events[0].is_high
        assert not events[1].is_high
        assert events[1].name == "Discours BCE"

    def test_blackout_active(self):
        events = parse_news(self.TEXT)
        now = datetime(2026, 7, 9, 8, 15)  # 15 min avant le NFP
        assert news_blackout(events, "EURUSD", now, 30) is not None
        assert news_blackout(events, "USDJPY", now, 30) is not None

    def test_blackout_wrong_currency(self):
        events = parse_news(self.TEXT)
        now = datetime(2026, 7, 9, 8, 15)
        assert news_blackout(events, "EURJPY", now, 30) is None  # NFP = USD

    def test_blackout_expired(self):
        events = parse_news(self.TEXT)
        now = datetime(2026, 7, 9, 9, 30)  # 60 min après le NFP
        assert news_blackout(events, "EURUSD", now, 30) is None

    def test_medium_impact_not_blocking(self):
        events = parse_news(self.TEXT)
        now = datetime(2026, 7, 9, 10, 0)  # pile sur la news BCE Medium
        assert news_blackout(events, "EURUSD", now, 30) is None


# ---------------------------------------------------------------------------
# Sessions / biais
# ---------------------------------------------------------------------------

class TestSessions:
    def test_in_window(self):
        assert in_window(datetime(2026, 7, 9, 9, 0), "08:00-11:00")
        assert not in_window(datetime(2026, 7, 9, 11, 0), "08:00-11:00")
        assert in_window(datetime(2026, 7, 9, 23, 30), "22:00-02:00")  # minuit

    def test_asian_range(self):
        base = datetime(2026, 7, 9, 0, 0)
        df = pd.DataFrame([
            {"time": base + pd.Timedelta(hours=h), "open": 1.0, "high": 1.0 + h * 0.001,
             "low": 1.0 - h * 0.001, "close": 1.0, "tick_volume": 100}
            for h in range(10)
        ])
        ar = asian_range(df, base, "00:00-07:00")
        assert ar == (pytest.approx(1.006), pytest.approx(0.994))  # h=0..6

    # Chemin de prix en zigzag : swings H1=1.05 < H2=1.08 < H3=1.10 (HH)
    # et L1=1.01 < L2=1.03 (HL) => structure haussière.
    UPTREND = [1.00, 1.02, 1.05, 1.03, 1.01, 1.04, 1.08,
               1.05, 1.03, 1.06, 1.10, 1.07, 1.05]

    @staticmethod
    def dojis(path):
        """Bougies plates (o=h=l=c) suivant un chemin de prix."""
        return make_df([(v, v, v, v) for v in path])

    def test_htf_bias_bullish(self):
        assert htf_bias(self.dojis(self.UPTREND)) == "bullish"

    def test_htf_bias_bearish(self):
        assert htf_bias(self.dojis([2.5 - v for v in self.UPTREND])) == "bearish"


# ---------------------------------------------------------------------------
# Intégration : pipeline AMD complet (accumulation -> manipulation -> distribution)
# ---------------------------------------------------------------------------

from smc.core import find_amd_setup  # noqa: E402


class TestFindAMDSetup:
    CFG = {
        "strategy": {
            "risk_reward": 2.0, "sl_buffer_pips": 3.0, "fvg_min_pips": 2.0,
            "equal_level_tolerance_pips": 2.0, "volume_mult": 1.2,
            "volume_period": 20, "swing_k": 2, "ob_lookback": 10,
            "liquidity_lookback": 120,
        },
        "sessions": {
            "asian": "00:00-07:00",
            "killzones": {"london": "08:00-11:00", "newyork": "13:30-16:00"},
        },
    }

    @staticmethod
    def bullish_htf():
        """H4 en tendance haussière nette (HH + HL)."""
        path = []
        base = 1.0
        for _ in range(6):
            path += [base + 0.01 * s for s in range(6)]        # jambe haussière
            path += [base + 0.04, base + 0.025]                # retracement (creux strict)
            base += 0.03
        return make_df([(v, v, v, v) for v in path])

    @staticmethod
    def ltf_amd_day():
        """Journée M15 : range asiatique 1.0000-1.0050, sweep du low asiatique
        à 08:00, impulsion haussière avec FVG et gros volume, retour dans le
        FVG sur la dernière bougie (09:00, killzone Londres)."""
        base = datetime(2026, 7, 6, 0, 0)
        rows = []
        # Accumulation : 00:00 -> 07:00 (28 bougies), range 1.0000-1.0050
        for i in range(28):
            rows.append((1.0020, 1.0050, 1.0000, 1.0030, 100))
        # 07:00 -> 08:00 : 4 bougies neutres dans le range
        for i in range(4):
            rows.append((1.0025, 1.0040, 1.0010, 1.0030, 100))
        # 08:00 Manipulation : mèche sous le low asiatique, clôture dedans
        rows.append((1.0015, 1.0030, 0.9990, 1.0020, 110))
        # Distribution : impulsion haussière, FVG entre h(c1)=1.0040 et l(c3)=1.0060
        rows.append((1.0020, 1.0040, 1.0015, 1.0035, 150))   # c1
        rows.append((1.0035, 1.0080, 1.0030, 1.0075, 200))   # c2 impulsive
        rows.append((1.0075, 1.0100, 1.0060, 1.0095, 300))   # c3 (crée le FVG)
        # Retour du prix dans le FVG [1.0040 ; 1.0060]
        rows.append((1.0095, 1.0096, 1.0045, 1.0050, 120))
        df = pd.DataFrame([
            {"time": base + pd.Timedelta(minutes=15 * i), "open": o, "high": h,
             "low": l, "close": c, "tick_volume": v}
            for i, (o, h, l, c, v) in enumerate(rows)
        ])
        return df

    def test_full_long_setup(self):
        setup = find_amd_setup("EURUSD", self.bullish_htf(), self.ltf_amd_day(),
                               self.CFG, pip_size=0.0001)
        assert setup is not None
        assert setup.direction == "long"
        assert setup.sweep is not None
        assert setup.sweep.side == "low"
        assert setup.sweep.extreme == pytest.approx(0.9990)
        assert setup.zone.direction == "bullish"
        # SL derrière l'extrême du sweep + 3 pips de buffer
        assert setup.sl == pytest.approx(0.9990 - 0.0003)
        # TP = risque x 2
        risk = setup.entry - setup.sl
        assert setup.tp == pytest.approx(setup.entry + 2.0 * risk)

    def test_no_setup_outside_killzone(self):
        ltf = self.ltf_amd_day()
        # même scénario décalé pour finir à 06:00 (hors killzone)
        ltf["time"] = ltf["time"] - pd.Timedelta(hours=3)
        assert find_amd_setup("EURUSD", self.bullish_htf(), ltf,
                              self.CFG, pip_size=0.0001) is None

    def test_no_setup_when_bias_neutral(self):
        flat_htf = make_df([(1.0, 1.001, 0.999, 1.0)] * 40)
        assert find_amd_setup("EURUSD", flat_htf, self.ltf_amd_day(),
                              self.CFG, pip_size=0.0001) is None

    def test_min_risk_pips_rejects_tiny_risk(self):
        # même scénario valide, mais avec un risque minimum énorme : rejeté
        cfg = {**self.CFG, "strategy": {**self.CFG["strategy"],
                                        "min_risk_pips": 100.0}}
        assert find_amd_setup("EURUSD", self.bullish_htf(), self.ltf_amd_day(),
                              cfg, pip_size=0.0001) is None

    def test_min_risk_pips_accepts_normal_risk(self):
        # risque du scénario ≈ 63 pips : passe avec un minimum de 8 pips
        cfg = {**self.CFG, "strategy": {**self.CFG["strategy"],
                                        "min_risk_pips": 8.0}}
        setup = find_amd_setup("EURUSD", self.bullish_htf(), self.ltf_amd_day(),
                               cfg, pip_size=0.0001)
        assert setup is not None


# ---------------------------------------------------------------------------
# Nouveaux filtres : rejet, profondeur de sweep, D1, ordre limite, calendrier
# ---------------------------------------------------------------------------

from smc.core import calendar_blocked, is_rejection_candle  # noqa: E402


class TestRejectionCandle:
    def test_long_rejection(self):
        # corps haussier, clôture dans la moitié haute
        c = pd.Series({"open": 1.0046, "high": 1.0062, "low": 1.0042, "close": 1.0058})
        assert is_rejection_candle(c, "long") is True

    def test_long_traversal_fails(self):
        # bougie baissière qui traverse : pas un rejet pour un long
        c = pd.Series({"open": 1.0095, "high": 1.0096, "low": 1.0045, "close": 1.0050})
        assert is_rejection_candle(c, "long") is False

    def test_short_rejection(self):
        c = pd.Series({"open": 1.0058, "high": 1.0062, "low": 1.0042, "close": 1.0046})
        assert is_rejection_candle(c, "short") is True


class TestCalendar:
    def test_skip_date(self):
        cfg = {"skip_dates": ["12-24"], "skip_friday_after": ""}
        assert calendar_blocked(datetime(2025, 12, 24, 10, 0), cfg) is not None
        assert calendar_blocked(datetime(2025, 12, 23, 10, 0), cfg) is None

    def test_friday_afternoon(self):
        cfg = {"skip_dates": [], "skip_friday_after": "12:00"}
        friday = datetime(2026, 7, 10, 14, 0)   # un vendredi
        assert calendar_blocked(friday, cfg) is not None
        assert calendar_blocked(friday.replace(hour=9), cfg) is None
        monday = datetime(2026, 7, 6, 14, 0)
        assert calendar_blocked(monday, cfg) is None

    def test_disabled(self):
        assert calendar_blocked(datetime(2025, 12, 24, 10, 0), None) is None
        assert calendar_blocked(datetime(2025, 12, 24, 10, 0),
                                {"skip_dates": [], "skip_friday_after": ""}) is None


class TestNewSetupFilters:
    """Réutilise les fixtures de TestFindAMDSetup avec les nouveaux filtres."""

    @staticmethod
    def base():
        return TestFindAMDSetup()

    @staticmethod
    def cfg_with(**strategy_overrides):
        t = TestFindAMDSetup()
        return {**t.CFG, "strategy": {**t.CFG["strategy"], **strategy_overrides}}

    def test_sweep_depth_filter(self):
        t = self.base()
        # profondeur du sweep de la fixture : 10 pips (1.0000 -> 0.9990)
        assert find_amd_setup("EURUSD", t.bullish_htf(), t.ltf_amd_day(),
                              self.cfg_with(min_sweep_depth_pips=15.0),
                              pip_size=0.0001) is None
        assert find_amd_setup("EURUSD", t.bullish_htf(), t.ltf_amd_day(),
                              self.cfg_with(min_sweep_depth_pips=5.0),
                              pip_size=0.0001) is not None

    def test_rejection_candle_filter(self):
        t = self.base()
        cfg = self.cfg_with(require_rejection_candle=True)
        # la dernière bougie de la fixture est baissière (traversée) : rejeté
        assert find_amd_setup("EURUSD", t.bullish_htf(), t.ltf_amd_day(),
                              cfg, pip_size=0.0001) is None
        # même journée mais avec une vraie bougie de rejet en dernier
        ltf = t.ltf_amd_day()
        ltf.loc[len(ltf) - 1, ["open", "high", "low", "close"]] = \
            [1.0046, 1.0062, 1.0042, 1.0058]
        assert find_amd_setup("EURUSD", t.bullish_htf(), ltf,
                              cfg, pip_size=0.0001) is not None

    def test_d1_alignment(self):
        t = self.base()
        cfg = self.cfg_with(require_d1_alignment=True)
        bear_d1 = make_df([(v, v, v, v) for v in
                           [2.5 - x for x in [1.00, 1.02, 1.05, 1.03, 1.01,
                                              1.04, 1.08, 1.05, 1.03, 1.06,
                                              1.10, 1.07, 1.05]]])
        assert find_amd_setup("EURUSD", t.bullish_htf(), t.ltf_amd_day(),
                              cfg, pip_size=0.0001, d1_df=bear_d1) is None
        assert find_amd_setup("EURUSD", t.bullish_htf(), t.ltf_amd_day(),
                              cfg, pip_size=0.0001,
                              d1_df=t.bullish_htf()) is not None
        # sans D1 fourni : le filtre ne bloque pas
        assert find_amd_setup("EURUSD", t.bullish_htf(), t.ltf_amd_day(),
                              cfg, pip_size=0.0001, d1_df=None) is not None

    def test_entry_at_zone_edge(self):
        t = self.base()
        cfg = self.cfg_with(entry_at_zone_edge=True, entry_zone_depth=0.5)
        setup = find_amd_setup("EURUSD", t.bullish_htf(), t.ltf_amd_day(),
                               cfg, pip_size=0.0001)
        assert setup is not None
        assert setup.entry_is_limit is True
        # milieu de la zone FVG [1.0040 ; 1.0060]
        assert setup.entry == pytest.approx(1.0050)
        # TP recalculé depuis l'entrée limite avec le même R:R
        risk = setup.entry - setup.sl
        assert setup.tp == pytest.approx(setup.entry + cfg["strategy"]["risk_reward"] * risk)

    def test_calendar_blocks_setup(self):
        t = self.base()
        cfg = {**t.CFG, "calendar": {"skip_dates": ["07-06"],
                                     "skip_friday_after": ""}}
        # la fixture se déroule le 2026-07-06
        assert find_amd_setup("EURUSD", t.bullish_htf(), t.ltf_amd_day(),
                              cfg, pip_size=0.0001) is None


# ---------------------------------------------------------------------------
# Le vrai config.yaml doit contenir toutes les clés que le code utilise
# ---------------------------------------------------------------------------

class TestRealConfig:
    def test_config_yaml_complete(self):
        from smc.config import load_config
        cfg = load_config()
        for key in ["risk_reward", "sl_buffer_pips", "min_risk_pips",
                    "fvg_min_pips", "equal_level_tolerance_pips",
                    "volume_mult", "volume_period", "correlation_threshold",
                    "correlation_window", "swing_k", "ob_lookback",
                    "liquidity_lookback", "require_rejection_candle",
                    "min_sweep_depth_pips", "require_d1_alignment",
                    "entry_at_zone_edge", "entry_zone_depth"]:
            assert key in cfg["strategy"], f"strategy.{key} manquant"
        assert "breakeven_after_r" in cfg["exits"]
        assert "max_holding_bars" in cfg["exits"]
        assert "skip_dates" in cfg["calendar"]
        assert "asian" in cfg["sessions"]
        assert "spread_pips" in cfg["backtest"]

    def test_find_setup_runs_with_real_config(self):
        """Le pipeline complet doit tourner avec le config.yaml du dépôt
        (attrape les KeyError de clés manquantes)."""
        from smc.config import load_config
        cfg = load_config()
        t = TestFindAMDSetup()
        # ne doit pas lever d'exception, peu importe le résultat
        find_amd_setup("EURUSD", t.bullish_htf(), t.ltf_amd_day(), cfg,
                       pip_size=0.0001, d1_df=t.bullish_htf())


# ---------------------------------------------------------------------------
# Heures de marché forex : blackout ouverture hebdo + rollover
# ---------------------------------------------------------------------------

from smc.core import market_entry_blocked  # noqa: E402


class TestMarketHours:
    CFG = {"week_close_friday": "23:55", "force_close_minutes_before": 30,
           "week_open_blackout_minutes": 60, "rollover_blackout": "23:00-00:15"}

    def test_monday_open_blackout(self):
        # lundi 00:30 (heure serveur) : dans l'heure suivant l'ouverture hebdo
        monday_0030 = datetime(2026, 7, 6, 0, 30)
        assert market_entry_blocked(monday_0030, self.CFG) is not None
        # lundi 01:30 : blackout levé
        assert market_entry_blocked(monday_0030.replace(hour=1), self.CFG) is None

    def test_rollover_blackout(self):
        # mercredi 23:30 : rollover quotidien
        wed_2330 = datetime(2026, 7, 8, 23, 30)
        assert market_entry_blocked(wed_2330, self.CFG) is not None
        # mercredi 22:30 : ok
        assert market_entry_blocked(wed_2330.replace(hour=22), self.CFG) is None
        # jeudi 00:10 : encore dans la fenêtre (à cheval sur minuit)
        thu_0010 = datetime(2026, 7, 9, 0, 10)
        assert market_entry_blocked(thu_0010, self.CFG) is not None

    def test_disabled(self):
        monday_0030 = datetime(2026, 7, 6, 0, 30)
        assert market_entry_blocked(monday_0030, None) is None
        assert market_entry_blocked(
            monday_0030, {"week_open_blackout_minutes": 0,
                          "rollover_blackout": ""}) is None


# ---------------------------------------------------------------------------
# Taille de position
# ---------------------------------------------------------------------------

from smc.core import position_size  # noqa: E402


class TestPositionSize:
    def test_basic_1pct(self):
        # 10k, 1% = 100 ; stop 50 pips ; 10/pip/lot -> 0.2 lot exact
        r = position_size(10000, 1.0, 1.1000, 1.0950, 0.0001, 10.0)
        assert r["stop_pips"] == 50.0
        assert r["lots"] == 0.2
        assert r["risk_amount"] == pytest.approx(100.0)

    def test_rounds_down_to_step(self):
        # lot théorique 0.267 -> arrondi INFÉRIEUR au pas 0.01 = 0.26
        r = position_size(10000, 2.0, 1.1000, 1.0925, 0.0001, 10.0)
        # risk 200, stop 75 pips, risk/lot 750 -> 0.2666..
        assert r["lots"] == 0.26
        assert r["risk_amount"] <= 200.0  # jamais au-dessus du risque visé

    def test_min_volume_floor(self):
        # risque minuscule -> plancher au volume min du broker
        r = position_size(10000, 0.01, 1.1000, 1.0000, 0.0001, 10.0)
        assert r["lots"] == 0.01

    def test_zero_stop_returns_none(self):
        assert position_size(10000, 1.0, 1.1000, 1.1000, 0.0001, 10.0) is None

    def test_unknown_pip_value_returns_none(self):
        assert position_size(10000, 1.0, 1.1000, 1.0950, 0.0001, 0.0) is None
