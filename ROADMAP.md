# Roadmap — XAUUSD OF

Règle d'or : **on prouve avant de payer, on alerte avant d'exécuter.**

| # | Phase | Contenu | Coût |
|---|-------|---------|------|
| 0 ✅ | Reset + squelette | Structure, config, tests | 0 € |
| 1 ✅ | Données | Historique GC `trades` (Databento, crédits gratuits), XAUUSD M1 (MT5), roulements, Parquet | 0 € |
| 2 ✅ | Indicateurs | Volume profile, TPO, order flow, FVG / OB | 0 € |
| 3 🔧 | Passerelle | Basis GC → XAUUSD, garde-fous | 0 € |
| 4 | Stratégie + **bases de l'interface** | Score de confluence ; API locale + dashboard (lecture backtests, graphiques) | 0 € |
| 5 | Backtest + walk-forward + **interface v1** | Comparaison A (FVG/OB) / B (+profil) / C (+order flow) ; résultats dans l'interface | 0 € |
| 6 | Live : FTMO Challenge 10k (2-Step) | Flux GC temps réel, exécution MT5, news, interface hébergée et sécurisée (fermeture des trades) | Databento ~199 $/mois + VPS + challenge |
| 7 | Passage 100k | Uniquement après preuve sur le 10k | challenge 100k |

## Critères pour passer en live (fin de phase 5)

- Espérance positive **après spread** sur toutes les fenêtres hors échantillon
- Intervalle de confiance bootstrap > 0, ≥ 100 trades
- C (avec order flow) meilleur que A (FVG/OB seuls)
- Drawdown simulé compatible avec 5 % / jour et 10 % total (FTMO 2-Step)

## News

- Source calendrier : à brancher en phase 6 (flux gratuit type Forex Factory,
  ou API payante si besoin de fiabilité).
- Pas de nouvelle entrée ±15 min autour des news USD à fort impact.
- Fermeture des positions avant NFP / CPI / FOMC.
- Compte FTMO financé (Standard) : interdiction d'ouvrir, fermer, ou de
  voir un SL/TP se déclencher dans les 2 min avant/après une news ciblée.

## Interface

Dashboard web façon « Jarvis » : état du bot, positions, P&L, respect des
limites FTMO, profil volume/TPO en direct, bouton de fermeture des trades.
Accès protégé (HTTPS + authentification) dès la mise en ligne.
