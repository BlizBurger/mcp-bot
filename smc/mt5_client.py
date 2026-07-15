"""Connexion MT5 robuste : reconnexion automatique, erreurs explicites.

Le package `MetaTrader5` n'existe que sous Windows avec un terminal MT5
installé — l'import est donc paresseux pour que les tests unitaires et le
dashboard restent utilisables ailleurs.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

import pandas as pd

log = logging.getLogger("smc.mt5")

_TIMEFRAME_NAMES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30,
                    "H1": 16385, "H4": 16388, "D1": 16408}


class MT5Error(RuntimeError):
    pass


class MT5Client:
    """Enveloppe autour de MetaTrader5 avec reconnexion automatique."""

    RETRIES = 4
    BACKOFF = 2  # 2s, 4s, 8s, 16s

    def __init__(self, env: dict):
        self.env = env
        self._mt5 = None

    @property
    def mt5(self):
        if self._mt5 is None:
            try:
                import MetaTrader5 as mt5  # type: ignore
            except ImportError as exc:
                raise MT5Error(
                    "Le package MetaTrader5 n'est pas disponible. Il ne "
                    "fonctionne que sous Windows avec un terminal MT5 installé."
                ) from exc
            self._mt5 = mt5
        return self._mt5

    # -- connexion ----------------------------------------------------------

    def connect(self) -> None:
        kwargs = {}
        if self.env.get("mt5_path"):
            kwargs["path"] = self.env["mt5_path"]
        if self.env.get("mt5_login"):
            kwargs.update(login=int(self.env["mt5_login"]),
                          password=self.env["mt5_password"],
                          server=self.env["mt5_server"])
        for attempt in range(1, self.RETRIES + 1):
            if self.mt5.initialize(**kwargs):
                info = self.mt5.terminal_info()
                log.info("Connecté à MT5 (%s, connecté au broker: %s)",
                         getattr(info, "name", "?"), getattr(info, "connected", "?"))
                return
            log.warning("Échec initialize MT5 (tentative %d/%d) : %s",
                        attempt, self.RETRIES, self.mt5.last_error())
            if attempt < self.RETRIES:
                time.sleep(self.BACKOFF ** attempt)
        raise MT5Error(f"Impossible d'initialiser MT5 : {self.mt5.last_error()}")

    def is_connected(self) -> bool:
        try:
            info = self.mt5.terminal_info()
            return bool(info and info.connected)
        except Exception:
            return False

    def ensure_connected(self) -> None:
        """Reconnecte si la liaison est tombée (nuit, coupure réseau...)."""
        if not self.is_connected():
            log.warning("Connexion MT5 perdue — tentative de reconnexion")
            try:
                self.mt5.shutdown()
            except Exception:
                pass
            self.connect()

    def shutdown(self) -> None:
        if self._mt5 is not None:
            self._mt5.shutdown()

    # -- données ------------------------------------------------------------

    def get_rates(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        """Bougies récentes en DataFrame (time, open, high, low, close,
        tick_volume). Reconnexion + retry en cas d'échec."""
        tf = _TIMEFRAME_NAMES[timeframe.upper()]
        last_exc: Exception | None = None
        for attempt in range(1, self.RETRIES + 1):
            try:
                self.ensure_connected()
                self.mt5.symbol_select(symbol, True)
                rates = self.mt5.copy_rates_from_pos(symbol, tf, 0, count)
                if rates is not None and len(rates) > 0:
                    df = pd.DataFrame(rates)
                    df["time"] = pd.to_datetime(df["time"], unit="s")
                    return df[["time", "open", "high", "low", "close",
                               "tick_volume"]].reset_index(drop=True)
                last_exc = MT5Error(f"copy_rates_from_pos vide : {self.mt5.last_error()}")
            except Exception as exc:  # noqa: BLE001 — on veut survivre à tout
                last_exc = exc
            log.warning("get_rates(%s %s) tentative %d/%d : %s",
                        symbol, timeframe, attempt, self.RETRIES, last_exc)
            if attempt < self.RETRIES:
                time.sleep(self.BACKOFF ** attempt)
        raise MT5Error(f"get_rates({symbol}) en échec : {last_exc}")

    def get_rates_range(self, symbol: str, timeframe: str,
                        start: datetime, end: datetime) -> pd.DataFrame:
        """Historique borné pour le backtest."""
        tf = _TIMEFRAME_NAMES[timeframe.upper()]
        self.ensure_connected()
        self.mt5.symbol_select(symbol, True)
        rates = self.mt5.copy_rates_range(symbol, tf, start, end)
        if rates is None or len(rates) == 0:
            raise MT5Error(f"Pas d'historique {symbol} {timeframe} "
                           f"({start} → {end}) : {self.mt5.last_error()}")
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        return df[["time", "open", "high", "low", "close",
                   "tick_volume"]].reset_index(drop=True)

    def pip_size(self, symbol: str) -> float:
        """Taille de pip d'après le broker (10 × point pour les cotations à
        5/3 décimales)."""
        info = self.mt5.symbol_info(symbol)
        if info is None:
            raise MT5Error(f"symbol_info({symbol}) introuvable")
        return info.point * 10 if info.digits in (3, 5) else info.point

    def pip_value_per_lot(self, symbol: str, pip: float) -> float:
        """Valeur d'un pip pour 1.0 lot, dans la DEVISE DU COMPTE (calculée par
        le broker via trade_tick_value / trade_tick_size)."""
        info = self.mt5.symbol_info(symbol)
        if info is None or not getattr(info, "trade_tick_size", 0):
            raise MT5Error(f"valeur de pip indisponible pour {symbol}")
        return info.trade_tick_value * (pip / info.trade_tick_size)

    def volume_specs(self, symbol: str) -> dict:
        """Pas / min / max de volume du broker pour arrondir le lot."""
        info = self.mt5.symbol_info(symbol)
        if info is None:
            raise MT5Error(f"symbol_info({symbol}) introuvable")
        return {"volume_step": info.volume_step or 0.01,
                "volume_min": info.volume_min or 0.01,
                "volume_max": info.volume_max or 100.0}
