# Stratégie V1 — règles du moteur (phase 4)

> Stratégie **explicite et testable**, dont la rentabilité **reste à démontrer**.
> Les paramètres de `config.yaml` sont des hypothèses initiales, pas des réglages
> optimisés. Aucun ordre réel n'est passé : le moteur ne tourne qu'en simulation.

## Cadre

| Élément | Valeur V1 |
|---|---|
| Compte | FTMO CFD 2-Step, 10 000 USD |
| Exécution | XAUUSD (MT5), zones en M5, confirmation en M1 |
| Contexte | GC (COMEX) : volume profile, TPO, delta |
| Entrées | 08:20 → 13:15 America/New_York ; clôture et annulation à 13:30 |
| Risque | 0,5 % de min(equity, capital initial), frais estimés compris |
| Limites | 1 position, 3 entrées/jour, 1 tentative par zone, ni martingale ni renforcement |

Les heures sont des fuseaux nommés (`America/New_York`, `Europe/Prague`), donc les
changements d'heure sont gérés automatiquement. Londres est désactivé.

## Chaîne de décision (machine à états d'une zone)

`detected → eligible → touched → confirming → ordered → position → closed`,
avec les sorties `rejected` (taille), `duplicate`, `consumed` (contact sans trade),
`invalidated` et `expired`. Chaque transition est horodatée avec son motif.

1. **Zone (XAUUSD M5, bid)**
   - FVG : 3 bougies clôturées, activé à la clôture de la 3e. Taille entre
     max(2 spreads, 0,1 ATR14) et 1 ATR14.
   - OB : une clôture casse un pivot (2 bougies de chaque côté) déjà confirmé avant
     elle. Le corps de la cassure doit être ≥ 1 ATR14 (ATR calculé avant la bougie).
     L'OB est la dernière bougie opposée parmi les 5 précédentes (range complet).
   - Zones de la séance uniquement, expirées à 13:15. Une zone qui chevauche une zone
     de même sens déjà vue est un doublon : même idée, donc pas de réentrée.
2. **Premier contact (M1)** : il consomme la zone, trade ou non. Une clôture M1
   au-delà du bord distal invalide la zone.
3. **Scénario** (Value Area de la séance New York précédente)
   - *Retour vers la valeur* : une clôture M5 hors VA, puis une clôture de retour
     dans les 3 bougies. Retour sous VAH → vente seulement ; au-dessus de VAL → achat
     seulement. Le setup reste valable 60 minutes. Le POC précédent doit se trouver
     dans le sens du trade.
   - *Cassure acceptée* (approximation, pas une preuve) : 2 clôtures M5 au-delà
     de la même borne, avec un volume GC de la paire ≥ médiane des 20 paires
     précédentes. Toute zone fraîche dans le sens de la cassure est alors
     tradable (continuation ; `breakout.require_bound_retest` pour exiger le
     retest de la borne). Le scénario se désactive au retour d'une clôture dans la VA.
     Son objectif de référence est le **mouvement mesuré** : borne cassée ± hauteur
     de la VA précédente, sauf obstacle connu plus proche.
4. **Confluences** (variantes B, C). Un niveau est « proche » si sa distance au
   range de la zone est ≤ 0,5 ATR M5, après projection GC→XAUUSD (même distance
   pour le retest de la borne, s'il est exigé). Il y a 1 point par famille, au
   plus : rejet (LVN, single prints), bornes de valeur (VAH/VAL volume ou TPO), IB
   (connue à partir de 09:20). Il faut au moins 1 famille (`min_score_families`).
   Le POC ne rapporte aucun point.
5. **Déclencheur** : dans les 3 bougies M1 clôturées après le contact.
   - A, B : clôture M1 au-delà du high (achat) ou du low (vente) précédent.
   - C : en plus, delta GC précédent < 0 et delta courant > 0 (achat), et l'inverse
     pour une vente.
   - C_ABS (désactivée par défaut) : absorption GC au lieu du delta.
6. **Entrée** à l'ouverture de la bougie suivante : ask + slippage pour un achat,
   bid − slippage pour une vente. Tous les garde-fous sont revérifiés à ce prix.
7. **SL** derrière le bord distal + max(spread, 0,1 ATR). **TP** au premier
   niveau opposant dans le sens du trade (POC vol/TPO, borne de VA, bord proche
   d'une HVN). Aucun obstacle n'est sauté ; sans objectif, pas de trade. Il faut un
   **R:R net ≥ 1,5** (commission et slippage inclus). Le volume est arrondi vers le
   bas, et le trade est refusé si le lot minimum dépasse le budget. SL/TP fixes.

## Protections

- **Arrêt interne journalier : −150 USD** (réalisé + flottant + frais) par rapport
  au solde à minuit Europe/Prague. Il clôture la position et verrouille les entrées
  jusqu'au lendemain. Avant chaque entrée, la perte au SL doit tenir dans ce budget.
- **FTMO 2-Step (référence)** : 500 USD/jour, plancher d'equity à 9 000 USD.
  Une marge interne arrête tout à 9 200 USD (`risk.total_floor_margin_usd`).
  Source : objectifs de trading FTMO et synthèses 2026, **à vérifier dans les
  conditions du compte**.
- **Contrôle permanent de l'equity** au pire prix de chaque bougie M1. Une clôture
  au marché subit du slippage, donc **aucun seuil ne garantit une perte maximale
  exacte**.
- **Pas d'entrée** dans les cas suivants : spread > 2 × médiane des 60 dernières
  minutes ou historique insuffisant ; données GC ou XAUUSD périmées ; basis
  instable ou indisponible (roulement) ; niveaux de la veille indisponibles ;
  calendrier absent.
- **News (protections internes)** : pas d'entrée de T−15 à T+15 autour des annonces
  USD à fort impact. Les positions sont clôturées à T−15 avant NFP, CPI et FOMC.
  Ces règles sont distinctes des restrictions contractuelles FTMO, qui dépendent
  du programme et de la phase.
- **Échec de placement du SL** : la position est clôturée immédiatement et la
  journée verrouillée.

## Hypothèses et limites des données

- **XAUUSD** : bougies M1 MT5 au bid, plus le spread de la bougie (pas de ticks).
  L'ask est reconstruit, et le spread réel varie à l'intérieur de la minute.
- **Si SL et TP sont touchés dans la même bougie M1 → SL** (convention
  conservatrice). Une ouverture au-delà du SL sort au prix d'ouverture.
- **Commission FTMO non vérifiée** : 6 USD/lot aller-retour est une estimation. La
  lire dans l'historique MT5 et corriger `costs`.
- **Spécifications XAUUSD** : valeurs par défaut tant que
  `python -m xauof.data.symbol_spec` n'a pas été lancé (MT5 ouvert).
- **Calendrier économique historique absent.** Par défaut, le moteur bloque
  toutes les entrées des jours sans calendrier. Pour un backtest exploratoire,
  `news.missing_calendar: ignore_backtest_only` lève ce blocage, et le rapport est
  alors marqué non représentatif.
- **Delta** : la variante C exige que la source GC classe les transactions
  acheteur/vendeur. La part non classée est contrôlée à chaque lancement, et la
  variante est refusée au-delà de 5 %.
- **Roulements GC** : les niveaux de la veille sont convertis en prix XAUUSD avec
  le basis de leur propre séance. L'IB n'est utilisée que si le contrat n'a pas
  changé. Le basis de référence repart de zéro après chaque roulement.
- Les journées « degraded » de Databento ne sont pas exclues par le moteur, car
  cette information n'est connue qu'après coup.

## Calendrier : format attendu (`data/calendar/`)

```
usd_events.csv : time_utc,currency,impact,kind,event
                 2026-09-04 12:30:00+00:00,USD,High,NFP,Non-Farm Employment Change
coverage.csv   : start_utc,end_utc   (périodes où la liste est COMPLÈTE)
```

En live, `python -m xauof.news.calendar --import-ff` importe la semaine en cours.

## Journal des réglages

| Date | Changement | Justification |
|---|---|---|
| 2026-10-23 | cassure : objectif mouvement mesuré | aucun objectif n'existait dans le sens d'une cassure (13 refus, R:R de 0,02 à 0,19) |
| 2026-10-23 | `near_atr` 0,15 → 0,5 | 441 contacts refusés « zone loin de la borne » ; 0,15 ATR ≈ 0,30-0,50 $ |
| 2026-10-23 | validité du retour vers la valeur 30 → 60 min | hypothèse initiale non spécifiée |
| 2026-10-23 | cassure : toute zone fraîche dans le sens (plus seulement le retest de la borne) | 423 contacts refusés « zone loin de la borne » même à 0,5 ATR ; logique de continuation |
| 2026-10-23 | score minimum 2 → 1 famille (B, C) | ~1 000 contacts sur 1 131 bloqués : B et C ne pouvaient pas être évaluées |

Ces décisions s'appuient sur l'entonnoir in-sample (oct. 2025 → juin 2026), pas sur
le PnL. La règle « le premier contact consomme la zone » est conservée.

## Validation

- Variantes A (zones + scénario + réaction), B (+ contexte volume/TPO) et
  C (+ delta). Mêmes règles de risque et d'exécution. Résultats séparés par
  scénario.
- **Période réservée** à partir de `validation.holdout_start` (2026-07-01) :
  refusée sans `--holdout`. On ne règle rien dessus.
- Pas de conclusion sur la rentabilité avant le hors échantillon **et** un
  forward test en démo.

```bash
pytest
python -m xauof.strategy.run --start 2025-10-06 --end 2026-07-01 --variant A
python -m xauof.strategy.run --start 2025-10-06 --end 2026-07-01 --variant B
python -m xauof.strategy.run --start 2025-10-06 --end 2026-07-01 --variant C
```
