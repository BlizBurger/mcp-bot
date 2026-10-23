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
