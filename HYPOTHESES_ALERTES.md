# Deux hypothèses sur les alertes A+ — figées avant tout résultat

Figé le 2026-10-23, avant toute mesure. Base de comparaison : les plans de
[ALERTES_PLAN.md](ALERTES_PLAN.md) (−0,12 R par trade sur 2024-10 → 2026-06, IC 95 %
[−0,27 ; +0,05], non validé). Aucune règle ci-dessous ne sera modifiée après la mesure ; une
modification = une nouvelle hypothèse, à tester sur des données neuves.

Tout ce qui n'est pas précisé ici reprend ALERTES_PLAN.md : alertes A+ (zone FVG/OB sur un
niveau d'hier, dans le sens du contexte, 08:20–13:15 NY), plan à la première alerte valable de
la zone, SL au-delà de la zone + 0,25 ATR M5, TP avant le premier niveau d'hier à ≥ 1,5 R,
SL avant TP sur une même bougie, clôture forcée à 13:30, coûts estimés (commission 6 $/lot,
slippage 0,10 $), résultat en R net. Les trades pendant une fenêtre news sont exclus du verdict.

## H1 — Sens de la journée

Une alerte A+ n'est retenue que si :

- l'Initial Balance est terminée (bougie M1 clôturée à 09:20 NY ou après) ;
- **achat** : la dernière clôture M1 (bid) est **au-dessus du plus haut de l'IB** ;
- **vente** : la dernière clôture M1 (bid) est **sous le plus bas de l'IB**.

IB = plus haut / plus bas des bougies M1 XAUUSD (bid) de 08:20 à 09:20 NY. Le plan est calculé
à la première minute où la zone est A+ ET remplit cette condition. Entrée : ordre limite au bord
de la zone, valable jusqu'à 13:15 (comme la base).

## H2 — Confirmation par l'order flow

Pour chaque zone A+ (dès sa première alerte) :

1. Attendre le **contact** : bid ≤ bord haut de la zone (achat) ; ask ≥ bord bas (vente).
2. À partir de la bougie M1 du contact incluse, et pendant au plus **10 bougies M1**, attendre
   la première bougie qui **clôture dans le sens du trade** (achat : clôture > ouverture ;
   vente : clôture < ouverture) **et** dont le **delta GC** (vrai volume CME, acheteurs −
   vendeurs agressifs) de la même minute est dans le même sens (achat > 0 ; vente < 0).
3. Entrée **au marché** à l'ouverture de la bougie suivante (achat à l'ask + 0,10 $ ; vente au
   bid − 0,10 $). Cette bougie doit commencer avant 13:15.
4. Annulation si une bougie M1 clôture au-delà du bord distal de la zone avant le signal, ou si
   aucune confirmation dans les 10 bougies.
5. SL : celui du plan (bord distal ± 0,25 ATR, + spread pour une vente). TP : premier niveau
   d'hier à ≥ 1,5 R **depuis le prix d'entrée réel** ; aucun → pas de trade.

## Règle de décision

**Étape 1** (2024-10-02 → 2026-06-30, déjà vu pour la base mais jamais pour H1 / H2) :
une hypothèse passe si son R net moyen (hors news) est **> 0**. Sinon elle est abandonnée.

**Étape 2** (holdout : 2026-07-01 → veille du lancement, **une seule exécution**, enregistrée
dans `data/validation/hypotheses_holdout.json`, toute relance refusée), pour chaque hypothèse
qui a passé l'étape 1 :

- **CONFIRMÉE** si la borne basse de l'IC **97,5 %** (bootstrap, correction pour 2 hypothèses)
  du R moyen est > 0 ;
- **ENCOURAGEANTE, non prouvée** si le R moyen est > 0 mais l'IC contient 0 → à suivre en réel
  avec le journal, sans augmenter le risque ;
- **REJETÉE** sinon.

Le holdout ne couvre que ~4 mois (environ 40 à 70 trades par hypothèse) : il ne peut
confirmer qu'un effet nettement positif.
