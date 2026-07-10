"""Stratégie AMD asiatique (v1) — enveloppe de smc.core.find_amd_setup."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from smc.core import Setup, find_amd_setup


def find_setup(pair: str, data: dict, cfg: dict, pip: float,
               now: Optional[datetime] = None) -> Optional[Setup]:
    setup = find_amd_setup(pair, data["htf"], data["ltf"], cfg, pip,
                           now=now, d1_df=data.get("d1"))
    if setup is not None:
        setup.strategy = "amd_asian"
    return setup
