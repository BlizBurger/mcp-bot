"""API de l'interface, avec la source démo (sans MT5 ni réseau)."""
import copy
import os

import pytest
from fastapi.testclient import TestClient

from xauof.config import load_config
from xauof.web.server import create_app
from xauof.web.sources import DemoSource


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    os.environ["XAUOF_NO_TELEGRAM"] = "1"
    cfg = copy.deepcopy(load_config())
    cfg["data"]["dir"] = str(tmp_path_factory.mktemp("web") / "data")
    with TestClient(create_app(cfg, DemoSource(cfg, speed=0.0), alert_loop=False)) as c:
        yield c


def test_index_and_static(client):
    assert "XAUUSD" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/card.js").status_code == 200 and "card-modal" in client.get("/").text


def test_state_payload(client):
    s = client.get("/api/state").json()
    assert s["source"].startswith("DÉMO") and s["heure_ny"] == "10:30"
    assert s["prix"]["ask"] > s["prix"]["bid"]
    assert s["niveaux"] and all({"nom", "prix", "aide", "distance"} <= set(x) for x in s["niveaux"])
    assert s["ib"]["definitive"] is True
    assert {"delta_5min", "cvd", "ticks_5min"} <= set(s["orderflow"])
    assert s["compte"]["reste_arret_interne"] == pytest.approx(150 - s["compte"]["perte_jour"])
    for st in s["setups"]:                                   # chaque setup A+ a ses deux critères
        assert st["niveaux"] and st["contexte"]
    assert len(s["alertes"]) == len(s["setups"])             # une alerte « en préparation » par setup
    assert s["spread_bloque"] is None                        # 60 minutes complètes d'historique


def test_bars_and_orderflow_aligned(client):
    m1 = client.get("/api/bars?tf=1").json()
    m5 = client.get("/api/bars?tf=5").json()
    of = client.get("/api/orderflow").json()
    assert m1 and m5 and of and len(m5) < len(m1)
    assert m1[-1]["time"] == of[-1]["time"]
    assert all(b["low"] <= min(b["open"], b["close"]) and b["high"] >= max(b["open"], b["close"]) for b in m1)
    of5 = client.get("/api/orderflow?tf=5").json()
    assert {x["time"] for x in of5} <= {b["time"] for b in m5} and len(of5) < len(of)   # même grille que les bougies M5


def test_journal_sync_and_annotate(client):
    r = client.post("/api/journal/sync").json()
    assert r["importes"] == 2
    j = client.get("/api/journal").json()
    tid = j["trades"][0]["id"]
    t = client.post(f"/api/journal/{tid}", json={"setup": "FVG sur VAL", "criteres": ["Zone FVG/OB"], "note": "ok"}).json()
    assert t["setup"] == "FVG sur VAL"
    client.post("/api/journal/sync")                          # resynchroniser garde l'annotation
    j = client.get("/api/journal").json()
    assert next(x for x in j["trades"] if x["id"] == tid)["criteres"] == ["Zone FVG/OB"]
    assert client.post("/api/journal/999", json={}).status_code == 404


def test_price_is_live_tick(client):
    s = client.get("/api/state").json()
    assert s["prix"]["direct"] is True and s["prix"]["ask"] > s["prix"]["bid"]
    assert s["prix"]["spread_pts"] == round((s["prix"]["ask"] - s["prix"]["bid"]) / 0.01)


def test_demo_isolated_from_real_journal_and_telegram(monkeypatch):
    from xauof.web import journal as jr
    from xauof.web.__main__ import demo_config

    monkeypatch.delenv("XAUOF_NO_TELEGRAM", raising=False)
    cfg = load_config()
    demo = demo_config(cfg)
    assert jr.journal_path(demo) != jr.journal_path(cfg)
    assert jr.journal_path(demo).parent.parent.name == "demo"
    assert os.environ["XAUOF_NO_TELEGRAM"] == "1"


def test_password_protects_pages_and_api(tmp_path):
    from xauof.web.server import COOKIE

    cfg = copy.deepcopy(load_config())
    cfg["data"]["dir"] = str(tmp_path / "data")
    app = create_app(cfg, DemoSource(cfg, speed=0.0), alert_loop=False, password="un-long-secret")
    with TestClient(app) as c:
        assert c.get("/api/state").status_code == 401
        r = c.get("/", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login"
        assert c.get("/static/app.js", follow_redirects=False).status_code == 303
        bad = c.post("/login", content="password=faux", headers={"Content-Type": "application/x-www-form-urlencoded"},
                     follow_redirects=False)
        assert bad.headers["location"] == "/login?e=1" and COOKIE not in c.cookies
        ok = c.post("/login", content="password=un-long-secret",
                    headers={"Content-Type": "application/x-www-form-urlencoded"}, follow_redirects=False)
        assert ok.status_code == 303 and "httponly" in ok.headers["set-cookie"].lower()
        assert c.get("/api/state").status_code == 200 and "XAUUSD" in c.get("/").text


def test_phone_mode_requires_a_password(monkeypatch):
    from xauof.web.__main__ import phone_password

    monkeypatch.setenv("XAUOF_WEB_PASSWORD", "court")
    with pytest.raises(SystemExit):
        phone_password()
    monkeypatch.setenv("XAUOF_WEB_PASSWORD", "assez-long-pour-passer")
    assert phone_password() == "assez-long-pour-passer"
