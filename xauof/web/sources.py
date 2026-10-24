"""Sources de données de l'interface : MT5 réel (lecture seule) ou démo synthétique."""
from __future__ import annotations

import time
from typing import Optional

import numpy as np
import pandas as pd

from xauof.assistant import levels as lv


class MT5Source:
    """Broker réel via le terminal MT5 ouvert. Aucune fonction d'ordre."""
    name = "MT5 LIVE"

    def __init__(self, cfg: dict):
        from xauof.assistant import mt5_live

        self.cfg, self.live = cfg, mt5_live
        self.mt5 = mt5_live.connect()

    def now(self) -> pd.Timestamp:
        return pd.Timestamp.now(tz="UTC")

    def bars_m1(self, start, end) -> pd.DataFrame:
        return self.live.xau_m1(self.mt5, self.cfg, start, end)

    def ticks(self, start, end) -> pd.DataFrame:
        from xauof.data.xauusd import mt5_time, server_to_utc

        clock = self.cfg["broker"]["server_clock"]
        parts, a = [], pd.Timestamp(start)
        while a < end:                              # par tranches de 2 h : une longue séance d'un bloc peut échouer
            b = min(a + pd.Timedelta(hours=2), pd.Timestamp(end))
            raw = self.mt5.copy_ticks_range(self.cfg["symbols"]["broker"], mt5_time(a, clock), mt5_time(b, clock),
                                            self.mt5.COPY_TICKS_INFO)
            if raw is not None and len(raw):
                parts.append(pd.DataFrame(raw))
            a = b
        if not parts:
            return pd.DataFrame(columns=["bid", "ask"])
        df = pd.concat(parts, ignore_index=True).drop_duplicates(subset=["time_msc", "bid", "ask"])
        idx = server_to_utc(pd.to_datetime(df["time_msc"], unit="ms"), clock)
        return pd.DataFrame({"bid": df["bid"].to_numpy(), "ask": df["ask"].to_numpy()},
                            index=pd.DatetimeIndex(idx)).dropna().sort_index()

    def account(self, now) -> Optional[dict]:
        return self.live.account(self.mt5, self.cfg, now)

    def tick(self) -> Optional[dict]:
        """Dernier prix en direct (comme l'affiche MT5)."""
        t = self.mt5.symbol_info_tick(self.cfg["symbols"]["broker"])
        return {"bid": float(t.bid), "ask": float(t.ask)} if t is not None and t.bid > 0 else None

    def levels(self, day) -> Optional[dict]:
        return lv.load_levels(self.cfg, day)

    def journal_raw(self, start, end):
        from xauof.data.xauusd import mt5_time

        clock = self.cfg["broker"]["server_clock"]
        a, b = mt5_time(start, clock), mt5_time(end, clock)
        return self.mt5.history_deals_get(a, b) or [], self.mt5.history_orders_get(a, b) or []

    def server_to_utc(self, sec: int) -> pd.Timestamp:
        from xauof.data.xauusd import server_to_utc

        return server_to_utc(pd.Series(pd.to_datetime([sec], unit="s")), self.cfg["broker"]["server_clock"]).iloc[0]


class DemoSource:
    """Marché synthétique rejoué en accéléré (1 s réelle = `speed` s simulées), pour voir
    l'interface marché fermé. Les niveaux « de la veille » sont calculés sur la veille synthétique."""
    name = "DÉMO (données synthétiques)"

    def __init__(self, cfg: dict, day: str = "2026-10-23", start_ny: str = "10:30", speed: float = 20.0,
                 seed: int = 7):
        self.cfg, self.speed = cfg, speed
        self.t0_sim = pd.Timestamp(f"{day} {start_ny}", tz="America/New_York").tz_convert("UTC")
        self.t0_real = time.time()
        rng = np.random.default_rng(seed)
        start = pd.Timestamp(day, tz="America/New_York").tz_convert("UTC") - pd.Timedelta(hours=26)
        minutes = pd.date_range(start, start + pd.Timedelta(hours=40), freq="1min")
        # marche aléatoire TICK PAR TICK (retour lent vers 4040) : les bougies M1 s'enchaînent
        # comme sur un vrai marché, sans trou entre la clôture et l'ouverture suivante
        n = 20                                             # ticks par minute
        tick_t = minutes.repeat(n) + pd.to_timedelta(np.tile(np.arange(n) * 3.0, len(minutes)), unit="s")
        shocks = rng.normal(0, 0.45 / np.sqrt(n), len(tick_t))
        for k in rng.choice(len(minutes), 60, replace=False):        # quelques impulsions (FVG / OB)
            shocks[k * n:(k + 3) * n] += rng.choice([-1, 1]) * rng.uniform(1.5, 3.5) / n
        mid = np.empty(len(tick_t))
        price = 4040.0
        for i, e in enumerate(shocks):
            price += 0.02 / n * (4040.0 - price) + e
            mid[i] = price
        spread = 0.30 + 0.05 * rng.random(len(tick_t))
        self._ticks = pd.DataFrame({"bid": mid - spread / 2, "ask": mid + spread / 2}, index=tick_t)
        bid = self._ticks["bid"].resample("1min").ohlc()
        bid["spread"] = (spread.reshape(-1, n).mean(axis=1) / cfg["broker"]["point"]).round()
        bid["tick_volume"] = n
        self._bars = bid.dropna()
        prev_day = pd.Timestamp(day) - pd.Timedelta(days=1)
        self._levels = self._prev_levels(prev_day)

    def _prev_levels(self, prev_day: pd.Timestamp) -> dict:
        from xauof.indicators import tpo
        from xauof.indicators import volume_profile as vp

        a, b = (pd.Timestamp(f"{prev_day:%Y-%m-%d} {h}", tz="America/New_York").tz_convert("UTC")
                for h in self.cfg["sessions"]["rth"])
        t = self._ticks[(self._ticks.index >= a) & (self._ticks.index < b)]
        trades = pd.DataFrame({"price": ((t["bid"] + t["ask"]) / 2).round(1), "size": 1}, index=t.index)
        p = self.cfg["profile"]
        vol = vp.volume_profile(trades, p["bin_size"], p["value_area_pct"], p["hvn_smooth"], p["lvn_ratio"])
        hz = vp.hvn_zones(vol.profile, p["hvn_smooth"], self.cfg["strategy"]["data_guards"]["hvn_zone_frac"])
        tp = tpo.tpo_profile(trades, p["bin_size"], 30, 60, p["value_area_pct"])
        return {"source_day": f"{prev_day:%Y-%m-%d}", "contract": 0, "basis": 0.0, "poc_v": vol.poc,
                "vah_v": vol.vah, "val_v": vol.val, "poc_t": tp.poc, "vah_t": tp.vah, "val_t": tp.val,
                "hvn": hz, "lvn": vol.lvn, "singles": tp.single_prints,
                "profil_volume": [(float(k), float(v)) for k, v in vol.profile.items()],
                "profil_tpo": [(float(k), int(v)) for k, v in tp.counts.items()]}

    def now(self) -> pd.Timestamp:
        return (self.t0_sim + pd.Timedelta(seconds=(time.time() - self.t0_real) * self.speed)).floor("s")

    def bars_m1(self, start, end) -> pd.DataFrame:
        now = self.now()
        b = self._bars[(self._bars.index >= start) & (self._bars.index < min(end, now))]
        return b[b.index + pd.Timedelta(minutes=1) <= now]

    def ticks(self, start, end) -> pd.DataFrame:
        t = self._ticks
        return t[(t.index >= start) & (t.index < min(end, self.now()))]

    def account(self, now) -> Optional[dict]:
        return {"balance": 10040.0, "equity": 10012.5, "realized_today": 40.0, "trades_today": 1,
                "realized_week": -95.0}

    def tick(self) -> Optional[dict]:
        t = self._ticks[self._ticks.index <= self.now()]
        return None if t.empty else {"bid": float(t["bid"].iloc[-1]), "ask": float(t["ask"].iloc[-1])}

    def levels(self, day) -> Optional[dict]:
        return self._levels

    def journal_raw(self, start, end):
        deals = [
            {"symbol": "XAUUSD", "position_id": 101, "entry": 0, "type": 0, "volume": 0.10, "price": 4031.2,
             "profit": 0.0, "commission": -0.3, "swap": 0.0, "fee": 0.0, "time": 1_792_000_000},
            {"symbol": "XAUUSD", "position_id": 101, "entry": 1, "type": 1, "volume": 0.10, "price": 4037.9,
             "profit": 67.0, "commission": -0.3, "swap": 0.0, "fee": 0.0, "time": 1_792_003_000},
            {"symbol": "XAUUSD", "position_id": 102, "entry": 0, "type": 1, "volume": 0.08, "price": 4049.5,
             "profit": 0.0, "commission": -0.24, "swap": 0.0, "fee": 0.0, "time": 1_792_090_000},
            {"symbol": "XAUUSD", "position_id": 102, "entry": 1, "type": 0, "volume": 0.08, "price": 4053.8,
             "profit": -34.4, "commission": -0.24, "swap": 0.0, "fee": 0.0, "time": 1_792_092_000},
        ]
        orders = [{"position_id": 101, "sl": 4027.0, "time_setup": 1_792_000_000},
                  {"position_id": 102, "sl": 4054.0, "time_setup": 1_792_090_000}]
        return deals, orders

    def server_to_utc(self, sec: int) -> pd.Timestamp:
        return pd.Timestamp(sec, unit="s", tz="UTC")
