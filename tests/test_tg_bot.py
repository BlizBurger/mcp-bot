"""Bot Telegram : seul ton chat est écouté, décisions et notes enregistrées, commandes lisibles."""
import copy
import os

import pandas as pd
import pytest

from xauof.config import load_config
from xauof.web.server import Hub
from xauof.web.sources import DemoSource
from xauof.web.tg_bot import Decisions, TelegramBot

ME, OTHER = "6291755478", "111111111"
NOW = pd.Timestamp("2026-10-23 15:00", tz="UTC")


@pytest.fixture
def bot(tmp_path):
    os.environ["XAUOF_NO_TELEGRAM"] = "1"
    cfg = copy.deepcopy(load_config())
    cfg["data"]["dir"] = str(tmp_path / "data")
    hub = Hub(cfg, DemoSource(cfg, speed=0.0))
    sent, calls = [], []

    def send(text, buttons=None, reply_to=None):
        sent.append((text, reply_to))
        return 900 + len(sent)

    b = TelegramBot(cfg, hub, "jeton", ME, send=send, api=lambda m, **p: calls.append((m, p)) or {"result": []},
                    clock=lambda: NOW)
    hub.alerts.messages[500] = {"cle": "z1:pret", "texte": "Setup A+ en préparation : VENTE …", "t": NOW.isoformat()}
    return b, sent, calls, cfg


def msg(chat, text, reply_to=None, mid=10):
    m = {"message_id": mid, "chat": {"id": int(chat)}, "text": text}
    if reply_to:
        m["reply_to_message"] = {"message_id": reply_to}
    return {"update_id": 1, "message": m}


def test_other_chats_are_ignored(bot):
    b, sent, calls, cfg = bot
    b.handle(msg(OTHER, "/etat"))
    b.handle({"update_id": 2, "callback_query": {"id": "q", "data": "take|z1:pret",
                                                  "message": {"message_id": 500, "chat": {"id": int(OTHER)}}}})
    assert sent == [] and calls == [] and Decisions(cfg).load() == []


def test_buttons_record_decision_then_reply_adds_note(bot):
    b, sent, calls, cfg = bot
    b.handle({"update_id": 3, "callback_query": {"id": "q", "data": "skip|z1:pret",
                                                  "message": {"message_id": 500, "chat": {"id": int(ME)}}}})
    d = Decisions(cfg).load()[0]
    assert d["cle"] == "z1:pret" and d["decision"] == "passé" and "VENTE" in d["texte"]
    assert [c[0] for c in calls] == ["answerCallbackQuery", "editMessageReplyMarkup"]
    assert sent[-1][1] == 500 and "tu passes" in sent[-1][0]
    b.handle(msg(ME, "zone trop loin du prix", reply_to=901))          # réponse à la confirmation du bot
    b.handle(msg(ME, "et news dans 10 min", reply_to=500))             # réponse directe à l'alerte
    notes = Decisions(cfg).load()[0]["notes"]
    assert [n["texte"] for n in notes] == ["zone trop loin du prix", "et news dans 10 min"]


def test_commands(bot):
    b, sent, calls, cfg = bot
    for c in ("/etat", "/setups", "/niveaux", "/semaine", "/aide"):
        b.handle(msg(ME, c))
    etat, setups, niveaux, semaine, aide = (t for t, _ in sent)
    assert "Prix" in etat and "trades 1/2" in etat
    assert "VENTE" in setups or "Aucun setup" in setups
    assert "POC vol" in niveaux and "Semaine du" in semaine and "/pause" in aide
    b.handle(msg(ME, "/pause"))
    assert b.hub.alerts.paused(NOW) and not b.hub.alerts.paused(NOW + pd.Timedelta(days=1))
    b.handle(msg(ME, "/reprendre"))
    assert not b.hub.alerts.paused(NOW)


def test_alerts_still_sent_after_limit_with_warning(monkeypatch):
    from xauof.strategy.zones import Zone
    from xauof.web import setups as su

    monkeypatch.setenv("XAUOF_NO_TELEGRAM", "1")
    ac = su.AlertCenter()
    z = Zone("z9", "FVG", -1, 100.0, 101.0, NOW)
    z.state = "eligible"
    ac.from_setups([{"zone": z, "niveaux": ["VAH vol 101.00"], "contexte": "vente sur la VAH", "etat": "en attente du prix"}],
                   NOW, None, "limite atteinte : 2 trades aujourd'hui (max 2)")
    assert len(ac.feed) == 1 and "pour info seulement" in ac.feed[0]["texte"]
    ac.paused_until = NOW + pd.Timedelta(hours=1)
    assert ac.paused(NOW)
