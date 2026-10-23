from xauof.web.telegram import chat_ids


def test_chat_ids_deduplicated():
    upd = {"result": [{"message": {"chat": {"id": 42, "first_name": "Alex"}}},
                      {"message": {"chat": {"id": 42, "first_name": "Alex"}}},
                      {"edited_message": {"chat": {"id": -100, "title": "Groupe"}}}, {"update_id": 3}]}
    assert chat_ids(upd) == [(42, "Alex"), (-100, "Groupe")]
