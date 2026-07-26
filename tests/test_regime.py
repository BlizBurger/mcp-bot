"""Filtre de régime de marché (ADX H4) : indicateur + helper.

L'ADX mesure la FORCE de tendance ; +DI/-DI en donnent la DIRECTION. Le filtre
ne sert qu'à écarter les entrées prises dans le bruit d'un range.
"""

import numpy as np
import pandas as pd

from smc.core import adx, regime_adx


def _series(n: int, slope: float, noise: float = 0.0) -> pd.DataFrame:
    """Bougies H4 synthétiques : dérive linéaire `slope` + bruit sinusoïdal."""
    t = pd.date_range("2024-01-01", periods=n, freq="4h")
    close = 100 + slope * np.arange(n) + noise * np.sin(np.arange(n))
    return pd.DataFrame({"time": t, "open": close,
                         "high": close + 0.5, "low": close - 0.5,
                         "close": close, "tick_volume": 1})


def test_adx_trend_plus_fort_que_range():
    """Une tendance nette doit donner un ADX bien plus élevé qu'un range bruité."""
    tendance = regime_adx(_series(120, slope=1.0))[0]
    range_ = regime_adx(_series(120, slope=0.0, noise=3.0))[0]
    assert tendance > range_
    assert tendance > 25  # tendance franche
    assert range_ < 25    # bruit


def test_adx_direction_haussiere_et_baissiere():
    _, plus_up, minus_up = regime_adx(_series(120, slope=1.0))
    assert plus_up > minus_up        # +DI domine en tendance haussière
    _, plus_dn, minus_dn = regime_adx(_series(120, slope=-1.0))
    assert minus_dn > plus_dn        # -DI domine en tendance baissière


def test_regime_adx_nan_si_historique_court():
    """Pas assez de bougies pour un ADX fiable => (nan, nan, nan) : le filtre
    ne doit alors PAS bloquer (comportement géré côté stratégie)."""
    adx_val, plus_di, minus_di = regime_adx(_series(10, slope=1.0))
    assert np.isnan(adx_val) and np.isnan(plus_di) and np.isnan(minus_di)


def test_adx_colonnes_et_bornes():
    out = adx(_series(120, slope=0.5, noise=1.0))
    assert list(out.columns) == ["adx", "plus_di", "minus_di"]
    last = out.iloc[-1]
    assert 0 <= last["adx"] <= 100
    assert 0 <= last["plus_di"] <= 100
    assert 0 <= last["minus_di"] <= 100
