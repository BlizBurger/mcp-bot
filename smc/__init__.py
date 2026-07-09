"""SMC/AMD Setup Scanner — outil d'alerte (jamais d'exécution d'ordres).

AVERTISSEMENTS PERMANENTS — à ne jamais retirer du code, des logs ou de l'UI.
"""

WARNINGS = [
    "Le SMC/ICT n'a pas de preuve statistique académique rigoureuse d'edge. "
    "Cette automatisation est une approximation mécanique d'une méthode "
    "discrétionnaire, pas une garantie.",
    "Risque d'overfitting : les paramètres (tolérance equal highs/lows, "
    "multiplicateur ATR, seuils de corrélation) sont arbitraires, pas "
    "optimisés rigoureusement.",
    "Le tick_volume MT5 n'est pas un vrai volume centralisé (le forex n'en a pas).",
    "Le filtre news dépend à 100% du remplissage manuel de news_today.txt "
    "chaque matin — ce n'est pas un flux temps réel.",
    "Le backtest simule sur mèches M15, pas tick par tick : spread et slippage "
    "réels non modélisés, les résultats réels seront probablement moins bons.",
]

__version__ = "1.0.0"
