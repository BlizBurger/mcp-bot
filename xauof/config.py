"""Chargement de config.yaml et des secrets (.env)."""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def load_config(path: str | Path = ROOT / "config.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def secret(name: str) -> str:
    """Lit un secret depuis l'environnement (.env chargé), erreur claire si absent."""
    load_dotenv(ROOT / ".env")
    value = os.getenv(name, "")
    if not value:
        raise RuntimeError(f"{name} manquant : renseigne-le dans .env (voir .env.example)")
    return value
