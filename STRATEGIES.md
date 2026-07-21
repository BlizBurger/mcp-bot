# Stratégies du bot

Chaque stratégie est testable et comparable sur le même moteur (`python -m smc.backtest --compare-strategies --split 0.75`).

> ⚠️ Un edge sur backtest ne prouve rien tant qu'il ne tient pas out-of-sample ET en démo réelle. Voir les avertissements du projet.


## Sweep 4H + BOS M15 (SMC)

- **Nom technique** : `sweep_bos`
- **Famille** : Smart Money Concepts
- **Edge** : Faible (méthode discrétionnaire mécanisée, pas d'edge prouvé)
- **En bref** : Prise de liquidité sur un niveau H4 puis confirmation par cassure de structure M15, entrée sur la zone de retour.

**Fonctionnement :**
1. Repère les niveaux de liquidité H4 (swings, equal highs/lows, hauts/bas de session).
1. Attend un SWEEP : une mèche H4 dépasse le niveau puis clôture dedans, avec un rejet net (mèche ≥ 60 % de la bougie).
1. Confirme par un BOS M15 : cassure en clôture d'un swing dans le sens opposé au sweep, dans les 20 bougies.
1. Entre en ordre limite sur la confluence la plus proche (FVG, IFVG, Breaker), SL derrière la mèche du sweep, TP sur la liquidité opposée.

**Paramètres (`config.yaml`)** : `sl_buffer_pct, min_rr, entry_zone_kinds, min_score, amd_*`

## AMD — Accumulation/Manipulation/Distribution (session asiatique)

- **Nom technique** : `amd_asian`
- **Famille** : Smart Money Concepts
- **Edge** : Faible (méthode discrétionnaire mécanisée)
- **En bref** : Range asiatique comme accumulation, sweep de liquidité comme manipulation, FVG/OB comme distribution.

**Fonctionnement :**
1. Délimite le range de la session asiatique (accumulation).
1. Attend un sweep du haut/bas du range pendant les killzones Londres/NY (manipulation).
1. Entre sur un FVG ou Order Block aligné avec le biais H4 (distribution), avec filtres volume et corrélation.

**Paramètres (`config.yaml`)** : `risk_reward, sl_buffer_pips, volume_mult, correlation_*`

## EMA + RSI (pullback de momentum)

- **Nom technique** : `ema_rsi`
- **Famille** : Momentum / suivi de tendance
- **Edge** : Moyen (le momentum est l'anomalie la mieux documentée)
- **En bref** : Trade dans le sens de la tendance (EMA) sur un repli du RSI qui redémarre.

**Fonctionnement :**
1. Tendance définie par la position de l'EMA rapide vs EMA lente.
1. En tendance haussière, entre LONG quand le RSI repasse au-dessus de son seuil (pullback terminé) ; symétrique en baissier.
1. SL en multiple d'ATR, TP = R:R × risque. Filtre de tendance H4 optionnel.

**Paramètres (`config.yaml`)** : `ema_fast, ema_slow, rsi_period, rsi_buy/sell, atr_sl_mult, risk_reward, htf_filter`

## Donchian Breakout (Turtle)

- **Nom technique** : `donchian`
- **Famille** : Suivi de tendance (cassure)
- **Edge** : Le mieux documenté académiquement (time-series momentum)
- **En bref** : Achète les cassures du plus haut des N dernières bougies, vend les cassures du plus bas.

**Fonctionnement :**
1. Calcule le plus haut et le plus bas des N dernières bougies (canal de Donchian).
1. Entre LONG à la cassure en clôture du plus haut, SHORT à la cassure du plus bas.
1. SL en multiple d'ATR, TP = R:R × risque. C'est le cœur du système des Turtles.

**Paramètres (`config.yaml`)** : `channel, atr_sl_mult, risk_reward, htf_filter`

## Bollinger — retour à la moyenne

- **Nom technique** : `bollinger`
- **Famille** : Contre-tendance / mean reversion
- **Edge** : Moyen (marche en range, souffre en tendance)
- **En bref** : Parie sur le retour vers la moyenne quand le prix sort d'une bande de Bollinger puis y revient.

**Fonctionnement :**
1. Bandes = moyenne mobile ± k × écart-type.
1. Entre LONG quand le prix, sorti sous la bande basse, y repasse au-dessus ; SHORT symétrique sur la bande haute.
1. SL en multiple d'ATR, TP = R:R × risque (souvent plus court).

**Paramètres (`config.yaml`)** : `period, std, atr_sl_mult, risk_reward, htf_filter`

## Croisement d'EMA

- **Nom technique** : `ema_cross`
- **Famille** : Suivi de tendance (référence de base)
- **Edge** : Faible seul (référence de comparaison)
- **En bref** : Entre quand l'EMA rapide croise l'EMA lente. Le classique des classiques, utile comme point de repère.

**Fonctionnement :**
1. EMA rapide croise au-dessus de l'EMA lente → LONG.
1. EMA rapide croise en-dessous → SHORT.
1. SL en multiple d'ATR, TP = R:R × risque.

**Paramètres (`config.yaml`)** : `ema_fast, ema_slow, atr_sl_mult, risk_reward, htf_filter`