"""Lance l'interface : python -m xauof.web            (MT5 ouvert)
                       python -m xauof.web --demo     (données synthétiques, marché fermé)"""
from __future__ import annotations

import argparse
import threading
import webbrowser


def main() -> None:
    import uvicorn

    from xauof.config import load_config
    from xauof.web.server import create_app
    from xauof.web.sources import DemoSource, MT5Source

    p = argparse.ArgumentParser(description="Interface XAUUSD OF")
    p.add_argument("--demo", action="store_true", help="marché synthétique rejoué en accéléré")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-browser", action="store_true")
    args = p.parse_args()
    cfg = load_config()
    source = DemoSource(cfg) if args.demo else MT5Source(cfg)
    url = f"http://127.0.0.1:{args.port}"
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"Interface : {url}  (Ctrl+C pour arrêter)")
    uvicorn.run(create_app(cfg, source), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
