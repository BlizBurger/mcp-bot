# Plans proposés avec les alertes A+ — règles figées

Figé le 2026-10-23, **avant** toute mesure sur l'historique réel. Toute modification après la
mesure doit être notée ici, avec sa date et sa raison, et la mesure refaite sur une période
neuve.

## Ce qu'est une alerte A+ (critères choisis par Alex, non validés)

1. Zone FVG ou OB (détecteur M5 du moteur, sans anticipation) à moins de 0,5 × ATR M5 d'un
   niveau de la séance New York précédente (POC/VAH/VAL volume et TPO, HVN, LVN, single prints).
2. Dans le sens du contexte : achat au-dessus de la Value Area ou sur la VAL, vente sous la
   Value Area ou sur la VAH.
3. Uniquement entre 08:20 et 13:15 (New York).

## Plan proposé (calculé à la première alerte de la zone)

| | Achat | Vente |
|---|---|---|
| Entrée | ordre limite au bord haut de la zone (prix ask) | ordre limite au bord bas de la zone (prix bid) |
| SL | bord bas − 0,25 × ATR M5 | bord haut + spread + 0,25 × ATR M5 |
| TP | premier niveau d'hier au-dessus, à ≥ 1,5 R, moins 0,1 × ATR | premier niveau d'hier en dessous, à ≥ 1,5 R, plus spread + 0,1 × ATR |
| Lots | risque 1 % de min(equity, capital initial) (100 $ sur 10k ; 0,5 % jusqu'au 2026-10-25), coûts estimés compris | idem |

Pas de niveau à ≥ 1,5 R → **pas de plan** (« RR insuffisant »).

## Mesure (`python -m xauof.web.alert_stats`)

- L'ordre limite reste valable jusqu'à 13:15 NY. Ensuite : SL, TP, ou clôture forcée à 13:30.
- Bougies M1 bid + spread. Sur une même bougie, le SL passe avant le TP. Le TP n'est pas compté
  sur la bougie d'entrée. Slippage de 0,10 $ sur le SL et la clôture forcée. Commission
  estimée 6 $/lot aller-retour.
- Résultat en R net (perte au SL, coûts compris = −1 R environ).
- Période : tout l'historique **avant** le holdout (2026-07-01). Le holdout n'est pas touché.

## Règle de décision (figée)

Sur les trades remplis hors fenêtre news, IC 95 % bootstrap de la moyenne en R :

- borne basse > 0 → « positif sur l'historique », à confirmer sur le holdout avant d'y croire ;
- IC qui contient 0 → « non validé » : les niveaux restent affichés, à titre indicatif ;
- borne haute < 0 → « négatif » : les niveaux restent affichés, avec la mention qu'ils auraient
  perdu de l'argent appliqués à l'aveugle.

Dans tous les cas, l'interface et les alertes indiquent « Non validé : à toi de confirmer »
tant que le holdout n'a pas confirmé un résultat positif.
