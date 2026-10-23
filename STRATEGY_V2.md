# Stratégie V2 — projet de spécification (À VALIDER, puis à figer)

> Statut : **brouillon**. Rien n'est codé. Une fois validée, cette spec sera
> datée et figée. Elle ne changera plus avant le verdict sur la période réservée.

## Ce que la V1 nous a appris (backtest de recherche, oct. 2024 → juin 2026, sans filtre news)

| Constat | Chiffres |
|---|---|
| La cassure en continuation perd | A : 73 trades, R moyen −0,35, IC95 [−0,62 ; −0,02] |
| Le retour vers la valeur est trop rare | 7 trades en 21 mois |
| Les objectifs sont trop lointains | TP visé médian 3,1 R, atteint par 11 % des trades ; 38 % passent par +1 R |
| La moitié des perdants partent tout de suite dans le mauvais sens | 49 % ne dépassent jamais +0,25 R |
| La confirmation par le delta GC améliore la direction (piste, non prouvée) | MFE médiane +1,24 R (C) contre +0,47 R (A) ; 58 % atteignent +1 R ; seulement 19 trades |
| Le contexte (scénarios et score) coûte des trades pour un gain non démontré | 1 254 contacts refusés pour scénario, 1 507 pour score |

## Idée directrice

**Les zones FVG/OB disent où regarder. L'order flow GC dit si on agit.** On retire
les régimes de marché qui filtraient la V1 (scénarios, score), on garde la
confirmation par le delta, et on vise des objectifs que le prix atteint réellement.

## Règles V2

1. **Zones** : identiques à la V1 (détecteur M5 testé, sans anticipation). FVG et
   OB bullish/bearish, premier contact uniquement, déduplication, invalidation par
   clôture M1 au-delà du bord distal, expiration à 13:15.
2. **Direction** : celle de la zone (bullish → achat, bearish → vente). **Pas** de
   scénario ni de score obligatoire.
3. **Déclencheur** (celui de la variante C de la V1, sans changement) : dans les
   3 bougies M1 après le contact, delta GC précédent < 0 et delta courant > 0, puis
   clôture XAUUSD au-dessus du high M1 précédent pour un achat. Inverse pour une
   vente.
4. **Entrée** : ouverture de la bougie suivante, au prix exécutable (ask ou bid, plus
   le slippage).
5. **Stop** : identique à la V1, derrière le bord distal + max(spread, 0,1 ATR M5).
6. **Objectif : 2 R fixes**, calculés sur la distance entrée → SL. Le R:R net après
   commission et slippage est d'environ 1,9.
7. **Filtre « espace libre »** : si un niveau connu de la séance précédente (POC
   volume ou TPO, VAH/VAL, bord d'HVN, projeté en XAUUSD) se trouve **entre
   l'entrée et +1 R**, le trade est refusé : l'obstacle est trop proche.
   C'est le seul usage du profil de la veille en V2.
8. **Gestion** : SL et TP fixes, sans break-even, trailing ni sortie partielle.
   Clôture forcée à 13:30.
9. **Inchangé par rapport à la V1** : fenêtre 08:20–13:15 New York, 1 position,
   3 entrées par jour, risque de 0,5 %, arrêt journalier interne à −150 $, plancher
   FTMO et marge, filtre de spread, contrôles de données, news (±15 min, clôture
   avant NFP/CPI/FOMC), échec de SL, journal.

Il n'y a **qu'un seul nouveau paramètre** (2 R), choisi sur le principe (« un
objectif que le prix atteint »), et non optimisé.

## Plan de validation (fixé AVANT tout test)

1. **Contrôle sur la période d'étude** (oct. 2024 → juin 2026) : la V2 ne doit
   **pas** être significativement perdante (borne haute de l'IC95 du R moyen > 0).
   Ces données ont servi à concevoir la V2. Ce n'est donc **pas** une preuve, seulement
   un garde-fou.
2. **Verdict sur la période réservée** (juil. → sept. 2026), lancée **une seule
   fois** avec `--holdout`, règles figées, **calendrier news réel obligatoire** :
   - au moins 40 trades ; sinon pas de verdict, on attend plus de données ;
   - R moyen net > 0 ;
   - drawdown compatible avec FTMO 2-Step (aucun dépassement de −500 $/jour ni
     du plancher en simulation de compte).
3. **Si le verdict est positif** : 1 à 3 mois de forward test en démo FTMO, avec
   alertes et exécution simulée, avant tout challenge payant.
4. **Si le verdict est négatif** : la V2 est abandonnée telle quelle. Pas de
   réglage a posteriori sur la période réservée.

## Ce qu'il faut avant le verdict

- **Calendrier économique historique** couvrant au moins juillet à septembre 2026
  (NFP, CPI, FOMC et autres annonces USD à fort impact).
- Commission FTMO réelle relevée dans MT5 (actuellement estimée à 6 $/lot).

## Questions ouvertes (à trancher avant de figer)

- Objectif : 2 R fixes, ou 1,5 R ? (1,5 R : plus de gagnants, moins de gain par trade.)
- Filtre « espace libre » : à garder ou à retirer ?
- Faut-il garder une variante sans le delta (réaction de prix seule) comme
  référence de comparaison ?
