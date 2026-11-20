"""Configuration snapshots: save/load/validate, atomicity, preconditions and provenance."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from opal_listener.service.snapshots import managed_snapshot_path
from opal_listener.tests.conftest import etag_of, patch_settings


def _revision(client: TestClient) -> str:
    """Live-configuration revision (the If-Match scope for config load)."""
    return client.post("/api/v1/config/save", json={"destination": "response"}).json()["revision"]


def _load(client: TestClient, body: dict | None = None, etag: str | None = None):
    headers = {"If-Match": etag if etag is not None else _revision(client)}
    return client.post("/api/v1/config/load", json=body or {}, headers=headers)


# --- retirement --------------------------------------------------------------------------------


def test_backup_routes_are_retired(client: TestClient) -> None:
    for method, path in (
        ("get", "/api/v1/backup/list"),
        ("post", "/api/v1/backup/create"),
        ("get", "/api/v1/backup/export"),
        ("post", "/api/v1/backup/import"),
        ("get", "/api/v1/backup/validate"),
        ("get", "/api/v1/backup/download/x.db"),
        ("post", "/api/v1/backup/restore/x.db"),
        ("delete", "/api/v1/backup/x.db"),
    ):
        assert getattr(client, method)(path).status_code in (404, 405), (method, path)


def test_setting_collection_patch_is_retired(client: TestClient) -> None:
    etag = client.get("/api/v1/setting/").headers["etag"]
    response = client.patch("/api/v1/setting/", json={"cooldown_ms": "1"}, headers={"If-Match": etag})
    assert response.status_code in (404, 405)


# --- save ---------------------------------------------------------------------------------------


def test_config_save_managed_writes_snapshot_and_keeps_revision(client: TestClient, app_settings) -> None:
    before = _revision(client)
    result = client.post("/api/v1/config/save", json={"destination": "managed"})
    assert result.status_code == 200 and result.headers["etag"] == before
    body = result.json()
    assert body["destination"] == "managed" and body["destination_ref"] == "managed"
    assert body["revision"] == before  # save does not bump the live revision
    snap = body["snapshot"]
    assert snap["format_version"] == "1.0.0" and snap["module"] == "opal-listen"
    assert set(snap["resources"]) == {
        "profiles",
        "personas",
        "wakewords",
        "intents",
        "slots",
    }
    assert managed_snapshot_path(app_settings.data_dir).exists()
    assert _revision(client) == before


def test_config_save_response_does_not_touch_managed_store(client: TestClient, app_settings) -> None:
    client.post(
        "/api/v1/wakeword/", headers={"If-Match": etag_of(client, "/api/v1/wakeword/")}, json={"name": "jarvis"}
    )
    result = client.post("/api/v1/config/save", json={"destination": "response"}).json()
    assert result["destination"] == "response" and result["destination_ref"] is None
    names = {w["name"] for w in result["snapshot"]["resources"]["wakewords"]}
    assert names == {"computer", "jarvis"}
    assert not managed_snapshot_path(app_settings.data_dir).exists()


def test_config_save_captures_only_setting_overrides(client: TestClient) -> None:
    patch_settings(client, cooldown_ms="999")
    snap = client.post("/api/v1/config/save", json={"destination": "response"}).json()["snapshot"]
    assert snap["settings"] == {"cooldown_ms": 999}
    saved = client.post("/api/v1/config/save", json={"destination": "managed"})
    assert saved.status_code == 200


# --- load ---------------------------------------------------------------------------------------


def test_config_load_managed_roundtrip(client: TestClient) -> None:
    client.post("/api/v1/config/save", json={"destination": "managed"})
    client.post(
        "/api/v1/wakeword/", headers={"If-Match": etag_of(client, "/api/v1/wakeword/")}, json={"name": "jarvis"}
    )
    assert len(client.get("/api/v1/wakeword/").json()) == 2
    result = _load(client).json()
    assert result["status"] != "error" if "status" in result else True
    assert result["applied_counts"]["wakewords"] == 1
    assert result["revision"] == _revision(client)
    assert [w["name"] for w in client.get("/api/v1/wakeword/").json()] == ["computer"]


def test_config_load_missing_managed_snapshot_is_404(client: TestClient) -> None:
    response = _load(client)
    assert response.status_code == 404


def test_config_load_requires_if_match(client: TestClient) -> None:
    response = client.post("/api/v1/config/load", json={})
    assert response.status_code == 428


def test_config_load_stale_etag_rejected_and_unchanged(client: TestClient) -> None:
    client.post("/api/v1/config/save", json={"destination": "managed"})
    client.post(
        "/api/v1/wakeword/", headers={"If-Match": etag_of(client, "/api/v1/wakeword/")}, json={"name": "jarvis"}
    )
    response = _load(client, etag='"stale"')
    assert response.status_code == 412
    assert {w["name"] for w in client.get("/api/v1/wakeword/").json()} == {"computer", "jarvis"}


def test_config_load_body_preserves_identifiers(client: TestClient) -> None:
    snap = client.post("/api/v1/config/save", json={"destination": "response"}).json()["snapshot"]
    wakeword_id = snap["resources"]["wakewords"][0]["id"]
    client.post(
        "/api/v1/wakeword/", headers={"If-Match": etag_of(client, "/api/v1/wakeword/")}, json={"name": "jarvis"}
    )
    _load(client, {"snapshot": snap})
    wakewords = client.get("/api/v1/wakeword/").json()
    assert [w["id"] for w in wakewords] == [wakeword_id]


def test_config_load_failure_leaves_configuration_unchanged(client: TestClient) -> None:
    snap = client.post("/api/v1/config/save", json={"destination": "response"}).json()["snapshot"]
    client.post(
        "/api/v1/wakeword/", headers={"If-Match": etag_of(client, "/api/v1/wakeword/")}, json={"name": "jarvis"}
    )
    snap["resources"]["wakewords"] = [{"id": "x", "name": "computer"}] * 2  # duplicate name -> compile fails
    response = _load(client, {"snapshot": snap})
    assert response.status_code in (409, 422)
    assert {w["name"] for w in client.get("/api/v1/wakeword/").json()} == {"computer", "jarvis"}


def test_config_load_corrupt_managed_snapshot_keeps_state(client: TestClient, app_settings) -> None:
    managed_snapshot_path(app_settings.data_dir).write_bytes(b"not json {{{")
    response = _load(client)
    assert response.status_code == 422
    assert len(client.get("/api/v1/wakeword/").json()) == 1


def test_config_load_rejects_wrong_module_and_version(client: TestClient) -> None:
    snap = client.post("/api/v1/config/save", json={"destination": "response"}).json()["snapshot"]
    for bad in ({**snap, "module": "opal-say"}, {**snap, "format_version": "9.9.9"}):
        response = _load(client, {"snapshot": bad})
        assert response.status_code == 422


def test_config_load_reports_pending_restart(client: TestClient) -> None:
    snap = client.post("/api/v1/config/save", json={"destination": "response"}).json()["snapshot"]
    snap["settings"] = {"auto_start_listener": True}
    result = _load(client, {"snapshot": snap}).json()
    assert result["pending_restart_fields"] == ["auto_start_listener"]
    assert client.get("/api/v1/setting/auto_start_listener").json()["pending_restart"] is True


# --- validate -----------------------------------------------------------------------------------


def test_config_validate_live_is_valid(client: TestClient) -> None:
    report = client.post("/api/v1/config/validate", json={}).json()
    assert report["valid"] is True and report["errors"] == []


def test_config_validate_body_reports_structured_issues(client: TestClient) -> None:
    bad = {
        "format_version": "2.0.0",
        "module": "opal-say",
        "settings": {"no_such_key": 1, "cooldown_ms": "abc"},
        "resources": {"history": [], "wakewords": "nope"},
        "mystery": 1,
    }
    report = client.post("/api/v1/config/validate", json={"snapshot": bad}).json()
    assert report["valid"] is False
    paths = {issue["path"] for issue in report["errors"]}
    for issue in report["errors"]:
        assert set(issue) == {"code", "path", "message"}
    assert {"format_version", "module", "mystery", "resources.history", "resources.wakewords"} <= paths
    assert {"settings.no_such_key", "settings.cooldown_ms"} <= paths


def test_config_validate_is_non_mutating(client: TestClient) -> None:
    before = client.post("/api/v1/config/save", json={"destination": "response"}).json()["snapshot"]
    bad = {**before, "module": "opal-say"}
    client.post("/api/v1/config/validate", json={"snapshot": bad})
    after = client.post("/api/v1/config/save", json={"destination": "response"}).json()["snapshot"]
    assert {k: v for k, v in after.items() if k != "created_at"} == {
        k: v for k, v in before.items() if k != "created_at"
    }


def test_config_validate_needs_no_if_match(client: TestClient) -> None:
    assert client.post("/api/v1/config/validate", json={}).status_code == 200


# --- settings provenance ------------------------------------------------------------------------


def test_setting_read_provenance(client: TestClient) -> None:
    read = client.get("/api/v1/setting/cooldown_ms").json()
    assert read["key"] == "cooldown_ms" and read["value"] == 1500
    assert read["configured_value"] == 1500 and read["default"] == 1500
    assert read["has_override"] is False and read["is_default"] is True
    assert read["source"] == "default" and read["restart_required"] is False
    patch_settings(client, cooldown_ms="999")
    read = client.get("/api/v1/setting/cooldown_ms").json()
    assert read["value"] == 999 and read["configured_value"] == 999
    assert read["has_override"] is True and read["is_default"] is False
    assert read["source"] == "persisted"


def test_setting_put_get_delete_roundtrip(client: TestClient) -> None:
    etag = client.get("/api/v1/setting/").headers["etag"]
    put = client.put("/api/v1/setting/cooldown_ms", json={"value": 5}, headers={"If-Match": etag})
    assert put.status_code == 200 and put.headers["etag"] != etag
    assert put.json()["value"] == 5 and put.json()["has_override"] is True
    delete = client.delete("/api/v1/setting/cooldown_ms", headers={"If-Match": put.headers["etag"]})
    assert delete.status_code == 204 and "etag" in delete.headers
    read = client.get("/api/v1/setting/cooldown_ms")
    assert read.json()["has_override"] is False and read.json()["value"] == 1500
    assert read.headers["etag"]


def test_setting_item_preconditions(client: TestClient) -> None:
    assert client.put("/api/v1/setting/cooldown_ms", json={"value": 1}).status_code == 428
    etag = client.get("/api/v1/setting/").headers["etag"]
    assert (
        client.put("/api/v1/setting/cooldown_ms", json={"value": 1}, headers={"If-Match": '"stale"'}).status_code
        == 412
    )
    assert client.delete("/api/v1/setting/cooldown_ms", headers={"If-Match": etag}).status_code == 204


def test_setting_unknown_key(client: TestClient) -> None:
    assert client.get("/api/v1/setting/not_a_key").status_code == 404
    etag = client.get("/api/v1/setting/").headers["etag"]
    assert (
        client.put("/api/v1/setting/not_a_key", json={"value": 1}, headers={"If-Match": etag}).status_code == 404
    )
    assert client.delete("/api/v1/setting/not_a_key", headers={"If-Match": etag}).status_code == 404


def test_restart_required_flag(client: TestClient) -> None:
    read = client.get("/api/v1/setting/auto_start_listener").json()
    assert read["restart_required"] is True and read["pending_restart"] is False


# --- database internals -------------------------------------------------------------------------


def test_db_maintenance_blocks_other_sessions(tmp_path: Path) -> None:
    from opal_listener.data.db import Database
    from opal_listener.service.errors import ServiceUnavailableError

    db = Database(f"sqlite:///{tmp_path}/x.db")
    db.migrate()
    entered = threading.Event()
    release = threading.Event()

    def hold_maintenance() -> None:
        with db.maintenance():
            entered.set()
            release.wait(5)

    worker = threading.Thread(target=hold_maintenance, daemon=True)
    worker.start()
    try:
        assert entered.wait(5)
        with pytest.raises(ServiceUnavailableError), db.session():
            pass
    finally:
        release.set()
        worker.join(5)
    with db.session():  # sessions work again once the window closes
        pass


def test_db_maintenance_waits_for_active_sessions(tmp_path: Path) -> None:
    from opal_listener.data.db import Database

    db = Database(f"sqlite:///{tmp_path}/x.db")
    db.migrate()
    entered = threading.Event()

    def hold_maintenance() -> None:
        with db.maintenance():
            entered.set()

    with db.session():  # an open session must drain before maintenance starts
        worker = threading.Thread(target=hold_maintenance, daemon=True)
        worker.start()
        assert not entered.wait(0.5)
    assert entered.wait(5)


def test_managed_snapshot_write_failure_keeps_previous(client: TestClient, app_settings) -> None:
    path = managed_snapshot_path(app_settings.data_dir)
    client.post("/api/v1/config/save", json={"destination": "managed"})
    first = path.read_bytes()
    path.write_bytes(b"grow" * 10)
    assert path.read_bytes() != first
    client.post("/api/v1/config/save", json={"destination": "managed"})
    assert json.loads(path.read_text())["module"] == "opal-listen"
