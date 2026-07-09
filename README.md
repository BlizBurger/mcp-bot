# SMC/AMD Setup Scanner (MT5)

Scanner de setups **Smart Money Concepts / modèle AMD** (Accumulation -
Manipulation - Distribution) sur forex, connecté à MetaTrader 5, avec alertes
Telegram, backtest et dashboard local.

> **Cet outil ALERTE seulement.** Il n'exécute jamais d'ordre — la décision
> d'ouvrir un trade sur MT5 reste 100 % manuelle. Aucune option d'exécution
> automatique n'existe, même cachée.

## ⚠️ Limites connues — à lire avant toute utilisation

Ces limites sont volontairement affichées partout (logs au démarrage, rapport
de backtest, dashboard) et ne doivent pas être retirées :

1. **Le SMC/ICT n'a pas de preuve statistique académique rigoureuse d'edge.**
   C'est une méthode populaire mais discrétionnaire à la base — cette
   automatisation est une approximation mécanique, pas une garantie.
2. **Risque d'overfitting** : les paramètres (tolérance equal highs/lows,
   buffers, seuils de corrélation) ont été choisis arbitrairement, pas
   optimisés rigoureusement.
3. **Le tick_volume MT5 n'est pas un vrai volume centralisé** (le forex n'en
   a pas).
4. **Le filtre news dépend à 100 % du remplissage manuel de
   `news_today.txt` chaque matin** — ce n'est pas un flux temps réel. Fichier
   vide ou absent = filtre inactif (le scanner le signale en WARNING).
5. **Le backtest simule sur mèches M15, pas tick par tick** : spread et
   slippage réels non modélisés ; si SL et TP sont touchés dans la même
   bougie, le trade est compté perdant (hypothèse conservatrice). Les
   résultats réels seront probablement moins bons.

## Logique métier (modèle AMD)

- **Accumulation** : range de la session asiatique.
- **Manipulation** : sweep de liquidité (mèche au-delà d'un equal high/low ou
  du range asiatique, puis clôture à l'intérieur) pendant les killzones
  Londres/NY.
- **Distribution** : entrée sur retour du prix dans un FVG ou Order Block
  aligné avec le biais H4 (structure HH/HL ou LH/LL), formé après le sweep.

Filtres de confluence :
- **Volume** : tick_volume de la bougie impulsive ≥ 1.2× sa moyenne 20 périodes.
- **Corrélation** : si une paire corrélée (>0.6 sur 50 bougies) donne un biais
  H4 opposé (en tenant compte du signe de la corrélation), le setup est ignoré.
- **News** : blackout 30 min avant/après toute news *High impact* touchant une
  des deux devises de la paire (lu depuis `news_today.txt`).

SL derrière l'extrême du sweep + buffer en pips ; TP = risque × R:R (2.0 par
défaut). Règle FTMO : **1 alerte Telegram par jour maximum** — les setups
suivants sont stockés en base sans alerte.

Le scanner live et le backtest utilisent **exactement la même fonction de
détection** (`smc.core.find_amd_setup`) : ce qui est backtesté est ce qui
serait alerté.

## Installation (Windows, requis pour MetaTrader5)

```bash
git clone <repo>
cd mcp-bot
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

1. **Secrets** : `copy .env.example .env` puis remplir :
   - `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` (bot créé via @BotFather ;
     le chat_id s'obtient p.ex. via @userinfobot) ;
   - `MT5_LOGIN` / `MT5_PASSWORD` / `MT5_SERVER` — ou laisser vide si le
     terminal MT5 est déjà ouvert et connecté sur la machine.
2. **Paramètres** : tout est dans `config.yaml` (paires, timeframes, sessions,
   seuils, R:R, buffers, chemins). ⚠️ Les fenêtres de sessions sont en
   **heure serveur MT5** — vérifier le décalage de votre broker.
3. **News** : `copy news_today.example.txt news_today.txt` et remplir chaque
   matin (format `HH:MM DEVISE Nom de l'événement Impact`).

## Lancement

| Quoi | Commande |
|---|---|
| Scanner live (alerte Telegram) | `python -m smc.scanner` |
| Backtest (rapport HTML + CSV dans `reports/`) | `python -m smc.backtest --days 60` |
| Dashboard local | `streamlit run dashboard.py` |
| Tests unitaires | `python -m pytest tests/` |

- **Scanner** : scanne toutes les 5 min (configurable), logue dans
  `logs/scanner.log` (rotation quotidienne, 30 jours conservés), stocke chaque
  setup dans `data/alerts.db` (SQLite) avec la colonne `alerted` pour comparer
  plus tard « alerté » vs « backtesté », et écrit `data/scanner_status.json`
  pour le dashboard. Reconnexion MT5 automatique, retries Telegram avec
  backoff — une erreur sur une paire ou un cycle est loggée et n'arrête
  jamais le processus.
- **Backtest** : `--days N` et `--pairs EURUSD,USDJPY` optionnels. Produit
  `reports/report_<horodatage>.html` (stats globales, courbe d'équité en R,
  stats par paire, tableau des trades — avec le rappel des limites) plus le
  CSV brut des trades.
- **Dashboard** : statut du scanner (fraîcheur du dernier scan, connexion
  MT5), news chargées, derniers setups en base, bouton pour relancer un
  backtest et rapport intégré.

## Structure

```
config.yaml            # TOUS les paramètres (rien en dur dans le code)
.env                   # secrets (jamais commité)
news_today.txt         # news du jour, rempli à la main chaque matin
smc/
  core.py              # logique métier PURE (FVG, OB, sweeps, biais, SL/TP, filtres)
  scanner.py           # boucle live robuste -> Telegram + SQLite
  backtest.py          # rejoue find_amd_setup sur l'historique MT5
  report.py            # rapport HTML autonome (SVG inline)
  mt5_client.py        # connexion MT5 avec reconnexion automatique
  telegram.py          # envoi avec retry/backoff
  news.py              # lecture de news_today.txt
  db.py                # historique SQLite des setups
  config.py            # chargement config.yaml + .env
  logging_setup.py     # logs fichier avec rotation quotidienne
dashboard.py           # Streamlit
tests/test_core.py     # tests unitaires + test d'intégration du pipeline AMD
```

## Notes

- Le package `MetaTrader5` ne fonctionne que sous **Windows** avec un terminal
  MT5 installé. Les tests unitaires, eux, tournent partout (la logique métier
  est pure).
- Aucune API de news payante n'est utilisée : le fichier texte manuel reste la
  méthode par défaut.
