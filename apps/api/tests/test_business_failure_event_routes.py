"""Lost creation replies are read back through the real member/input boundary."""
from uuid import uuid4

import app.main as main
import pytest
from test_creation_request_api import client_state, create, prepare_one


def test_lost_creation_reply_is_read_by_original_input_without_creating_again(client_state):
    c = client_state
    c.client.app.add_api_route('/api/assistant/creation-request', main.read_current_input_creation_request, methods=['GET'])
    message = '制作1条视频'
    chat = c.conversations.create('request-test', c.member.member_id, 'home', 'home', '目标')
    c.conversations.save_draft(chat['id'], 'request-test', c.member.member_id, message, 0)
    turn, _ = c.conversations.create_turn_idempotent(chat['id'], 'request-test', c.member.member_id,
        message, str(uuid4()), 1)
    path = '/api/assistant/creation-request?assistant_turn_id=' + turn['id']
    absent = c.client.get(path, headers=c.alice)
    assert absent.status_code == 200 and absent.json() is None
    saved = create(c, target_count=1, assistant_turn_id=turn['id'], request_key=turn['id'],
                   intent={'kind': 'goal_delegation'})
    assert saved.status_code == 200, saved.text
    for _ in range(2):
        found = c.client.get(path, headers=c.alice)
        assert found.status_code == 200 and found.json()['id'] == saved.json()['id']
        assert found.json()['confirmed_count'] == 0
    with c.repository._connect() as conn:
        assert conn.execute('SELECT count(*) FROM creation_requests').fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM creation_tasks').fetchone()[0] == 0
    assert c.client.get(path, headers=c.bob).status_code == 404
    assert c.client.get(path, headers={**c.alice, 'X-Assistant-Input-ID':str(uuid4())}).status_code == 409
    assert c.repository.creation_requests.find_creation_request_by_key('another-workspace', turn['id'], viewer_user_id=c.member.member_id) is None
    c.conversations.finish_turn(turn['id'], 'failed', result={'kind':'failure', 'message':'response lost'})
    assert c.client.get(path, headers=c.alice).status_code == 409


@pytest.mark.parametrize("action", ["replan", "confirm"])
def test_input_ending_during_preflight_cannot_save_or_adopt(client_state, monkeypatch, action):
    c = client_state
    base, data, confirm_url, confirmation = prepare_one(c, {"kind": "goal_delegation", "topic_title": "原主题"})
    chat = c.conversations.create("request-test", c.member.member_id, "home", "home", "继续制作")
    c.conversations.save_draft(chat["id"], "request-test", c.member.member_id, "继续制作", 0)
    turn, _ = c.conversations.create_turn_idempotent(chat["id"], "request-test", c.member.member_id,
        "继续制作", str(uuid4()), 1)
    preflight = main._creation_plan_preflight
    def input_ends(*args, **kwargs):
        result = preflight(*args, **kwargs)
        c.conversations.finish_turn(turn["id"], "completed", "输入已结束")
        return result
    monkeypatch.setattr(main, "_creation_plan_preflight", input_ends)
    if action == "replan":
        response = c.client.post(base + "/windows/" + data["current_window"]["id"] + "/replan?assistant_turn_id=" + turn["id"],
            headers=c.alice, json={"expected_revision": data["revision"], "goals": [{"goal": "新主题"}]})
    else:
        response = c.client.post(confirm_url, headers=c.alice, json={**confirmation,
            "adoption_kind": "delegated_system", "assistant_turn_id": turn["id"]})
    assert response.status_code == 409, response.text
    assert c.client.get(base, headers=c.alice).json()["revision"] == data["revision"]
    assert c.repository.task_records.list_creation_tasks("request-test") == []
