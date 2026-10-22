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
