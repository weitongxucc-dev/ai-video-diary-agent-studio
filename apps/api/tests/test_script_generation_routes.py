from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.creation.script_generation_routes import build_script_generation_router


def test_generation_http_keeps_request_identity_and_dispatches_only_new_acceptance():
    request_id, conversation_id, turn_id = map(str, (uuid4(), uuid4(), uuid4()))
    receipt = dict(request_id=request_id, task_id="task", conversation_id=conversation_id,
                   turn_id=turn_id, turn_status="running", run_id="run", run_status="running",
                   status="running", requested_effect="draft", stop_requested=False,
                   result_stale=False, script_id=None, script_updated_at=None)
    accepted, dispatched = [], []

    def accept(request, task_id, identity):
        assert task_id == "task" and identity == request_id
        assert request.headers["X-Local-Member-Token"] == "member"
        new = not accepted
        accepted.append(identity)
        return receipt, ("original", identity) if new else None

    def read(request, task_id, identity):
        if request.headers.get("X-Local-Member-Token") != "member" or identity != request_id:
            raise KeyError(identity)
        assert task_id == "task"
        return receipt

    app = FastAPI()
    app.include_router(build_script_generation_router(accept=accept, read=read, execute=dispatched.append))
    client = TestClient(app)
    headers = {"X-Local-Member-Token": "member"}
    url = "/api/creation-tasks/task/scripts/generate"
    for _ in range(2):
        result = client.post(url, json={"request_id": request_id}, headers=headers)
        assert result.status_code == 202
        assert result.json() == receipt
    assert dispatched == [("original", request_id)]
    query = f"/api/creation-tasks/task/scripts/generation-requests/{request_id}"
    assert client.get(query, headers=headers).json() == receipt
    assert client.get(query).status_code == 404
    assert client.get(query.replace(request_id, str(uuid4())), headers=headers).status_code == 404
    for body in ({}, {"request_id": "invalid"}, {"request_id": request_id, "requested_effect": "execute"}):
        assert client.post(url, json=body, headers=headers).status_code == 422
    assert len(accepted) == 2 and len(dispatched) == 1
