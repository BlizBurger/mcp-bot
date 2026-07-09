"""Logger commun : console + fichier avec rotation quotidienne."""

from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from smc import WARNINGS

_FORMAT = "%(asctime)s %(levelname)-8s %(name)s — %(message)s"


def setup_logging(log_dir: str, name: str = "smc",
                  level: int = logging.INFO) -> logging.Logger:
    """Configure et retourne le logger racine `smc`.

    - Console : niveau demandé.
    - Fichier `<log_dir>/<name>.log` : rotation à minuit, 30 jours conservés,
      suffixe date (smc.log.2026-07-09) pour relire ce qui s'est passé un jour
      donné.
    """
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("smc")
    if logger.handlers:  # déjà configuré (appel répété dans les tests/dashboard)
        return logger
    logger.setLevel(level)

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(_FORMAT))
    logger.addHandler(console)

    file_handler = TimedRotatingFileHandler(
        Path(log_dir) / f"{name}.log", when="midnight", backupCount=30,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(_FORMAT))
    logger.addHandler(file_handler)
    return logger


def log_warnings_banner(logger: logging.Logger) -> None:
    """Affiche les limites connues de l'outil — à chaque démarrage, exprès."""
    logger.warning("=" * 70)
    logger.warning("LIMITES CONNUES DE CET OUTIL (à garder en tête) :")
    for w in WARNINGS:
        logger.warning("  • %s", w)
    logger.warning("=" * 70)
