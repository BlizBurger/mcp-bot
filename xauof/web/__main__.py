"""Lance l'interface : python -m xauof.web            (MT5 ouvert)
                       python -m xauof.web --phone    (+ mot de passe, pour le téléphone via Tailscale)
                       python -m xauof.web --demo     (données synthétiques, marché fermé)"""
from __future__ import annotations

import argparse
import copy
import os
import threading
import webbrowser
from pathlib import Path


def demo_config(cfg: dict) -> dict:
    """La démo ne touche ni au vrai journal (dossier data/demo/ à part) ni à Telegram."""
    os.environ["XAUOF_NO_TELEGRAM"] = "1"
    cfg = copy.deepcopy(cfg)
    cfg["data"]["dir"] = str(Path(cfg["data"]["dir"]) / "demo")
    return cfg


def phone_password() -> str:
    """Mot de passe de l'interface (XAUOF_WEB_PASSWORD dans .env), obligatoire avec --phone."""
    from dotenv import load_dotenv

    from xauof.config import ROOT

    load_dotenv(ROOT / ".env")
    pw = (os.getenv("XAUOF_WEB_PASSWORD") or "").strip()
    if len(pw) < 10:
        raise SystemExit("--phone : ajoute dans .env une ligne XAUOF_WEB_PASSWORD=... (au moins 10 caractères, "
                         "différent de tes autres mots de passe), puis relance.")
    return pw


def main() -> None:
    import uvicorn

    from xauof.config import load_config
    from xauof.web.server import create_app
    from xauof.web.sources import DemoSource, MT5Source

    p = argparse.ArgumentParser(description="Interface XAUUSD OF")
    p.add_argument("--demo", action="store_true", help="marché synthétique rejoué en accéléré")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--phone", action="store_true", help="exige le mot de passe XAUOF_WEB_PASSWORD (accès Tailscale)")
    args = p.parse_args()
    password = phone_password() if args.phone else None
    cfg = load_config()
    if args.demo:
        cfg = demo_config(cfg)
    source = DemoSource(cfg) if args.demo else MT5Source(cfg)
    url = f"http://127.0.0.1:{args.port}"
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"Interface : {url}  (Ctrl+C pour arrêter)")
    if password:
        print(f"Téléphone : mot de passe actif. Une seule fois sur le PC : tailscale serve --bg {args.port}\n"
              "            puis ouvre sur le téléphone l'adresse https://...ts.net affichée par Tailscale.")
    # toujours 127.0.0.1 : le téléphone passe par « tailscale serve », jamais par un port ouvert
    uvicorn.run(create_app(cfg, source, password=password), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
