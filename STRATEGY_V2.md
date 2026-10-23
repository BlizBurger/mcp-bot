# Stratégie V2 — spécification FIGÉE le 2026-10-23

> Cette spec ne change plus avant le verdict sur la période réservée. Toute
> modification ultérieure crée une V3, qui devra être validée sur des données
> qu'elle n'a pas vues.

## Ce que la V1 nous a appris (backtest de recherche, oct. 2024 → juin 2026, sans filtre news)

| Constat | Chiffres |
|---|---|
| La cassure en continuation perd | 73 trades, R moyen −0,35, IC95 [−0,62 ; −0,02] |
| Le retour vers la valeur est trop rare | 7 trades en 21 mois |
| Les objectifs sont trop lointains | TP visé médian 3,1 R, atteint par 11 % des trades ; 38 % passent par +1 R |
| La moitié des perdants partent tout de suite dans le mauvais sens | 49 % ne dépassent jamais +0,25 R |
| Piste : la confirmation par le delta GC améliore la direction | MFE médiane +1,24 R (C) contre +0,47 R (A) ; seulement 19 trades |

## Idée directrice

**Les zones FVG/OB disent où regarder, l'order flow GC dit si on agit.**

## Règles

1. **Zones** : identiques à la V1. FVG et OB bullish/bearish M5, premier contact
   uniquement, déduplication, invalidation par clôture M1 au-delà du bord distal,
   expiration à 13:15.
2. **Direction** : celle de la zone. Pas de scénario ni de score.
3. **Déclencheur** : dans les 3 bougies M1 qui suivent la bougie du contact.
   - `D` (delta) : delta GC précédent < 0 et delta courant > 0, puis clôture XAUUSD
     au-delà du high (achat) ou du low (vente) M1 précédent ;
   - `P` (témoin) : réaction de prix seule.
4. **Entrée** à l'ouverture de la bougie suivante : ask + slippage pour un achat,
   bid − slippage pour une vente.
5. **Stop** : bord distal + max(spread, 0,1 ATR M5). 1 R = distance entrée → stop.
6. **Sorties**
   - **50 % à +1 R** (ordre limite). Volume arrondi vers le bas. S'il est impossible
     de fractionner (moins de 2 × lot minimum), il n'y a pas de partiel.
   - Après le partiel, le **stop du reste passe au prix d'entrée** (break-even).
   - Le **reste vise +2 R**.
   - Clôture forcée à 13:30, et avant NFP, CPI et FOMC.
7. **Filtre « espace libre »** (variantes `F`) : refus si un niveau de la séance
   précédente (POC volume ou TPO, VAH/VAL, bord proche d'une HVN, projetés en
   XAUUSD) se trouve entre l'entrée et +1 R.
8. **Inchangé par rapport à la V1** : fenêtre 08:20–13:15 New York, 1 position,
   3 entrées par jour, risque de 0,5 % de min(equity, capital), R:R net ≥ 1,5,
   arrêt journalier interne à −150 $, plancher FTMO et marge, filtre de spread,
   contrôles de données, news ±15 min, gestion de l'échec du SL, journal.

### Conventions de simulation (conservatrices)

Les données sont des bougies M1 bid avec leur spread : on ne voit pas l'ordre des
prix à l'intérieur d'une minute. On tranche donc toujours dans le sens défavorable :
- si le stop et le partiel (ou l'objectif) sont touchés dans la même bougie, **le
  stop compte** ;
- après un partiel, si la même bougie repasse par le prix d'entrée, **le
  break-even compte** ;
- une ouverture au-delà du stop sort au prix d'ouverture.

## Variantes

| Variante | Déclencheur | Espace libre | Rôle |
|---|---|---|---|
| `V2_DF` | delta | oui | candidate |
| `V2_D` | delta | non | candidate |
| `V2_PF` | prix | oui | témoin |
| `V2_P` | prix | non | témoin |

## Plan de validation (fixé avant tout test)

1. **Période d'étude** (oct. 2024 → juin 2026, `--research`) :
   - les 4 variantes sont lancées une fois ;
   - **choix du filtre** : on garde, parmi `V2_DF` et `V2_D`, celle qui a le R moyen
     le plus élevé. En cas d'écart inférieur à 0,05 R, on garde `V2_D`, la plus simple ;
   - **garde-fou** : la candidate retenue ne doit pas être significativement
     perdante (borne haute de l'IC95 > 0). Sinon, la V2 est abandonnée.
   - Ces données ont servi à concevoir la V2 : ce n'est **pas** une preuve.
2. **Verdict sur la période réservée** (juil. → sept. 2026), avec la seule candidate
   retenue (et son témoin pour information), lancé **une seule fois** avec
   `--holdout`, **calendrier news réel obligatoire** :
   - au moins 40 trades, sinon pas de verdict et on attend plus de données ;
   - R moyen net > 0 ;
   - simulation de compte sans dépassement de −500 $/jour ni du plancher.
3. **Verdict positif** : 1 à 3 mois de forward test en démo FTMO avant tout
   challenge payant.
4. **Verdict négatif** : la V2 est abandonnée, sans réglage a posteriori.

## Prérequis avant le verdict

- ~~Calendrier économique historique couvrant au moins juillet à septembre 2026.~~
  Fait le 2026-10-23 : `calendar/history/` (NFP, CPI, PPI, JOLTS, PIB, PCE, FOMC).
  Ne couvre pas les ventes au détail, l'ISM ni les inscriptions au chômage.
- Commission FTMO réelle relevée dans MT5 (actuellement estimée à 6 $/lot).

## Verdict sur la période d'étude — V2 ABANDONNÉE

Backtests `--research`, oct. 2024 → juin 2026, 446 séances, filtre news réel :

| Variante | Trades | Gagnants | R moyen [IC95] |
|---|---|---|---|
| V2_DF (delta + espace libre) | 282 | 43,6 % | −0,149 [−0,26 ; −0,03] |
| V2_D (delta) | 380 | 44,2 % | −0,141 [−0,24 ; −0,04] |
| V2_PF (témoin prix + espace libre) | 640 | 44,2 % | −0,144 [−0,22 ; −0,06] |

Application de la règle figée :
1. Choix du filtre : écart V2_DF / V2_D = 0,008 R < 0,05 R → **V2_D** retenue.
2. Garde-fou : borne haute de l'IC95 de V2_D = −0,04 < 0 → **significativement
   perdante → V2 abandonnée**. La période réservée n'est pas utilisée.

Enseignements :
- le delta GC, sous cette forme, n'apporte rien (V2_D ≈ témoin V2_PF) ;
- le filtre « espace libre » n'apporte rien ;
- 54 % des perdants ne dépassent jamais +0,25 R : la direction des entrées est
  proche du hasard ;
- hypothèse à mesurer : les frais (spread, slippage, commission) pèsent lourd
  face à des stops M5 courts (décomposition ajoutée au rapport).

Note : les lignes « excursions » de ces rapports étaient faussées pour les trades
avec partiel (R calculé avec le stop ramené au prix d'entrée). Corrigé ensuite ;
le R moyen et le verdict n'en dépendent pas.
