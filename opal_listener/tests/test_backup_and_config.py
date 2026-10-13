"""Backup, restore, rollback, export/import and path-traversal protection."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from opal_listener.app_settings import PACKAGE_DIR
from opal_listener.service.config_store import load_config_dir


def test_backup_create_list_download_delete(client: TestClient) -> None:
    created = client.post("/api/v1/backup/create").json()
    filename = created["filename"]
    assert filename.endswith(".db")
    assert filename in [b["filename"] for b in client.get("/api/v1/backup/list").json()]
    download = client.get(f"/api/v1/backup/download/{filename}")
    assert download.status_code == 200 and download.content[:15] == b"SQLite format 3"
    assert client.delete(f"/api/v1/backup/{filename}").status_code == 204
    assert client.get(f"/api/v1/backup/download/{filename}").status_code == 404


def test_restore_returns_previous_state(client: TestClient) -> None:
    filename = client.post("/api/v1/backup/create").json()["filename"]
    client.post("/api/v1/wakewords/", json={"name": "jarvis"})
    assert len(client.get("/api/v1/wakewords/").json()) == 2
    result = client.post(f"/api/v1/backup/restore/{filename}").json()
    assert result["status"] == "restored"
    assert [w["name"] for w in client.get("/api/v1/wakewords/").json()] == ["computer"]
    # the pre-restore safety backup holds the newer state
    assert result["rollback_backup"] in [b["filename"] for b in client.get("/api/v1/backup/list").json()]
    assert client.post("/api/v1/recognition/test_text", json={"text": "what time is it"}).json()["matched"]


def test_restore_rejects_corrupt_backup_and_keeps_data(client: TestClient, app_settings) -> None:
    (app_settings.backup_dir / "corrupt.db").write_bytes(b"this is not sqlite" * 100)
    response = client.post("/api/v1/backup/restore/corrupt.db")
    assert response.status_code == 422
    assert len(client.get("/api/v1/wakewords/").json()) == 1


def test_restore_with_invalid_config_rolls_back(client: TestClient, app_settings) -> None:
    import sqlite3

    filename = client.post("/api/v1/backup/create").json()["filename"]
    conn = sqlite3.connect(app_settings.backup_dir / filename)
    conn.execute("INSERT INTO intent_phrases (intent_id, phrase, created_at) VALUES (1, 'x {ghost}', '2024-01-01')")
    conn.commit()
    conn.close()
    client.post("/api/v1/wakewords/", json={"name": "jarvis"})
    response = client.post(f"/api/v1/backup/restore/{filename}")
    assert response.status_code == 422
    assert "invalid configuration" in response.json()["detail"][0]["msg"]
    assert {w["name"] for w in client.get("/api/v1/wakewords/").json()} == {"computer", "jarvis"}


def test_backup_filename_traversal_is_rejected(client: TestClient) -> None:
    for name in ("..%2F..%2Fetc%2Fpasswd", "..%2Fopal_listener.db", "x.txt", ".hidden.db"):
        assert client.get(f"/api/v1/backup/download/{name}").status_code in (404, 422), name
        assert client.post(f"/api/v1/backup/restore/{name}").status_code in (404, 422), name
        assert client.delete(f"/api/v1/backup/{name}").status_code in (404, 405, 422), name


def test_export_import_roundtrip(client: TestClient) -> None:
    exported = client.get("/api/v1/backup/export").json()
    assert {"wakewords", "stt", "intents", "slots", "profiles", "settings"} <= set(exported)
    client.post("/api/v1/wakewords/", json={"name": "jarvis"})
    result = client.post("/api/v1/backup/import", json=exported).json()
    assert result["status"] == "imported" and result["backup_filename"]
    assert [w["name"] for w in client.get("/api/v1/wakewords/").json()] == ["computer"]
    assert client.get("/api/v1/backup/export").json() == exported


def test_import_from_server_config_dir(client: TestClient) -> None:
    client.post("/api/v1/intents/", json={"name": "extra_intent", "phrases": ["hello there"]})
    result = client.post("/api/v1/backup/import").json()
    assert result["counts"]["intents"] == len(load_config_dir(PACKAGE_DIR / "config").intents)
    assert "extra_intent" not in {i["name"] for i in client.get("/api/v1/intents/").json()}


def test_invalid_import_changes_nothing(client: TestClient) -> None:
    before = client.get("/api/v1/backup/export").json()
    backups_before = len(client.get("/api/v1/backup/list").json())
    bad = {**before, "intents": [{"name": "broken", "phrases": ["turn on {ghost}"]}]}
    response = client.post("/api/v1/backup/import", json=bad)
    assert response.status_code == 422
    assert client.get("/api/v1/backup/export").json() == before
    assert len(client.get("/api/v1/backup/list").json()) == backups_before


def test_validate_endpoints(client: TestClient) -> None:
    report = client.get("/api/v1/backup/validate").json()
    assert report["valid"] is True and report["sources"] == {"config_dir": True, "database": True}
    bad = client.post("/api/v1/backup/validate", json={"intents": [{"name": "x", "phrases": ["{nope}"]}]}).json()
    assert bad["valid"] is False and "unknown slot" in bad["errors"][0]


def test_validate_reports_broken_config_dir(tmp_path: Path) -> None:
    from opal_listener.data.db import Database
    from opal_listener.main import create_app
    from opal_listener.service.listener_service import ListenerService
    from opal_listener.tests.conftest import make_settings

    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    (config_dir / "intents.yaml").write_text("intents: [{name: a, phrases: ['{ghost}']}]")
    settings = make_settings(tmp_path, config_dir=config_dir)
    settings.ensure_dirs()
    service = ListenerService(Database(settings.database_url), settings)
    with TestClient(create_app(settings, service)) as client:
        # an invalid seed is refused: the database starts empty but valid
        assert client.get("/api/v1/intents/").json() == []
        report = client.get("/api/v1/backup/validate").json()
        assert report["valid"] is False
        assert report["sources"] == {"config_dir": False, "database": True}
        assert client.post("/api/v1/backup/import").status_code == 422
