"""Chargement du fichier news manuel (news_today.txt).

⚠️ Ce filtre dépend à 100% de la discipline de remplissage du fichier chaque
matin — ce n'est PAS un flux temps réel. Fichier absent ou vide = AUCUN filtre
news actif ce jour-là (le scanner le signale en WARNING à chaque démarrage).
"""

from __future__ import annotations

import logging
from pathlib import Path

from smc.core import NewsEvent, parse_news

log = logging.getLogger("smc.news")


def load_news(path: str) -> list[NewsEvent]:
    p = Path(path)
    if not p.exists():
        log.warning("Fichier news introuvable (%s) — filtre news INACTIF aujourd'hui", path)
        return []
    text = p.read_text(encoding="utf-8")
    events = parse_news(text)
    n_lines = len([ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")])
    if n_lines > len(events):
        log.warning("%d ligne(s) de %s ignorée(s) car mal formée(s) "
                    "(format attendu : HH:MM DEVISE Nom Impact)",
                    n_lines - len(events), p.name)
    if not events:
        log.warning("Aucune news chargée depuis %s — filtre news INACTIF", p.name)
    else:
        log.info("%d news chargée(s), dont %d High impact",
                 len(events), sum(1 for e in events if e.is_high))
    return events
