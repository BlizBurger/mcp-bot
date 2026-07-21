"""Descriptions des stratégies — source unique réutilisée par le dashboard,
le rapport HTML et STRATEGIES.md."""

STRATEGY_INFO = {
    "sweep_bos": {
        "titre": "Sweep 4H + BOS M15 (SMC)",
        "famille": "Smart Money Concepts",
        "edge": "Faible (méthode discrétionnaire mécanisée, pas d'edge prouvé)",
        "resume": "Prise de liquidité sur un niveau H4 puis confirmation par "
                  "cassure de structure M15, entrée sur la zone de retour.",
        "fonctionnement": [
            "Repère les niveaux de liquidité H4 (swings, equal highs/lows, "
            "hauts/bas de session).",
            "Attend un SWEEP : une mèche H4 dépasse le niveau puis clôture "
            "dedans, avec un rejet net (mèche ≥ 60 % de la bougie).",
            "Confirme par un BOS M15 : cassure en clôture d'un swing dans le "
            "sens opposé au sweep, dans les 20 bougies.",
            "Entre en ordre limite sur la confluence la plus proche (FVG, "
            "IFVG, Breaker), SL derrière la mèche du sweep, TP sur la "
            "liquidité opposée.",
        ],
        "params": "sl_buffer_pct, min_rr, entry_zone_kinds, min_score, amd_*",
    },
    "amd_asian": {
        "titre": "AMD — Accumulation/Manipulation/Distribution (session asiatique)",
        "famille": "Smart Money Concepts",
        "edge": "Faible (méthode discrétionnaire mécanisée)",
        "resume": "Range asiatique comme accumulation, sweep de liquidité "
                  "comme manipulation, FVG/OB comme distribution.",
        "fonctionnement": [
            "Délimite le range de la session asiatique (accumulation).",
            "Attend un sweep du haut/bas du range pendant les killzones "
            "Londres/NY (manipulation).",
            "Entre sur un FVG ou Order Block aligné avec le biais H4 "
            "(distribution), avec filtres volume et corrélation.",
        ],
        "params": "risk_reward, sl_buffer_pips, volume_mult, correlation_*",
    },
    "ema_rsi": {
        "titre": "EMA + RSI (pullback de momentum)",
        "famille": "Momentum / suivi de tendance",
        "edge": "Moyen (le momentum est l'anomalie la mieux documentée)",
        "resume": "Trade dans le sens de la tendance (EMA) sur un repli du RSI "
                  "qui redémarre.",
        "fonctionnement": [
            "Tendance définie par la position de l'EMA rapide vs EMA lente.",
            "En tendance haussière, entre LONG quand le RSI repasse au-dessus "
            "de son seuil (pullback terminé) ; symétrique en baissier.",
            "SL en multiple d'ATR, TP = R:R × risque. Filtre de tendance H4 "
            "optionnel.",
        ],
        "params": "ema_fast, ema_slow, rsi_period, rsi_buy/sell, atr_sl_mult, "
                  "risk_reward, htf_filter",
    },
    "donchian": {
        "titre": "Donchian Breakout (Turtle)",
        "famille": "Suivi de tendance (cassure)",
        "edge": "Le mieux documenté académiquement (time-series momentum)",
        "resume": "Achète les cassures du plus haut des N dernières bougies, "
                  "vend les cassures du plus bas.",
        "fonctionnement": [
            "Calcule le plus haut et le plus bas des N dernières bougies "
            "(canal de Donchian).",
            "Entre LONG à la cassure en clôture du plus haut, SHORT à la "
            "cassure du plus bas.",
            "SL en multiple d'ATR, TP = R:R × risque. C'est le cœur du "
            "système des Turtles.",
        ],
        "params": "channel, atr_sl_mult, risk_reward, htf_filter",
    },
    "bollinger": {
        "titre": "Bollinger — retour à la moyenne",
        "famille": "Contre-tendance / mean reversion",
        "edge": "Moyen (marche en range, souffre en tendance)",
        "resume": "Parie sur le retour vers la moyenne quand le prix sort "
                  "d'une bande de Bollinger puis y revient.",
        "fonctionnement": [
            "Bandes = moyenne mobile ± k × écart-type.",
            "Entre LONG quand le prix, sorti sous la bande basse, y repasse "
            "au-dessus ; SHORT symétrique sur la bande haute.",
            "SL en multiple d'ATR, TP = R:R × risque (souvent plus court).",
        ],
        "params": "period, std, atr_sl_mult, risk_reward, htf_filter",
    },
    "ema_cross": {
        "titre": "Croisement d'EMA",
        "famille": "Suivi de tendance (référence de base)",
        "edge": "Faible seul (référence de comparaison)",
        "resume": "Entre quand l'EMA rapide croise l'EMA lente. Le classique "
                  "des classiques, utile comme point de repère.",
        "fonctionnement": [
            "EMA rapide croise au-dessus de l'EMA lente → LONG.",
            "EMA rapide croise en-dessous → SHORT.",
            "SL en multiple d'ATR, TP = R:R × risque.",
        ],
        "params": "ema_fast, ema_slow, atr_sl_mult, risk_reward, htf_filter",
    },
}
