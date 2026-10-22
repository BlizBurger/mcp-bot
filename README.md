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
├── strategy/    score de confluence, entrées / sorties
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
