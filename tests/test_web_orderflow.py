import numpy as np
import pandas as pd

from xauof.web.orderflow_proxy import proxy_bars, tick_signs


def test_tick_rule_carries_previous_sign():
    assert list(tick_signs(np.array([1.0, 1.1, 1.1, 1.0, 1.0, 1.2]))) == [0, 1, 1, -1, -1, 1]


def test_proxy_bars_delta_and_cvd_reset():
    idx = pd.DatetimeIndex(["2026-10-23 12:19:10", "2026-10-23 12:19:40", "2026-10-23 12:20:05",
                            "2026-10-23 12:20:30", "2026-10-23 12:21:00"], tz="UTC")
    t = pd.DataFrame({"bid": [100, 101, 102, 101, 100], "ask": [100.3, 101.3, 102.3, 101.3, 100.3]}, index=idx)
    out = proxy_bars(t, cvd_from=pd.Timestamp("2026-10-23 12:20", tz="UTC"))
    assert list(out["ticks"]) == [2, 2, 1]
    assert list(out["delta"]) == [1, 0, -1]           # 0, +1 | +1, -1 | -1
    assert np.isnan(out["cvd"].iloc[0]) and list(out["cvd"].iloc[1:]) == [0, -1]


def test_proxy_bars_empty():
    assert proxy_bars(pd.DataFrame(columns=["bid", "ask"])).empty
