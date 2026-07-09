"""Chargement de la configuration (config.yaml) et des secrets (.env)."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = PROJECT_ROOT / "config.yaml"


def load_config(path: Path | None = None) -> dict:
    with open(path or CONFIG_FILE, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    # Résoudre les chemins relatifs par rapport à la racine du projet
    for key, value in cfg.get("paths", {}).items():
        cfg["paths"][key] = str(PROJECT_ROOT / value)
    cfg["news"]["file"] = str(PROJECT_ROOT / cfg["news"]["file"])
    return cfg


def load_env() -> dict:
    """Charge .env (via python-dotenv si présent) et retourne les secrets."""
    try:
        from dotenv import load_dotenv
        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        pass
    return {
        "telegram_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
        "mt5_login": os.getenv("MT5_LOGIN", ""),
        "mt5_password": os.getenv("MT5_PASSWORD", ""),
        "mt5_server": os.getenv("MT5_SERVER", ""),
        "mt5_path": os.getenv("MT5_PATH", ""),
    }


def pip_size_fallback(pair: str, cfg: dict) -> float:
    """Taille de pip sans MT5 : 0.01 pour les paires en JPY, 0.0001 sinon."""
    ps = cfg.get("pip_sizes", {})
    if pair.upper().endswith("JPY"):
        return float(ps.get("jpy_quote", 0.01))
    return float(ps.get("default", 0.0001))
