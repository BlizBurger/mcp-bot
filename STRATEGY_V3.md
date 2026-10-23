# Stratégie V3 — échec de cassure de l'Initial Balance — spec FIGÉE

> Rédigée **avant** tout test de cette règle sur les données. Aucun backtest V3
> n'a été lancé au moment du gel. Toute modification après le premier test crée
> une V4.

## Hypothèse

Quand le prix sort de l'Initial Balance (IB) mais n'arrive pas à y rester, la
cassure a échoué : les acheteurs (ou vendeurs) agressifs sont piégés et le prix
revient traverser l'IB. Cette hypothèse est différente de la V1 et de la V2 : pas
de zones FVG/OB, pas de delta GC, mais une structure de séance avec des stops plus
larges, donc des frais plus légers en R (ils pesaient 0,11 R en V2).

## Règles

1. **IB** : plus haut et plus bas **XAUUSD (bid)** de 08:20 à 09:20 (New York),
   connus à 09:20. On utilise les prix du broker, sans passer par GC.
2. **Cassure** : une clôture M5 au-dessus du haut de l'IB (côté haut) ou
   au-dessous du bas (côté bas), après 09:20.
3. **Échec** : dans les **6 bougies M5** (30 min) qui suivent la bougie de cassure,
   une clôture M5 revient à l'intérieur de l'IB. Signal **vente** si la cassure
   était par le haut, **achat** si elle était par le bas.
   - Pas de retour en 6 bougies : la cassure est acceptée et ce côté est terminé
     pour la journée.
   - **Un seul signal par côté et par jour.** Pas de réentrée.
4. **Entrée** : ouverture de la bougie M1 qui suit la clôture d'échec, au prix
   exécutable (ask ou bid, plus le slippage).
5. **Stop** : au-delà de l'extrême atteint pendant la cassure (le plus haut depuis
   la cassure pour une vente), + max(spread, 0,1 ATR M5). 1 R = distance entrée → stop.
6. **Sorties**
   - **50 % au milieu de l'IB** (ordre limite), puis stop du reste au prix
     d'entrée (break-even) ;
   - **reste à la borne opposée de l'IB** ;
   - refus si le milieu de l'IB est à **moins de 1 R** de l'entrée, ou déjà
     dépassé ;
   - clôture forcée à 13:30.
7. **Inchangé** : fenêtre d'entrée jusqu'à 13:15, 1 position, 3 entrées par jour,
   risque de 0,5 %, arrêt journalier −150 $, plancher FTMO, filtre de spread,
   news (±15 min, clôture avant NFP/CPI/FOMC), échec du SL, journal.
   Les contrôles GC (basis, flux) ne s'appliquent pas, puisque la V3 n'utilise pas GC.

Conventions de simulation : identiques à la V2 (stop avant objectif dans une même
bougie, break-even compté si la bougie du partiel repasse par l'entrée).

## Plan de validation (fixé avant tout test)

La période d'étude (oct. 2024 → juin 2026) **n'a pas servi à concevoir cette
règle**. Elle constitue donc un vrai premier test, mais on reste prudent, car nous
avons déjà observé ce marché.

1. **Étape 1 — période d'étude**, lancée une seule fois (`--research`) :
   - au moins 100 trades, sinon pas de verdict ;
   - **R moyen net > 0 et borne basse de l'IC95 > −0,05**. Sinon, la V3 est abandonnée.
2. **Étape 2 — période réservée** (à partir du 2026-07-01, prolongée par les mois
   suivants jusqu'à atteindre **au moins 40 trades**), lancée une seule fois par
   tranche, règles inchangées : **R moyen net > 0**, et aucun dépassement des
   limites FTMO en simulation de compte.
3. **Étape 3** : 1 à 3 mois de forward test en démo FTMO.
4. Aucun réglage après l'étape 1. Une V3 qui échoue est abandonnée.

## Paramètres (config.yaml, `strategy.v3`)

| Paramètre | Valeur | Origine |
|---|---|---|
| `failure_window_m5` | 6 | principe : un échec rapide (30 min) |
| `partial_fraction` | 0,5 | comme la V2 |
| `breakeven_after_partial` | oui | comme la V2 |
| `min_first_target_r` | 1,0 | le milieu de l'IB doit valoir au moins le risque |

## Verdict sur la période d'étude — V3 ABANDONNÉE

Backtest `--research`, oct. 2024 → juin 2026, 446 séances, filtre news réel :
130 trades, 36,9 % de gagnants, **R moyen net −0,076 [IC95 −0,30 ; +0,15]**.
R moyen avant frais +0,038 [IC95 −0,18 ; +0,26] ; frais 0,115 R par trade.

Application de la règle figée : ≥ 100 trades (130 ✓), mais R moyen net ≤ 0 et borne
basse de l'IC95 (−0,30) < −0,05 → **V3 abandonnée**, sans réglage. La période
réservée reste intacte.

Enseignements : 196 signaux refusés car le milieu de l'IB était à moins de 1 R ;
avant frais, l'effet est indiscernable de zéro. Pas d'edge démontré.
