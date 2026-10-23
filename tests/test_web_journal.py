import copy

import pandas as pd
import pytest

from xauof.config import load_config
from xauof.web import journal as jr

to_utc = lambda sec: pd.Timestamp(sec, unit="s", tz="UTC")  # noqa: E731


def deal(pid, entry, typ, vol, price, profit=0.0, commission=-0.3, t=0):
    return {"symbol": "XAUUSD", "position_id": pid, "entry": entry, "type": typ, "volume": vol, "price": price,
            "profit": profit, "commission": commission, "swap": 0.0, "fee": 0.0, "time": t}


def test_trades_from_deals_with_partial_close_and_r():
    deals = [deal(1, 0, 0, 0.10, 4000.0, t=100),
             deal(1, 1, 1, 0.05, 4004.0, profit=20.0, t=200),
             deal(1, 1, 1, 0.05, 4008.0, profit=40.0, t=300),
             deal(2, 0, 1, 0.02, 4010.0, t=400)]                        # position 2 encore ouverte
    orders = [{"position_id": 1, "sl": 3996.0, "time_setup": 100}, {"position_id": 1, "sl": 4000.0, "time_setup": 250}]
    tr = jr.trades_from_deals(deals, orders, "XAUUSD", 100.0, to_utc)
    assert len(tr) == 1
    t = tr[0]
    assert t["sens"] == "achat" and t["prix_sortie"] == pytest.approx(4006.0)
    assert t["sl_initial"] == 3996.0 and t["risque_usd"] == pytest.approx(40.0)
    assert t["pnl"] == pytest.approx(60.0 - 0.9) and t["r"] == pytest.approx((60.0 - 0.9) / 40.0, abs=1e-3)


def test_merge_keeps_annotations_and_stats(tmp_path):
    cfg = copy.deepcopy(load_config())
    cfg["data"]["dir"] = str(tmp_path)
    fresh = jr.trades_from_deals([deal(1, 0, 0, 0.1, 4000, t=1), deal(1, 1, 1, 0.1, 4004, profit=40, t=2)],
                                 [{"position_id": 1, "sl": 3996.0, "time_setup": 1}], "XAUUSD", 100.0, to_utc)
    trades = jr.merge([], fresh)
    jr.annotate(trades, 1, setup="FVG sur VAL", criteres=["Zone FVG/OB", "Niveau de la veille", "Sens du contexte",
                                                            "inconnu"], note="propre")
    jr.save(cfg, trades)
    again = jr.merge(jr.load(cfg), fresh)
    assert again[0]["setup"] == "FVG sur VAL" and "inconnu" not in again[0]["criteres"]
    st = jr.stats(again)
    assert st["global"]["n"] == 1 and st["a_plus"]["avec"]["n"] == 1
    assert st["par_critere"]["Zone FVG/OB"]["avec"]["r_moyen"] == pytest.approx(again[0]["r"])
    assert st["avertissement"]


def test_stats_empty():
    assert jr.stats([])["global"]["n"] == 0
