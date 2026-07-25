"""Tests du moteur walk-forward et de son intégration."""

from datetime import datetime

import pandas as pd
import pytest

from smc.walkforward import (
    TradeResult, bootstrap_expectancy_ci, build_windows, consistency_check,
    run_walkforward, summarize_walkforward, WindowResult,
)


def _trades(rs, base=datetime(2025, 1, 1)):
    return [TradeResult(base, base, r) for r in rs]


class TestBuildWindows:
    def test_no_test_overlaps_vault(self):
        windows, (vault_start, vault_end) = build_windows(
            datetime(2022, 1, 1), datetime(2026, 1, 1),
            train_months=6, test_months=2, step_months=2, vault_months=3)
        assert len(windows) >= 8
        for w in windows:
            # aucune fenêtre de test ne doit empiéter sur le vault
            assert w.test_end <= vault_start
            # train précède strictement le test (pas de fuite)
            assert w.train_end == w.test_start
            assert w.train_start < w.train_end < w.test_end

    def test_windows_step_forward(self):
        windows, _ = build_windows(datetime(2023, 1, 1), datetime(2026, 1, 1))
        starts = [w.train_start for w in windows]
        assert starts == sorted(starts)
        assert len(set(starts)) == len(starts)  # toutes distinctes


class TestBootstrapCI:
    def test_known_positive_edge_excludes_zero(self):
        # edge net : gagnants +2R (55%), perdants -1R (45%) -> espérance +0.65
        rs = [2.0] * 55 + [-1.0] * 45
        mean, lo, hi = bootstrap_expectancy_ci(_trades(rs), n_iterations=3000)
        assert mean == pytest.approx(0.65, abs=0.01)
        assert lo > 0            # l'IC exclut 0 -> edge prouvé

    def test_zero_edge_includes_zero(self):
        # symétrique +1/-1 -> espérance nulle, l'IC doit inclure 0
        rs = ([1.0, -1.0] * 100)
        mean, lo, hi = bootstrap_expectancy_ci(_trades(rs), n_iterations=3000)
        assert lo < 0 < hi

    def test_too_few_trades_returns_nan(self):
        mean, lo, hi = bootstrap_expectancy_ci(_trades([1.0, -1.0, 1.0]))
        assert lo != lo and hi != hi  # NaN


class TestConsistency:
    def test_all_positive(self):
        wr = [WindowResult(None, _trades([1.0, 1.0])) for _ in range(5)]
        assert "positif" in consistency_check(wr)["verdict"].lower()

    def test_unstable(self):
        wr = [WindowResult(None, _trades([2.0, 2.0])),
              WindowResult(None, _trades([-2.0, -2.0])),
              WindowResult(None, _trades([2.0])),
              WindowResult(None, _trades([-2.0]))]
        v = consistency_check(wr)["verdict"].lower()
        assert "bruit" in v or "instable" in v


class TestIntegrationWiring:
    """Câblage make_data_loader + make_strategy_fn + run_walkforward sur des
    données synthétiques (sans MT5)."""

    def _full_data(self):
        # série M15 en tendance haussière franche -> donchian doit trader
        n = 900
        t0 = pd.Timestamp("2025-01-01")
        closes = [1.0000 + 0.0002 * i + (0.0006 if i % 30 < 15 else -0.0002)
                  for i in range(n)]
        df = pd.DataFrame({
            "time": [t0 + pd.Timedelta(minutes=15 * i) for i in range(n)],
            "open": closes,
            "high": [c + 0.0004 for c in closes],
            "low": [c - 0.0004 for c in closes],
            "close": closes, "tick_volume": [100] * n})
        return {"EURUSD": {"pip": 0.0001, "htf": df, "ltf": df, "d1": df}}

    def test_donchian_produces_trades(self):
        from smc.backtest_walkforward import make_data_loader, make_strategy_fn
        from smc.config import load_config

        cfg = load_config()
        cfg["calendar"] = {"skip_dates": [], "skip_friday_after": ""}
        cfg["market_hours"] = {}
        cfg["strategy"]["min_risk_pips"] = 0.0
        cfg["backtest"]["max_trades_per_day"] = 0
        full = self._full_data()
        loader = make_data_loader(full)
        fn = make_strategy_fn("donchian", cfg)

        sl = loader(pd.Timestamp("2025-01-04").to_pydatetime(),
                    pd.Timestamp("2025-01-09").to_pydatetime())
        trades = fn(sl, {("donchian", "channel"): 20,
                         ("donchian", "atr_sl_mult"): 1.5,
                         ("donchian", "risk_reward"): 2.0})
        assert isinstance(trades, list)
        assert all(isinstance(t, TradeResult) for t in trades)
        # les trades doivent être ouverts DANS la fenêtre demandée
        for t in trades:
            assert sl["start"] <= t.entry_time <= sl["end"]

    def test_run_walkforward_smoke(self):
        from smc.backtest_walkforward import make_data_loader, make_strategy_fn
        from smc.config import load_config

        cfg = load_config()
        cfg["calendar"] = {"skip_dates": [], "skip_friday_after": ""}
        cfg["market_hours"] = {}
        cfg["strategy"]["min_risk_pips"] = 0.0
        cfg["backtest"]["max_trades_per_day"] = 0
        loader = make_data_loader(self._full_data())
        fn = make_strategy_fn("donchian", cfg)

        # deux petites fenêtres manuelles
        from smc.walkforward import Window
        base = pd.Timestamp("2025-01-01")
        windows = [Window(base, base + pd.Timedelta(days=3),
                          base + pd.Timedelta(days=3), base + pd.Timedelta(days=5), 0),
                   Window(base + pd.Timedelta(days=2), base + pd.Timedelta(days=5),
                          base + pd.Timedelta(days=5), base + pd.Timedelta(days=7), 1)]
        windows = [Window(w.train_start.to_pydatetime(), w.train_end.to_pydatetime(),
                          w.test_start.to_pydatetime(), w.test_end.to_pydatetime(), w.index)
                   for w in windows]
        results = run_walkforward(fn, loader, windows, strategy_name="donchian")
        assert len(results) == 2
        summary = summarize_walkforward(results, "donchian")
        assert "strategy" in summary
