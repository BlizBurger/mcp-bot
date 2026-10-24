# Explorations (sans règle de trading, holdout jamais lu)

## Bulles GC — gros ordres agressifs (2026-10-24)

`python -m xauof.strategy.big_trades_study`, 2024-10 → 2026-06-30, 449 séances, séance New York.
Bulle = ordre agressif (transactions regroupées par horodatage) ≥ centile 99,5 de la séance
précédente (seuil médian 17 contrats) ; 100 142 bulles (223 par séance). Témoin : ordres
ordinaires (taille ≤ médiane), même nombre.

| | Bulles | Témoin | Écart |
|---|---|---|---|
| Suite après 1 min ($, sens de l'agresseur) | −0,18 [−0,20 ; −0,16] | −0,01 | −0,17 |
| Suite après 30 min | −0,25 [−0,33 ; −0,17] | −0,02 | −0,23 |
| Rebond au retour sur le niveau (±1 $) | 50,2 % | 49,8 % | +0,4 pt |
| Rebond au retour sur le niveau (±2 $) | 50,1 % | 50,2 % | −0,1 pt |

Lecture :

- Après une bulle, le prix revient **contre** l'agresseur d'environ 0,2 $. L'essentiel apparaît
  dès la première minute : c'est surtout un effet mécanique (le prix mesuré est le dernier prix
  touché par le balayage, le plus défavorable ; le marché se replace ensuite). La part qui
  continue après (−0,07 $ entre 1 et 30 min) est trop petite face au coût d'un aller-retour
  XAUUSD (≈ 0,45 $). Ni suivre ni contrer les bulles n'est exploitable.
- Le prix d'une bulle n'est pas un niveau : au retour, rebond et cassure à 50/50, comme le témoin.

Conclusion : pas d'effet exploitable sur l'or avec ces définitions. Pas de règle figée, holdout
intact. Le flux GC en direct n'est pas justifié pour afficher des bulles.
