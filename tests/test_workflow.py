from copy import deepcopy

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import httpx
import pytest

from app.keitaro import DemoKeitaro, Keitaro, KeitaroError
from app.main import create_app


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/test.db"
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    remote = DemoKeitaro(tmp_path / "remote.json")
    app = create_app(db_url=url, client=remote, demo=True)
    with TestClient(app) as client:
        yield client, remote


def fetch(client):
    response = client.post("/api/campaigns/1001/fetch", json={})
    assert response.status_code == 200, response.text
    return next(s for s in response.json() if s["id"] == 10012)


def edit(client, stream, action, offer_id, **kwargs):
    return client.post(f'/api/streams/{stream["id"]}/draft', json={"version": stream["version"], "action": action, "offer_id": offer_id, **kwargs})


def push(client, stream):
    return client.post(f'/api/streams/{stream["id"]}/push', json={"version": stream["version"]})


def shares(stream):
    return {o["offer_id"]: o["share"] for o in stream["offers"] if not o["archived"]}


def test_draft_pin_archive_restore_cancel_and_push(workspace):
    client, remote = workspace
    original = deepcopy(remote.data["streams"][1])
    stream = fetch(client)
    stream = edit(client, stream, "pin", 101, share=25).json()
    stream = edit(client, stream, "add", 103).json()
    assert shares(stream) == {101: 25, 102: 38, 103: 37}
    assert remote.data["streams"][1] == original  # All changes local until push.
    assert client.post("/api/campaigns/1001/fetch", json={}).status_code == 409
    stream = push(client, stream).json()
    assert not stream["dirty"]
    assert remote.data["streams"][1]["filters"] == original["filters"]
    stream = edit(client, stream, "remove", 102).json()
    assert shares(stream) == {101: 25, 103: 75}
    stream = push(client, stream).json()
    assert len(remote.data["streams"][1]["offers"]) == 2
    assert next(o for o in stream["offers"] if o["offer_id"] == 102)["archived"]
    stream = fetch(client)
    assert next(o for o in stream["offers"] if o["offer_id"] == 102)["archived"]
    restored = edit(client, stream, "restore", 102).json()
    assert shares(restored) == {101: 25, 103: 38, 102: 37}
    cancelled = client.post("/api/streams/10012/cancel", json={"version": restored["version"]}).json()
    assert not cancelled["dirty"]
    assert shares(cancelled) == {101: 25, 103: 75}
    added = edit(client, cancelled, "add", 104).json()
    cancelled = client.post("/api/streams/10012/cancel", json={"version": added["version"]}).json()
    assert 104 not in [o["offer_id"] for o in cancelled["offers"]]


def test_external_delete_is_archived_and_stale_push_is_rejected(workspace):
    client, remote = workspace
    stream = fetch(client)
    stream = edit(client, stream, "add", 103).json()
    remote.data["streams"][1]["offers"] = [{"offer_id": 101, "share": 100, "state": "active"}]
    assert push(client, stream).status_code == 409
    client.post("/api/streams/10012/cancel", json={"version": stream["version"]})
    stream = fetch(client)
    assert next(o for o in stream["offers"] if o["offer_id"] == 102)["archived"]
    stream = edit(client, stream, "restore", 102).json()
    assert shares(push(client, stream).json()) == {101: 50, 102: 50}


def test_stale_tab_invalid_weights_duplicates_and_empty_push(workspace):
    client, _ = workspace
    stream = fetch(client)
    new = edit(client, stream, "pin", 101, share=80).json()
    assert edit(client, stream, "add", 103).status_code == 409
    assert edit(client, new, "pin", 102, share=30).status_code == 422
    assert edit(client, new, "add", 101).status_code == 422
    assert edit(client, new, "pin", 102, share=-1).status_code == 422
    assert edit(client, new, "pin", 102, share=20.5).status_code == 422
    new = edit(client, new, "remove", 101).json()
    new = edit(client, new, "remove", 102).json()
    assert push(client, new).status_code == 422


def test_push_preserves_nonoffer_fields_including_external_updates(workspace):
    client, remote = workspace
    stream = fetch(client)
    draft = edit(client, stream, "add", 103).json()
    current = remote.data["streams"][1]
    current.update({"filters": [{"name": "country", "mode": "accept", "payload": ["DE"]}],
                    "comments": "changed externally", "landings": [{"landing_id": 9, "share": 100}],
                    "collect_clicks": True})
    before = {k: deepcopy(v) for k, v in current.items() if k != "offers"}
    assert push(client, draft).status_code == 200
    assert {k: v for k, v in remote.data["streams"][1].items() if k != "offers"} == before


def test_drafts_survive_application_restart(workspace, tmp_path):
    client, remote = workspace
    stream = edit(client, fetch(client), "add", 103).json()
    other = create_app(db_url=f"sqlite:///{tmp_path}/test.db", client=remote, demo=True)
    with TestClient(other) as second:
        rows = second.get("/api/campaigns/1001/streams").json()
        assert next(s for s in rows if s["id"] == 10012) == stream


def test_create_campaign_routes_and_foreign_key_validation(workspace):
    client, remote = workspace
    body = {"name": "test_goncharenko_new", "country": "pt", "offer_id": 101, "domain_id": 1, "group_id": 1, "traffic_source_id": 1}
    before = len(remote.data["campaigns"])
    assert client.post("/api/campaigns", json={**body, "country": "XX"}).status_code == 422
    assert client.post("/api/campaigns", json={**body, "name": "other"}).status_code == 422
    assert client.post("/api/campaigns", json={**body, "offer_id": 999}).status_code == 422
    assert len(remote.data["campaigns"]) == before
    response = client.post("/api/campaigns", json=body)
    assert response.status_code == 201, response.text
    campaign = response.json()["campaign"]
    assert campaign["state"] == "active"
    assert campaign["domain_id"] == campaign["traffic_source_id"] == 1
    streams = remote.request("GET", f'campaigns/{campaign["id"]}/streams')
    assert len(streams) == 2
    assert streams[0]["filters"] == [{"name": "country", "mode": "accept", "payload": ["PT"]}]
    assert streams[0]["action_payload"] == "https://www.google.com/"
    assert streams[1]["type"] == "default" and streams[1]["offers"][0]["offer_id"] == 101


def test_partial_creation_reports_remote_id_and_leaves_disabled(workspace, monkeypatch):
    client, remote = workspace
    request = remote.request
    def fail(method, path, data=None, params=None):
        if method == "POST" and path == "streams":
            raise KeitaroError("Connection timed out")
        return request(method, path, data, params)
    monkeypatch.setattr(remote, "request", fail)
    response = client.post("/api/campaigns", json={"name": "test_goncharenko_partial", "country": "PT", "offer_id": 101, "domain_id": 1, "group_id": 1, "traffic_source_id": 1})
    assert response.status_code == 502
    campaign = remote.data["campaigns"][-1]
    assert campaign["state"] == "disabled"
    assert str(campaign["id"]) in response.json()["detail"]


def test_readonly_stream_and_cross_origin_writes(workspace):
    client, _ = workspace
    rows = client.post("/api/campaigns/1001/fetch", json={}).json()
    readonly = next(s for s in rows if s["id"] == 10011)
    assert not readonly["editable"]
    assert edit(client, readonly, "add", 103).status_code == 422
    assert client.post("/api/campaigns/1001/fetch", json={}, headers={"Origin": "https://other.example"}).status_code == 403
    assert client.get("/").status_code == 200


def test_real_client_paths_auth_partial_put_and_no_post_retry():
    requests = []
    def handler(request):
        requests.append(request)
        assert request.headers["Api-Key"] == "test-secret"
        assert request.url.path.startswith("/admin_api/v1/")
        if request.method == "POST":
            raise httpx.ReadTimeout("timeout", request=request)
        if request.url.path.endswith("/streams/42"):
            return httpx.Response(200, json={"offers": []})
        return httpx.Response(403, text="secret-tracker-response")
    remote = Keitaro("https://tracker.example", "test-secret", transport=httpx.MockTransport(handler))
    assert remote.request("PUT", "streams/42", {"offers": []}) == {"offers": []}
    assert requests[-1].content == b'{"offers":[]}'
    with pytest.raises(KeitaroError, match="Недостаточно прав"):
        remote.request("GET", "offers")
    with pytest.raises(KeitaroError) as error:
        remote.request("POST", "campaigns", {})
    assert "test-secret" not in error.value.message
    assert sum(r.method == "POST" for r in requests) == 1
    remote.close()
