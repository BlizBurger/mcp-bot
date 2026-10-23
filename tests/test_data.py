import copy

import numpy as np
import pandas as pd
import pytest

from xauof.config import load_config
from xauof.data import gc, store, xauusd


@pytest.fixture
def cfg(tmp_path):
    c = copy.deepcopy(load_config())
    c["data"]["dir"] = str(tmp_path)  # chemin absolu : ROOT / abs = abs
    return c


def _raw_trades(start: str, n: int = 4) -> pd.DataFrame:
    ts = pd.date_range(start, periods=n, freq="1min", tz="UTC")
    return pd.DataFrame({
        "ts_event": ts,
        "price": np.linspace(2400.0, 2400.3, n),
        "size": [1, 2, 3, 4][:n],
        "side": ["B", "A", "N", "B"][:n],
        "instrument_id": [42] * n,
    }, index=ts + pd.Timedelta("1ms"))


class FakeClient:
    """Imite db.Historical : metadata.get_cost et timeseries.get_range."""

    def __init__(self, cost_per_chunk: float):
        self.cost_per_chunk = cost_per_chunk
        self.downloads = []
        client = self

        class Metadata:
            def get_cost(self, **kw):
                return client.cost_per_chunk

        class Store:
            def __init__(self, df):
                self.df = df

            def to_df(self):
                return self.df

        class Timeseries:
            def get_range(self, **kw):
                client.downloads.append((kw["start"], kw["end"]))
                return Store(_raw_trades(str(kw["start"])))

        self.metadata, self.timeseries = Metadata(), Timeseries()


def test_month_chunks_split_and_completeness():
    chunks = store.month_chunks("2026-01-15", "2026-03-10")
    assert [f"{c.month:%Y-%m}" for c in chunks] == ["2026-01", "2026-02", "2026-03"]
    assert [c.complete for c in chunks] == [False, True, False]
    assert chunks[0].start == pd.Timestamp("2026-01-15", tz="UTC")
    assert chunks[-1].end == pd.Timestamp("2026-03-10", tz="UTC")


def test_month_chunks_accepts_tz_aware():
    assert len(store.month_chunks(pd.Timestamp("2026-01-01", tz="UTC"), "2026-02-01")) == 1


def test_normalize_trades_maps_aggressor():
    df = gc.normalize_trades(_raw_trades("2026-01-02"))
    assert df["aggressor"].tolist() == [1, -1, 0, 1]
    assert df.index.name == "ts_event" and str(df.index.tz) == "UTC"
    assert df["size"].dtype == "int32"


def test_cost_guard_blocks_everything(cfg):
    client = FakeClient(cost_per_chunk=6.0)
    with pytest.raises(gc.CostLimitExceeded):
        gc.fetch(cfg, client, "2026-01-01", "2026-03-01", max_cost=10.0)  # 2 × 6 $ > 10 $
    assert client.downloads == []


def test_dry_run_downloads_nothing(cfg):
    client = FakeClient(cost_per_chunk=1.0)
    assert gc.fetch(cfg, client, "2026-01-01", "2026-03-01", max_cost=10.0, dry_run=True) == 2.0
    assert client.downloads == []


def test_complete_months_are_cached(cfg):
    client = FakeClient(cost_per_chunk=1.0)
    gc.fetch(cfg, client, "2026-01-01", "2026-02-10", max_cost=10.0)
    assert len(client.downloads) == 2
    client.downloads.clear()
    # janvier est complet donc en cache ; février partiel est re-téléchargé
    gc.fetch(cfg, client, "2026-01-01", "2026-02-10", max_cost=10.0)
    assert client.downloads == [(pd.Timestamp("2026-02-01", tz="UTC"), pd.Timestamp("2026-02-10", tz="UTC"))]

    df = store.load_range(store.gc_trades_dir(cfg), "2026-01-01", "2026-03-01")
    assert len(df) == 8 and df.index.is_monotonic_increasing


def test_load_range_missing_raises(cfg):
    with pytest.raises(FileNotFoundError):
        store.load_range(store.gc_trades_dir(cfg), "2020-01-01", "2020-02-01")


def _rate(server_time: str) -> dict:
    return {"time": int(pd.Timestamp(server_time).timestamp()), "open": 1.0, "high": 2.0, "low": 0.5,
            "close": 1.5, "tick_volume": 10, "spread": 20, "real_volume": 0}


@pytest.mark.parametrize("server_time, utc", [
    ("2026-01-05 10:00", "2026-01-05 08:00"),   # hiver : UTC+2
    ("2026-07-06 10:00", "2026-07-06 07:00"),   # été : UTC+3
    # entre le passage à l'heure d'été US (8 mars) et européen (29 mars) :
    # FTMO est déjà en UTC+3 (Athènes serait encore en UTC+2)
    ("2026-03-16 10:00", "2026-03-16 07:00"),
    # entre la fin de l'heure d'été européenne (26 oct.) et US (2 nov.) : encore UTC+3
    ("2025-10-28 10:00", "2025-10-28 07:00"),
])
def test_normalize_rates_ny_plus_7(server_time, utc):
    df = xauusd.normalize_rates([_rate(server_time)], "America/New_York+7")
    assert df.index[0] == pd.Timestamp(utc, tz="UTC")
    assert list(df.columns) == ["open", "high", "low", "close", "tick_volume", "spread"]


def test_plain_timezone_still_supported():
    df = xauusd.normalize_rates([_rate("2026-01-05 10:00")], "Europe/Athens")
    assert df.index[0] == pd.Timestamp("2026-01-05 08:00", tz="UTC")


def test_utc_to_server_round_trip():
    t = pd.Timestamp("2026-03-16 07:00", tz="UTC")
    assert xauusd.utc_to_server(t, "America/New_York+7") == pd.Timestamp("2026-03-16 10:00")


def test_load_range_bars_drops_duplicate_minutes_across_months(cfg):
    d = store.xauusd_m1_dir(cfg)
    a = pd.DataFrame({"close": [1.0, 2.0]}, index=pd.DatetimeIndex(
        ["2026-01-31 23:59", "2026-02-01 00:00"], tz="UTC", name="time"))
    b = pd.DataFrame({"close": [2.0, 3.0]}, index=pd.DatetimeIndex(
        ["2026-02-01 00:00", "2026-02-01 00:01"], tz="UTC", name="time"))
    store.save(a, store.month_file(d, pd.Timestamp("2026-01-01", tz="UTC")))
    store.save(b, store.month_file(d, pd.Timestamp("2026-02-01", tz="UTC")))
    df = store.load_range(d, "2026-01-31", "2026-02-02", index_name="time", bars=True)
    assert df.index.is_unique and len(df) == 3


def test_xauusd_fetch_keeps_only_month_minutes(cfg):
    class FakeMT5:
        TIMEFRAME_M1 = 1

        def copy_rates_range(self, symbol, tf, start, end):
            # comme MT5 : la bougie à la borne de fin est incluse
            return [_rate(str(start)), _rate(str(end))]

        def last_error(self):
            return (0, "ok")

    xauusd.fetch(cfg, FakeMT5(), "2026-01-01", "2026-02-01")
    df = pd.read_parquet(store.month_file(store.xauusd_m1_dir(cfg), pd.Timestamp("2026-01-01", tz="UTC")))
    assert list(df.index) == [pd.Timestamp("2026-01-01", tz="UTC")]


def test_mt5_time_is_server_wall_clock_marked_utc():
    import datetime as dt

    t = pd.Timestamp("2026-10-23 10:39", tz="UTC")                    # 06:39 New York (EDT)
    d = xauusd.mt5_time(t, "America/New_York+7")
    assert d.tzinfo is not None and d.utcoffset() == dt.timedelta(0)
    assert (d.hour, d.minute) == (13, 39)                             # heure serveur FTMO (NY + 7)
    assert d.timestamp() == pd.Timestamp("2026-10-23 13:39", tz="UTC").timestamp()

