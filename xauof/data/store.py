"""Stockage local Parquet : un fichier par mois, sous data/ (ignoré par git)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from xauof.config import ROOT


def utc(ts) -> pd.Timestamp:
    ts = pd.Timestamp(ts)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


@dataclass
class Chunk:
    month: pd.Timestamp
    start: pd.Timestamp
    end: pd.Timestamp

    @property
    def complete(self) -> bool:
        """Mois entier : mis en cache définitivement. Un mois partiel est re-téléchargé."""
        return self.start == self.month and self.end == self.month + pd.offsets.MonthBegin()


def month_chunks(start, end) -> list[Chunk]:
    start, end = utc(start), utc(end)
    chunks = []
    month = start.normalize().replace(day=1)
    while month < end:
        nxt = month + pd.offsets.MonthBegin()
        chunks.append(Chunk(month, max(start, month), min(end, nxt)))
        month = nxt
    return chunks


def data_dir(cfg: dict) -> Path:
    return ROOT / cfg["data"]["dir"]


def gc_trades_dir(cfg: dict) -> Path:
    return data_dir(cfg) / "gc" / "trades"


def xauusd_m1_dir(cfg: dict) -> Path:
    return data_dir(cfg) / "xauusd" / "m1"


def month_file(directory: Path, month: pd.Timestamp) -> Path:
    return directory / f"{month:%Y-%m}.parquet"


def save(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)


def load_range(directory: Path, start, end, index_name: str = "ts_event", bars: bool = False) -> pd.DataFrame:
    """Concatène les fichiers mensuels couvrant [start, end) et découpe.

    bars=True (bougies) : une minute présente dans deux fichiers mensuels n'est gardée
    qu'une fois. Jamais pour des transactions, qui peuvent partager un horodatage."""
    start, end = utc(start), utc(end)
    months = pd.date_range(start.normalize().replace(day=1), end, freq="MS")
    flt = [(index_name, ">=", start), (index_name, "<", end)]
    frames = [pd.read_parquet(p, filters=flt) for m in months if (p := month_file(directory, m)).exists()]
    if not frames:
        raise FileNotFoundError(f"aucune donnée dans {directory} pour {start:%Y-%m-%d} → {end:%Y-%m-%d}")
    df = pd.concat(frames).sort_index()
    if bars:
        df = df[~df.index.duplicated(keep="last")]
    return df
