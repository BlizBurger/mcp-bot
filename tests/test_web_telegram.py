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
