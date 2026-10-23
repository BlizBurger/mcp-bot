# XAUUSD OF

Bot de trading sur l'or (**XAUUSD**) fondé sur l'**order flow**, le **volume
profile** et le **TPO**, combinés aux **FVG** et **Order Blocks** (bullish et
bearish).

- **Données** : futures or du COMEX (GC/MGC) via Databento — vrai volume
  centralisé avec côté agresseur. Le XAUUSD spot (OTC) n'en a pas.
- **Exécution** : XAUUSD sur MT5 (compte FTMO), niveaux reportés via le basis
  GC → XAUUSD.
- **Protection news** : blackout et fermeture avant les annonces à fort impact.
- **Interface** : tableau de bord web auto-hébergé (suivi, données, gestion
  des trades).

> ⚠️ Projet en construction. Rien ne passe en live tant que le walk-forward
> n'a pas validé un edge après spread. Voir [ROADMAP.md](ROADMAP.md).

## Structure

```
xauof/
├── data/        Databento (GC) + MT5 (XAUUSD), Parquet
├── indicators/  volume profile, TPO, order flow, FVG / OB
├── bridge/      basis GC → XAUUSD
├── strategy/    zones, scénarios, confluences, déclencheur, risque, moteur
├── backtest/    simulation + walk-forward
├── news/        calendrier économique, blackouts
├── live/        temps réel, Telegram, exécution, garde-fous FTMO
└── web/         interface de pilotage
```

## Installation

```bash
python -m venv .venv && source .venv/bin/activate   # Windows : .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env    # puis remplir DATABENTO_API_KEY, MT5_*, TELEGRAM_*
pytest
```

## Données (phase 1)

```bash
# Or COMEX (GC) via Databento — TOUJOURS estimer d'abord, rien n'est dépensé
python -m xauof.data.gc --start 2025-10-01 --dry-run
python -m xauof.data.gc --start 2025-10-01            # refuse si > data.max_cost_usd

# XAUUSD M1 depuis MT5 (terminal ouvert et connecté)
python -m xauof.data.xauusd --start 2025-10-01
```

Les fichiers sont rangés par mois dans `data/` (jamais envoyés sur GitHub).
Un mois complet déjà téléchargé n'est jamais re-payé.

## Indicateurs (phase 2)

| Module | Contenu |
|---|---|
| `indicators/sessions.py` | journée CME (18:00 → 17:00 New York), séance RTH, contrat actif |
| `indicators/volume_profile.py` | POC, Value Area (VAH / VAL), HVN, LVN |
| `indicators/tpo.py` | Market Profile : POC / VA en temps, Initial Balance, single prints, queues, poor high / low |
| `indicators/orderflow.py` | delta, CVD, footprint + déséquilibres, absorption, grosses transactions, divergence CVD |
| `indicators/smc.py` | FVG et Order Blocks bullish / bearish, suivi retour en zone / invalidation |

Contrôle visuel d'une journée (niveaux affichés + graphique dans `reports/`) :

```bash
python -m xauof.indicators.check --date 2026-09-15
```

## Passerelle GC → XAUUSD (phase 3)

`bridge/basis.py` : basis = GC − XAUUSD (milieu bid/ask) minute par minute,
référence = médiane glissante 60 min sur le passé, remise à zéro à chaque
roulement. Un niveau GC se reporte par `niveau_xau = niveau_gc − basis`.
Contrôle d'horloge : la corrélation des rendements 1 min doit être maximale
sans décalage (sinon erreur de fuseau ou de changement d'heure).

```bash
python -m xauof.bridge.check --start 2025-10-01 --end 2026-10-01
```

## Interface « XAUUSD·OF » (assistant de trading manuel)

Aide à la décision pour ton trading discrétionnaire. **Lecture seule : aucun ordre n'est passé.**

```bash
python -m xauof.assistant.update   # chaque matin (MT5 ouvert) : niveaux GC de la veille vers XAUUSD
python -m xauof.web                # interface en direct (MT5 ouvert) : http://127.0.0.1:8000
python -m xauof.web --demo         # démo sur données synthétiques (marché fermé)
python -m xauof.web.alert_stats   # alertes A+ : fréquence, horaires et ce que les plans auraient donné (historique)
```

- **Live** : prix et spread, contexte (position par rapport à la Value Area d'hier),
  pression order flow (**estimation** à partir des ticks du broker), compte à rebours
  news, jauge de risque du jour ; graphique M1 / M5 avec niveaux d'hier, HVN, single
  prints, IB et zones FVG / OB ; order flow (delta par minute + CVD depuis 08:20) ;
  setups **A+** (zone FVG / OB sur un niveau d'hier ET dans le sens du contexte) et
  alertes (Telegram si `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` sont dans `.env`).
- **Journal** : import des positions clôturées MT5, critères à cocher, notes ;
  statistiques en R par setup et par critère.
- Écoute uniquement sur `127.0.0.1` (ton PC). La mise en ligne demandera HTTPS +
  authentification.

## Calendrier économique

```bash
python -m xauof.news.history                # historique officiel → calendar/history/ (backtests)
python -m xauof.news.calendar --import-ff   # live : semaine en cours (Forex Factory)
python -m xauof.news.calendar --status      # couverture et prochaines annonces
```

## Moteur de stratégie (phase 4)

Règles complètes, hypothèses et limites : [STRATEGY.md](STRATEGY.md) (V1) et
[STRATEGY_V2.md](STRATEGY_V2.md) (V2 : zones + delta GC, partiel à +1 R, break-even,
objectif 2 R — abandonnée, R moyen −0,14 [IC95 −0,24 ; −0,04]) et
[STRATEGY_V3.md](STRATEGY_V3.md) (V3 figée : échec de cassure de l'Initial Balance,
variante `V3`).
Simulation uniquement : aucun ordre réel n'est passé.

```bash
python -m xauof.data.symbol_spec          # spécifications XAUUSD réelles (MT5 ouvert)
python -m xauof.strategy.run --start 2025-10-06 --end 2026-07-01 --variant A   # B, C
```

La période réservée (`validation.holdout_start`) est refusée sans `--holdout`.
`--research` désactive les limites de compte pour tester chaque signal (lecture en R,
avec intervalle de confiance et excursions MFE / MAE) ; sans cette option, le
backtest simule le parcours réel du compte (arrêts journaliers, plancher FTMO).
Analyse de l'entonnoir (où et pourquoi les zones sont abandonnées) :
`python -m xauof.strategy.funnel reports/strategy_A_2025-10-06_2026-07-01`
