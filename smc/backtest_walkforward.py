"""Validation walk-forward multi-fenêtres branchée sur le vrai moteur.

Remplace le split unique 75/25 par : plusieurs fenêtres train→test glissantes,
un intervalle de confiance bootstrap sur l'espérance OOS, une vérification de
consistance inter-fenêtres, et un bloc "vault" scellé jamais utilisé pour
choisir quoi que ce soit.

Usage :
  python -m smc.backtest_walkforward --train-months 6 --test-months 2 \
      --step-months 2 --vault-months 3 --years 4

Le vault n'est évalué QUE si tu passes --vault-strategy NOM (une seule fois,
sur une stratégie déjà choisie d'après le comparatif walk-forward).
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from smc.backtest import simulate_all
from smc.config import load_config, load_env, pip_size_fallback
from smc.logging_setup import log_warnings_banner, setup_logging
from smc.mt5_client import MT5Client
from smc.strategies import STRATEGIES
from smc.walkforward import (
    TradeResult, build_windows, compare_strategies_safely, evaluate_on_vault,
)

log = logging.getLogger("smc.walkforward")

# Warmup (chauffe) à préfixer avant chaque fenêtre pour que les indicateurs
# et la structure aient assez d'historique. Généreux (jours calendaires).
_WARMUP = {"ltf": timedelta(days=20), "htf": timedelta(days=60),
           "d1": timedelta(days=280)}

# Grilles de paramètres — testées UNIQUEMENT sur le train de chaque fenêtre.
# Clés = (section_config, clé). Petites grilles exprès (comparaisons multiples).
_PARAM_GRIDS: dict[str, list[dict] | None] = {
    "sweep_bos": [
        {("sweep_bos", "min_wick_ratio"): 0.5, ("sweep_bos", "sl_buffer_pct"): 0.15,
         ("sweep_bos", "min_rr"): 1.5},
        {("sweep_bos", "min_wick_ratio"): 0.6, ("sweep_bos", "sl_buffer_pct"): 0.20,
         ("sweep_bos", "min_rr"): 2.0},
        {("sweep_bos", "min_wick_ratio"): 0.6, ("sweep_bos", "sl_buffer_pct"): 0.25,
         ("sweep_bos", "min_rr"): 2.5},
    ],
    "donchian": [
        {("donchian", "channel"): 20, ("donchian", "atr_sl_mult"): 1.5,
         ("donchian", "risk_reward"): 2.0},
        {("donchian", "channel"): 55, ("donchian", "atr_sl_mult"): 2.0,
         ("donchian", "risk_reward"): 2.0},
    ],
    "ema_rsi": None, "bollinger": None, "ema_cross": None, "amd_asian": None,
}


# --------------------------------------------------------------------------
# Chargement des données (une seule fois sur toute la plage)
# --------------------------------------------------------------------------

def fetch_full(cfg: dict, pairs: list[str], start: datetime,
               end: datetime) -> tuple[dict, datetime, datetime]:
    """Charge H4/M15/D1 de toutes les paires sur [start, end]. Retourne aussi
    la plage réellement disponible (le broker peut ne pas remonter aussi loin)."""
    env = load_env()
    client = MT5Client(env)
    client.connect()
    full: dict = {}
    real_start, real_end = end, start
    try:
        for pair in pairs:
            log.info("Chargement %s (%s → %s)...", pair, start.date(), end.date())
            try:
                pip = client.pip_size(pair)
            except Exception:
                pip = pip_size_fallback(pair, cfg)
            try:
                htf = client.get_rates_range(pair, cfg["timeframes"]["htf"], start, end)
                ltf = client.get_rates_range(pair, cfg["timeframes"]["ltf"], start, end)
                d1 = client.get_rates_range(pair, "D1", start, end)
            except Exception as exc:  # noqa: BLE001 — paire absente/insuffisante
                log.warning("%s ignoré : %s", pair, exc)
                continue
            full[pair] = {"pip": pip, "htf": htf, "ltf": ltf, "d1": d1}
            real_start = min(real_start, ltf["time"].iloc[0])
            real_end = max(real_end, ltf["time"].iloc[-1])
    finally:
        client.shutdown()
    return full, real_start, real_end


def make_data_loader(full: dict):
    """Renvoie loader(start, end) qui découpe les données pré-chargées, en
    préfixant un warmup pour les indicateurs. Le résultat porte les bornes de
    la fenêtre pour que la stratégie ne compte que les trades ouverts dedans."""
    def loader(start: datetime, end: datetime) -> dict:
        sliced = {}
        for pair, d in full.items():
            sub = {}
            for tf, warm in (("htf", _WARMUP["htf"]), ("ltf", _WARMUP["ltf"]),
                             ("d1", _WARMUP["d1"])):
                df = d[tf]
                m = (df["time"] >= start - warm) & (df["time"] <= end)
                sub[tf] = df[m].reset_index(drop=True)
            if len(sub["ltf"]) < 60 or len(sub["htf"]) < 30:
                continue
            sliced[pair] = {"pip": d["pip"], **sub}
        return {"pairs": sliced, "start": start, "end": end}
    return loader


def make_strategy_fn(name: str, base_cfg: dict):
    """Renvoie strategy_fn(slice, params) -> list[TradeResult], qui rejoue la
    vraie stratégie via simulate_all et ne garde que les trades OUVERTS dans la
    fenêtre (pas ceux issus du warmup)."""
    def fn(slice_obj: dict, params: dict) -> list[TradeResult]:
        if not slice_obj["pairs"]:
            return []
        vcfg = copy.deepcopy(base_cfg)
        vcfg["strategy_name"] = name
        for (section, key), value in (params or {}).items():
            vcfg.setdefault(section, {})[key] = value
        df = simulate_all(vcfg, slice_obj["pairs"])
        if df.empty:
            return []
        s, e = slice_obj["start"], slice_obj["end"]
        df = df[(df["open_time"] >= s) & (df["open_time"] <= e)]
        return [TradeResult(entry_time=r.open_time, exit_time=r.close_time,
                            r_multiple=float(r.result_r), symbol=r.pair,
                            strategy=name)
                for r in df.itertuples()]
    return fn


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def run(cfg: dict, pairs: list[str], years: int, train_m: int, test_m: int,
        step_m: int, vault_m: int, strat_names: list[str],
        vault_strategy: str | None) -> dict:
    end = datetime.now()
    start = end - timedelta(days=365 * years + 40)
    full, real_start, real_end = fetch_full(cfg, pairs, start, end)
    if not full:
        return {"erreur": "aucune donnée chargée"}

    windows, (vault_start, vault_end) = build_windows(
        real_start.to_pydatetime(), real_end.to_pydatetime(),
        train_months=train_m, test_months=test_m, step_months=step_m,
        vault_months=vault_m)

    log.info("Plage réelle : %s → %s | %d fenêtres walk-forward | vault %s → %s",
             real_start.date(), real_end.date(), len(windows),
             vault_start.date(), vault_end.date())
    if len(windows) < 8:
        log.warning("⚠️ Seulement %d fenêtres : historique trop court pour une "
                    "conclusion statistiquement solide (viser 10-15). Résultats "
                    "à prendre avec des pincettes.", len(windows))

    loader = make_data_loader(full)
    strategies = {name: (make_strategy_fn(name, cfg), _PARAM_GRIDS.get(name))
                  for name in strat_names if name in STRATEGIES}

    comparison = compare_strategies_safely(strategies, loader, windows)
    report = {
        "genere_le": datetime.now().isoformat(),
        "plage_donnees": f"{real_start.date()} → {real_end.date()}",
        "n_fenetres": len(windows),
        "decoupage": f"train {train_m}m / test {test_m}m / step {step_m}m / vault {vault_m}m",
        "vault": f"{vault_start.date()} → {vault_end.date()} (scellé)",
        **comparison,
    }

    # Vault : UNIQUEMENT si demandé explicitement, sur une seule stratégie
    if vault_strategy:
        if vault_strategy not in strategies:
            report["vault_result"] = {"erreur": f"{vault_strategy} non testée"}
        else:
            log.warning("Évaluation VAULT sur %s — une seule fois, verdict final.",
                        vault_strategy)
            fn = strategies[vault_strategy][0]
            report["vault_result"] = evaluate_on_vault(
                fn, {}, loader, vault_start.to_pydatetime(),
                vault_end.to_pydatetime(), strategy_name=vault_strategy)
    return report


def _print_report(report: dict) -> None:
    print("\n===== COMPARATIF WALK-FORWARD (multi-fenêtres, out-of-sample) =====")
    print(f"Plage : {report.get('plage_donnees')} | {report.get('n_fenetres')} "
          f"fenêtres | {report.get('decoupage')}")
    print(f"Vault scellé : {report.get('vault')}\n")
    for name, s in report.get("comparatif", {}).items():
        print(f"--- {name} ---")
        for k, v in s.items():
            print(f"  {k}: {v}")
        print()
    if report.get("avertissement_comparaisons_multiples"):
        print(report["avertissement_comparaisons_multiples"], "\n")
    if "vault_result" in report:
        print("===== VAULT (verdict final, jamais touché avant) =====")
        for k, v in report["vault_result"].items():
            print(f"  {k}: {v}")


def main() -> None:
    cfg = load_config()
    setup_logging(cfg["paths"]["logs"], "walkforward")
    log_warnings_banner(log)

    p = argparse.ArgumentParser(description="Validation walk-forward multi-fenêtres")
    p.add_argument("--walkforward", action="store_true", help="(implicite)")
    p.add_argument("--years", type=int, default=4, help="profondeur d'historique visée")
    p.add_argument("--train-months", type=int, default=6)
    p.add_argument("--test-months", type=int, default=2)
    p.add_argument("--step-months", type=int, default=2)
    p.add_argument("--vault-months", type=int, default=3)
    p.add_argument("--pairs", type=str, default=",".join(cfg["pairs"]))
    p.add_argument("--strategies", type=str,
                   default="sweep_bos,donchian,ema_rsi,bollinger,ema_cross,amd_asian")
    p.add_argument("--vault-strategy", type=str, default=None,
                   help="évalue le vault sur CETTE stratégie (une seule fois)")
    args = p.parse_args()

    pairs = [x.strip().upper() for x in args.pairs.split(",") if x.strip()]
    strats = [x.strip() for x in args.strategies.split(",") if x.strip()]

    report = run(cfg, pairs, args.years, args.train_months, args.test_months,
                 args.step_months, args.vault_months, strats, args.vault_strategy)
    _print_report(report)

    out = Path(cfg["paths"]["reports"]).parent / "Backtest" / \
        f"walkforward_{datetime.now():%Y%m%d_%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "rapport.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")
    log.info("Rapport walk-forward écrit : %s", out / "rapport.json")
    log.warning("⚠️ Ne retenir une stratégie que si edge_statistiquement_prouve "
                "== True ET consistance positive. Vault = verdict final unique.")


if __name__ == "__main__":
    main()
