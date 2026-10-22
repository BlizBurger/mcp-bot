"""Historique des transactions GC (or COMEX) via Databento, avec plafond de coût.

Chaque transaction : prix, taille, côté agresseur (+1 acheteur, -1 vendeur,
0 inconnu) et instrument_id (change au roulement de contrat).

Usage :
    python -m xauof.data.gc --start 2025-10-01 --dry-run   # estimation seule
    python -m xauof.data.gc --start 2025-10-01             # téléchargement
"""
from __future__ import annotations

import argparse
import warnings

import pandas as pd

from xauof.config import load_config, secret
from xauof.data import store

# Databento : 'B' = agresseur acheteur, 'A' = agresseur vendeur, 'N' = aucun
_AGGRESSOR = {"B": 1, "A": -1}


class CostLimitExceeded(RuntimeError):
    pass


def normalize_trades(raw: pd.DataFrame) -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "price": raw["price"].astype("float64").to_numpy(),
            "size": raw["size"].astype("int32").to_numpy(),
            "aggressor": raw["side"].map(_AGGRESSOR).fillna(0).astype("int8").to_numpy(),
            "instrument_id": raw["instrument_id"].astype("uint32").to_numpy(),
        },
        index=pd.DatetimeIndex(pd.to_datetime(raw["ts_event"], utc=True), name="ts_event"),
    )
    return df.sort_index()


def _request(cfg: dict, chunk: store.Chunk) -> dict:
    return dict(
        dataset=cfg["symbols"]["dataset"],
        symbols=[cfg["symbols"]["futures"]],
        stype_in="continuous",
        schema=cfg["data"]["schema"],
        start=chunk.start,
        end=chunk.end,
    )


def fetch(cfg: dict, client, start, end, max_cost: float, dry_run: bool = False) -> float:
    """Télécharge les mois manquants. Refuse tout si le coût estimé dépasse max_cost (USD).

    Retourne le coût estimé des mois à télécharger.
    """
    directory = store.gc_trades_dir(cfg)
    todo = [c for c in store.month_chunks(start, end)
            if not (c.complete and store.month_file(directory, c.month).exists())]
    if not todo:
        print("Tout est déjà en cache, rien à télécharger.")
        return 0.0

    costs = [client.metadata.get_cost(**_request(cfg, c)) for c in todo]
    total = sum(costs)
    for c, cost in zip(todo, costs):
        print(f"  {c.start:%Y-%m-%d} → {c.end:%Y-%m-%d} : {cost:.2f} $")
    print(f"Coût estimé total : {total:.2f} $ (plafond {max_cost:.2f} $)")

    if total > max_cost:
        raise CostLimitExceeded(
            f"{total:.2f} $ > plafond {max_cost:.2f} $ : rien n'a été téléchargé. "
            "Réduis la période ou relance avec --max-cost."
        )
    if dry_run:
        print("Mode --dry-run : rien n'a été téléchargé.")
        return total

    for c in todo:
        raw = client.timeseries.get_range(**_request(cfg, c)).to_df()
        df = normalize_trades(raw)
        store.save(df, store.month_file(directory, c.month))
        print(f"  {c.month:%Y-%m} : {len(df):,} transactions enregistrées")
    return total


def main() -> None:
    cfg = load_config()
    p = argparse.ArgumentParser(description="Télécharge l'historique GC trades (Databento)")
    p.add_argument("--start", required=True, help="date de début, ex. 2025-10-01")
    p.add_argument("--end", default=None, help="date de fin exclue (défaut : aujourd'hui 00:00 UTC)")
    p.add_argument("--max-cost", type=float, default=cfg["data"]["max_cost_usd"],
                   help="plafond de dépense en USD pour ce lancement")
    p.add_argument("--dry-run", action="store_true", help="estime le coût sans télécharger")
    args = p.parse_args()

    import databento as db

    # bruit interne à databento avec pandas récent, sans effet sur les données
    warnings.filterwarnings("ignore", message=".*inplace keyword.*")

    end = args.end or pd.Timestamp.now(tz="UTC").normalize()
    client = db.Historical(secret("DATABENTO_API_KEY"))
    try:
        fetch(cfg, client, args.start, end, args.max_cost, args.dry_run)
    except CostLimitExceeded as e:
        raise SystemExit(f"STOP : {e}")
    except db.BentoClientError as e:
        if e.http_status == 402:
            raise SystemExit(
                "STOP : Databento refuse la requête (402, budget insuffisant). "
                "La limite de dépense du compte (Billing → Usage-based access → Manage) "
                "est plus basse que le coût estimé : relève-la au-dessus de ce coût. "
                "Les mois déjà enregistrés sont conservés."
            )
        raise
    except db.BentoError as e:
        # coupure réseau en cours de téléchargement : pas de relance automatique, pour ne
        # jamais payer deux fois une requête interrompue
        raise SystemExit(
            f"STOP : téléchargement interrompu ({e}). Les mois déjà enregistrés sont conservés ; "
            "relance la même commande pour reprendre là où ça s'est arrêté."
        )


if __name__ == "__main__":
    main()
