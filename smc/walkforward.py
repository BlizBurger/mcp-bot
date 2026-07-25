"""
walkforward.py — Moteur de walk-forward multi-fenêtres
=========================================================
Objectif : remplacer le split unique 75/25 par une validation beaucoup plus
rigoureuse, avant de faire confiance à N'IMPORTE QUEL résultat de backtest
(le vôtre comme celui de votre ami).

Pourquoi un split unique ne suffit pas
---------------------------------------
- Une seule fenêtre OOS (3 mois par ex.) est un échantillon bruité : un
  résultat "plat" ou "excellent" peut n'être qu'un coup de chance sur CETTE
  période précise.
- Si vous testez plusieurs stratégies/configs sur le MÊME split, vous
  retombez dans le problème des comparaisons multiples : sur assez de
  variantes, l'une d'elles aura l'air excellente par pur hasard, même sans
  edge réel (cf. "39 bots testés en une soirée" → normal qu'un ressorte bien).

Ce que fait ce moteur
----------------------
1. Découpe l'historique en plusieurs fenêtres glissantes (train → test),
   pas une seule.
2. Sur CHAQUE fenêtre : les paramètres (si optimisés) ne sont choisis QUE sur
   le train ; le test ne sert jamais à choisir quoi que ce soit → zéro fuite.
3. Agrège les résultats OOS de toutes les fenêtres → distribution de
   performance, pas un seul chiffre.
4. Vérifie la CONSISTANCE entre fenêtres (edge stable vs signe qui change
   tout le temps = probablement du bruit).
5. Calcule un intervalle de confiance par bootstrap sur l'espérance globale.
6. Réserve un dernier bloc "vault" JAMAIS touché pendant toute l'exploration
   (même pas en test walk-forward) — le seul verdict final valide, à n'ouvrir
   qu'une fois la méthodologie arrêtée.

Interface attendue de votre côté
----------------------------------
Une fonction `strategy_fn(df_slice, params) -> list[TradeResult]` par
stratégie (sweep_bos, donchian, etc.), qui rejoue UNIQUEMENT sur les bougies
du df_slice fourni (pas d'accès à des données hors de cette fenêtre).
C'est la même logique que votre backtest.py actuel, juste appelée fenêtre
par fenêtre au lieu d'une fois sur tout l'historique.
"""

from __future__ import annotations

import random
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Optional


# ------------------------- STRUCTURES -------------------------

@dataclass
class TradeResult:
    """Un trade fermé, format minimal attendu depuis vos stratégies."""
    entry_time: datetime
    exit_time: datetime
    r_multiple: float          # gain/perte exprimé en multiples de R
    symbol: str = ""
    strategy: str = ""


@dataclass
class Window:
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    index: int


@dataclass
class WindowResult:
    window: Window
    trades: list = field(default_factory=list)
    params_used: dict = field(default_factory=dict)

    @property
    def n_trades(self) -> int:
        return len(self.trades)

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        wins = sum(1 for t in self.trades if t.r_multiple > 0)
        return wins / len(self.trades) * 100

    @property
    def expectancy(self) -> float:
        if not self.trades:
            return 0.0
        return statistics.mean(t.r_multiple for t in self.trades)

    @property
    def total_r(self) -> float:
        return sum(t.r_multiple for t in self.trades)


# ------------------------- DÉCOUPAGE DES FENÊTRES -------------------------

def build_windows(
    data_start: datetime,
    data_end: datetime,
    train_months: int = 6,
    test_months: int = 2,
    step_months: int = 2,
    vault_months: int = 3,
) -> tuple[list[Window], tuple[datetime, datetime]]:
    """
    Découpe [data_start, data_end] en fenêtres glissantes train->test,
    en réservant les `vault_months` derniers mois comme bloc final jamais
    touché (le "vault").

    Retourne (liste_de_fenêtres, (vault_start, vault_end)).
    """
    vault_start = data_end - timedelta(days=30 * vault_months)
    explorable_end = vault_start  # tout ce qui suit est intouchable

    windows = []
    cursor = data_start
    idx = 0

    while True:
        train_end = cursor + timedelta(days=30 * train_months)
        test_end = train_end + timedelta(days=30 * test_months)

        if test_end > explorable_end:
            break

        windows.append(Window(
            train_start=cursor, train_end=train_end,
            test_start=train_end, test_end=test_end,
            index=idx,
        ))
        idx += 1
        cursor = cursor + timedelta(days=30 * step_months)

    return windows, (vault_start, data_end)


# ------------------------- MOTEUR PRINCIPAL -------------------------

def run_walkforward(
    strategy_fn: Callable,
    data_loader: Callable[[datetime, datetime], "object"],
    windows: list[Window],
    param_grid: Optional[list[dict]] = None,
    selection_metric: str = "expectancy",
    strategy_name: str = "",
) -> list[WindowResult]:
    """
    Pour chaque fenêtre :
      1. Si param_grid fourni : teste chaque combinaison de paramètres
         UNIQUEMENT sur le segment train, garde la meilleure selon
         `selection_metric`.
      2. Si pas de param_grid (params fixes) : saute direct au test.
      3. Évalue (une seule fois) sur le segment test avec les paramètres
         retenus → c'est ce résultat, et seulement celui-là, qui compte.

    strategy_fn(df_slice, params) -> list[TradeResult]
    data_loader(start, end) -> df_slice (à vous de brancher MT5/CSV ici)
    """
    results = []

    for window in windows:
        best_params = {}

        if param_grid:
            best_score = float("-inf")
            train_df = data_loader(window.train_start, window.train_end)

            for params in param_grid:
                trades = strategy_fn(train_df, params)
                if not trades:
                    continue
                score = (
                    statistics.mean(t.r_multiple for t in trades)
                    if selection_metric == "expectancy"
                    else sum(t.r_multiple for t in trades)
                )
                if score > best_score:
                    best_score = score
                    best_params = params

        test_df = data_loader(window.test_start, window.test_end)
        test_trades = strategy_fn(test_df, best_params)
        for t in test_trades:
            t.strategy = strategy_name

        results.append(WindowResult(window=window, trades=test_trades, params_used=best_params))

    return results


# ------------------------- AGRÉGATION & STATS -------------------------

def bootstrap_expectancy_ci(all_trades: list, n_iterations: int = 2000, ci: float = 0.95) -> tuple[float, float, float]:
    """
    Intervalle de confiance par bootstrap sur l'espérance (moyenne des R).
    Retourne (moyenne_observée, borne_basse, borne_haute).
    Répond à la question : "cette espérance est-elle fiable, ou l'intervalle
    inclut-il 0 (= pas de preuve d'edge) ?"
    """
    r_values = [t.r_multiple for t in all_trades]
    if len(r_values) < 10:
        return (statistics.mean(r_values) if r_values else 0.0, float("nan"), float("nan"))

    observed = statistics.mean(r_values)
    boot_means = []
    n = len(r_values)

    for _ in range(n_iterations):
        sample = [random.choice(r_values) for _ in range(n)]
        boot_means.append(statistics.mean(sample))

    boot_means.sort()
    lower_idx = int((1 - ci) / 2 * n_iterations)
    upper_idx = int((1 - (1 - ci) / 2) * n_iterations) - 1

    return (observed, boot_means[lower_idx], boot_means[upper_idx])


def consistency_check(window_results: list[WindowResult]) -> dict:
    """
    Vérifie si l'edge est stable entre fenêtres ou si le signe/l'ampleur
    varie sans queue ni tête (signe de bruit plutôt que de vrai edge).
    """
    expectancies = [w.expectancy for w in window_results if w.n_trades > 0]
    if len(expectancies) < 2:
        return {"verdict": "pas assez de fenêtres avec des trades pour juger"}

    positive_windows = sum(1 for e in expectancies if e > 0)
    total = len(expectancies)
    std_dev = statistics.stdev(expectancies)
    mean_exp = statistics.mean(expectancies)

    coefficient_variation = abs(std_dev / mean_exp) if mean_exp != 0 else float("inf")

    if positive_windows == total:
        verdict = "Consistant positif — bon signe"
    elif positive_windows == 0:
        verdict = "Consistant négatif — abandonner cette stratégie"
    elif positive_windows / total >= 0.7:
        verdict = "Majoritairement positif mais pas unanime — prudence"
    else:
        verdict = "Signe instable entre fenêtres — ressemble à du bruit, pas à un edge"

    return {
        "fenetres_positives": f"{positive_windows}/{total}",
        "expectancy_moyenne_inter_fenetres": round(mean_exp, 3),
        "ecart_type_inter_fenetres": round(std_dev, 3),
        "coefficient_variation": round(coefficient_variation, 2) if coefficient_variation != float("inf") else "inf",
        "verdict": verdict,
    }


def summarize_walkforward(window_results: list[WindowResult], strategy_name: str = "") -> dict:
    all_trades = [t for w in window_results for t in w.trades]

    if not all_trades:
        return {"strategy": strategy_name, "verdict": "Aucun trade généré sur les fenêtres OOS — rien à évaluer"}

    wins = sum(1 for t in all_trades if t.r_multiple > 0)
    win_rate = wins / len(all_trades) * 100
    expectancy_mean, ci_low, ci_high = bootstrap_expectancy_ci(all_trades)
    consistency = consistency_check(window_results)

    edge_proven = ci_low > 0  # l'intervalle de confiance à 95% exclut 0

    return {
        "strategy": strategy_name,
        "n_fenetres": len(window_results),
        "n_trades_oos_total": len(all_trades),
        "win_rate_oos": round(win_rate, 1),
        "expectancy_moyenne": round(expectancy_mean, 3),
        "IC_95%_borne_basse": round(ci_low, 3) if ci_low == ci_low else "N/A (trop peu de trades)",
        "IC_95%_borne_haute": round(ci_high, 3) if ci_high == ci_high else "N/A (trop peu de trades)",
        "edge_statistiquement_prouve": edge_proven,
        "consistance_entre_fenetres": consistency,
        "verdict_final": (
            "Edge probable — l'intervalle de confiance exclut 0, ET consistant entre fenêtres"
            if edge_proven and "positif" in consistency.get("verdict", "")
            else "Edge NON prouvé à ce stade — ne pas trader en réel sur cette seule base"
        ),
    }


# ------------------------- COMPARAISON MULTI-STRATÉGIES -------------------------

def compare_strategies_safely(
    strategies: dict,  # {"sweep_bos": (strategy_fn, param_grid_or_None), ...}
    data_loader: Callable,
    windows: list[Window],
) -> dict:
    """
    Compare plusieurs stratégies en walk-forward, avec un avertissement
    explicite sur le problème des comparaisons multiples : plus vous testez
    de stratégies, plus il faut un signal fort pour le croire.
    """
    all_summaries = {}

    for name, (fn, grid) in strategies.items():
        window_results = run_walkforward(fn, data_loader, windows, grid, strategy_name=name)
        all_summaries[name] = summarize_walkforward(window_results, name)

    n_strategies = len(strategies)
    proven = [n for n, s in all_summaries.items() if s.get("edge_statistiquement_prouve")]

    warning = None
    if n_strategies > 5 and len(proven) <= 1:
        warning = (
            f"⚠️ Vous avez comparé {n_strategies} stratégies. Avec autant de comparaisons, "
            f"il faut s'attendre à ce qu'une seule ressorte bien par pur hasard, même sans edge réel. "
            f"Le seul résultat qui compte vraiment est le test sur le VAULT (jamais touché) "
            f"pour la/les stratégie(s) sélectionnée(s) ici — pas ce classement lui-même."
        )

    return {
        "comparatif": all_summaries,
        "strategies_avec_edge_prouve": proven,
        "avertissement_comparaisons_multiples": warning,
    }


# ------------------------- ÉTAPE FINALE : LE VAULT -------------------------

def evaluate_on_vault(
    strategy_fn: Callable,
    params: dict,
    data_loader: Callable,
    vault_start: datetime,
    vault_end: datetime,
    strategy_name: str = "",
) -> dict:
    """
    À N'APPELER QU'UNE SEULE FOIS, sur la stratégie déjà choisie via le
    walk-forward. Si ce résultat est mauvais, la stratégie est rejetée —
    on ne retourne PAS bidouiller les paramètres et retester le vault
    (sinon il devient lui-même de l'in-sample et perd toute sa valeur).
    """
    vault_df = data_loader(vault_start, vault_end)
    trades = strategy_fn(vault_df, params)

    if not trades:
        return {"strategy": strategy_name, "verdict": "Aucun trade sur le vault — non concluant"}

    expectancy_mean, ci_low, ci_high = bootstrap_expectancy_ci(trades)
    win_rate = sum(1 for t in trades if t.r_multiple > 0) / len(trades) * 100

    return {
        "strategy": strategy_name,
        "n_trades_vault": len(trades),
        "win_rate_vault": round(win_rate, 1),
        "expectancy_vault": round(expectancy_mean, 3),
        "IC_95%": (round(ci_low, 3) if ci_low == ci_low else "N/A", round(ci_high, 3) if ci_high == ci_high else "N/A"),
        "verdict": (
            "VALIDÉ sur données jamais vues — passable en démo réelle"
            if ci_low > 0 else
            "NON VALIDÉ — l'edge trouvé en walk-forward ne se confirme pas sur le vault. Abandonner ou retravailler la stratégie ENTIÈREMENT (nouvelles données, pas ce vault)."
        ),
    }
