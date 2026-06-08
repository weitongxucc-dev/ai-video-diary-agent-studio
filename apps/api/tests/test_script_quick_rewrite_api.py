from fastapi.testclient import TestClient

import app.main as main_module


client = TestClient(main_module.app)


def test_script_rewrite_intents_endpoint_returns_compact_shortcuts():
    response = client.get("/api/script/rewrite-intents")

    assert response.status_code == 200
    payload = response.json()
    assert [intent["id"] for intent in payload["intents"]] == [
        "sharper_angle",
        "more_conversational",
        "more_real_case",
        "shorter",
        "more_review_like",
    ]
    assert payload["intents"][0] == {
        "id": "sharper_angle",
        "label": "更犀利",
        "description": "强化冲突和判断，让观点更直接。",
        "prompt_hint": "把这一段改得更犀利，先给明确判断，再解释原因，减少铺垫。",
    }
