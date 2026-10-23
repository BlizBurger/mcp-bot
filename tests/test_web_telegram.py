from xauof.web.telegram import chat_ids


def test_chat_ids_deduplicated():
    upd = {"result": [{"message": {"chat": {"id": 42, "first_name": "Alex"}}},
                      {"message": {"chat": {"id": 42, "first_name": "Alex"}}},
                      {"edited_message": {"chat": {"id": -100, "title": "Groupe"}}}, {"update_id": 3}]}
    assert chat_ids(upd) == [(42, "Alex"), (-100, "Groupe")]


def test_token_diagnostic():
    from xauof.web.telegram import token_diagnostic

    good = "7123456789:" + "A" * 35
    assert token_diagnostic(good) == []
    assert any("coupé" in p for p in token_diagnostic(good[:-5]))
    assert any("espaces" in p for p in token_diagnostic(" " + good))
    assert any("identifiant" in p for p in token_diagnostic("xauusd_of_bot"))
    assert any("guillemets" in p for p in token_diagnostic('"' + good + '"'))


def test_clean_chat_id():
    from xauof.web.telegram import clean_chat_id

    assert clean_chat_id("6291755478      (Alex🫥)") == "6291755478"
    assert clean_chat_id(" 6291755478 ") == "6291755478"
    assert clean_chat_id("-1001234567890") == "-1001234567890"
    assert clean_chat_id("") is None and clean_chat_id(None) is None
