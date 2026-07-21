"""Tests des stratégies classiques (indicateurs) et de leurs briques."""

from datetime import datetime

import pandas as pd
import pytest

from smc.strategies import get_strategy
from smc.strategies.indicators import ema, rsi


def series_df(closes, highs=None, lows=None, start=datetime(2026, 6, 1, 0, 0)):
    n = len(closes)
    highs = highs or [c + 0.0005 for c in closes]
    lows = lows or [c - 0.0005 for c in closes]
    return pd.DataFrame({
        "time": [start + pd.Timedelta(minutes=15 * i) for i in range(n)],
        "open": closes, "high": highs, "low": lows, "close": closes,
        "tick_volume": [100] * n,
    })


def base_cfg():
    return {
        "strategy": {"min_risk_pips": 0.0, "swing_k": 2},
        "calendar": {"skip_dates": [], "skip_friday_after": ""},
        "market_hours": {},
    }


class TestIndicators:
    def test_rsi_bounds(self):
        up = pd.Series([1 + 0.01 * i for i in range(30)])
        assert rsi(up, 14).iloc[-1] > 90        # hausse continue -> RSI très haut
        down = pd.Series([1 - 0.01 * i for i in range(30)])
        assert rsi(down, 14).iloc[-1] < 10

    def test_ema_tracks(self):
        s = pd.Series([1.0] * 50)
        assert ema(s, 10).iloc[-1] == pytest.approx(1.0)


class TestDonchian:
    def test_breakout_long(self):
        # 40 bougies plates à 1.10, puis cassure au-dessus du plus haut
        closes = [1.1000] * 40 + [1.1050]
        df = series_df(closes)
        cfg = {**base_cfg(), "donchian": {"channel": 20, "atr_sl_mult": 1.5,
                                          "risk_reward": 2.0, "htf_filter": False}}
        setup = get_strategy("donchian")("EURUSD", {"htf": df, "ltf": df}, cfg, 0.0001)
        assert setup is not None
        assert setup.direction == "long"
        assert setup.sl < setup.entry < setup.tp
        # R:R respecté
        assert (setup.tp - setup.entry) / (setup.entry - setup.sl) == pytest.approx(2.0, abs=0.01)

    def test_no_breakout(self):
        df = series_df([1.1000] * 45)
        cfg = {**base_cfg(), "donchian": {"channel": 20}}
        assert get_strategy("donchian")("EURUSD", {"htf": df, "ltf": df}, cfg, 0.0001) is None


class TestEmaCross:
    def test_golden_cross(self):
        # descente puis remontée nette -> l'EMA rapide croise au-dessus de la lente
        # assez de bougies pour dépasser la chauffe (min 60), croisement tardif
        closes = [1.10 - 0.001 * i for i in range(55)] + [1.045 + 0.003 * i for i in range(35)]
        df = series_df(closes)
        cfg = {**base_cfg(), "ema_cross": {"ema_fast": 10, "ema_slow": 30,
                                           "atr_sl_mult": 1.5, "risk_reward": 2.0}}
        setups = []
        # rejoue toutes les bougies pour capter le croisement (à n'importe quel moment)
        for i in range(61, len(df) + 1):
            s = get_strategy("ema_cross")("EURUSD",
                                          {"htf": df.iloc[:i], "ltf": df.iloc[:i]},
                                          cfg, 0.0001)
            if s:
                setups.append(s)
        assert any(s.direction == "long" for s in setups)


class TestBollinger:
    def test_revert_long(self):
        # 24 bougies légèrement bruitées, un pic bas net (sous la bande),
        # puis retour au-dessus de la bande basse
        import math
        # 40 bougies de chauffe (min period+20) légèrement bruitées
        flat = [1.1000 + 0.0003 * math.sin(i) for i in range(40)]
        closes = flat + [1.0930, 1.0995]
        df = series_df(closes)
        cfg = {**base_cfg(), "bollinger": {"period": 20, "std": 2.0,
                                           "atr_sl_mult": 1.5, "risk_reward": 1.5}}
        setup = get_strategy("bollinger")("EURUSD", {"htf": df, "ltf": df}, cfg, 0.0001)
        assert setup is not None
        assert setup.direction == "long"


class TestEmaRsi:
    def test_registered(self):
        # sanity : la stratégie existe et ne lève pas sur des données plates
        df = series_df([1.1000] * 80)
        cfg = {**base_cfg(), "ema_rsi": {"ema_fast": 21, "ema_slow": 50,
                                         "rsi_period": 14, "htf_filter": False}}
        assert get_strategy("ema_rsi")("EURUSD", {"htf": df, "ltf": df}, cfg, 0.0001) is None
