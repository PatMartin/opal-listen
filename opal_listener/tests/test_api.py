"""FastAPI endpoint tests: CRUD, validation-before-apply, errors, settings, logs, performance, health."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from opal_listener.service.audio.pcm import write_wav_bytes
from opal_listener.tests.conftest import list_logs, log_counts, patch_settings, silence_frame, tone_frame


def _by_name(client: TestClient, path: str, name: str) -> dict:
    return next(item for item in client.get(path).json() if item["name"] == name)


def _etag(client: TestClient, path: str) -> str:
    return client.get(path).headers["etag"]


def test_kernel_info_routes(client: TestClient) -> None:
    info = client.get("/api/v1/info/").json()
    assert info["name"] == "opal-listen-svc"
    assert info["api_version"] == "v1"
    assert client.get("/api/v1/info/ping").json() == {"status": "ok"}


def test_web_ui_is_served(client: TestClient) -> None:
    response = client.get("/ui/opal-listen.html")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<title>opal listen</title>" in response.text


def test_openapi_does_not_pin_localhost(client: TestClient) -> None:
    servers = client.get("/openapi.json").json().get("servers", [])
    assert not any("localhost" in s.get("url", "") for s in servers)


def test_cors_allows_other_subnet_origins(client: TestClient) -> None:
    response = client.options(
        "/api/v1/health/",
        headers={"Origin": "http://192.168.1.50:3000", "Access-Control-Request-Method": "GET"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"


def test_seeded_configuration(client: TestClient) -> None:
    assert [w["name"] for w in client.get("/api/v1/wakeword/").json()] == ["computer"]
    assert {i["name"] for i in client.get("/api/v1/intent/").json()} >= {"light_turn_on", "get_time"}
    assert _by_name(client, "/api/v1/profile/", "default")["is_active"] is True
    settings = client.get("/api/v1/setting/").json()
    assert settings["cooldown_ms"] == "1500"
    # a fresh database starts with external event publication disabled
    assert settings["event_publication_enabled"] == "false"


def test_wakeword_crud(client: TestClient) -> None:
    created = client.post(
        "/api/v1/wakeword/",
        headers={"If-Match": _etag(client, "/api/v1/wakeword/")},
        json={"name": "Hey Opal", "confidence_threshold": 0.7},
    )
    assert created.status_code == 201
    assert created.headers["location"] == f"/api/v1/wakeword/{created.json()['id']}"
    ww = created.json()
    assert ww["name"] == "hey opal"
    assert client.get(f"/api/v1/wakeword/{ww['id']}").json()["confidence_threshold"] == 0.7
    assert (
        client.post(
            "/api/v1/wakeword/", headers={"If-Match": _etag(client, "/api/v1/wakeword/")}, json={"name": "hey opal"}
        ).status_code
        == 409
    )
    path = f"/api/v1/wakeword/{ww['id']}"
    assert client.patch(path, json={"enabled": False}).status_code == 428  # missing If-Match
    stale = client.patch(path, json={"enabled": False}, headers={"If-Match": '"stale-etag"'})
    assert stale.status_code == 412
    etag = _etag(client, path)
    assert client.patch(path, json={"enabled": False}, headers={"If-Match": etag}).json()["enabled"] is False
    etag = _etag(client, path)
    bad = client.patch(path, json={"confidence_threshold": 3}, headers={"If-Match": etag})
    assert bad.status_code == 422
    etag = _etag(client, path)
    assert client.delete(path, headers={"If-Match": etag}).status_code == 204
    assert client.get(path).status_code == 404
    result = client.post("/api/v1/recognition/test_text", json={"text": "hey opal what time is it"}).json()
    assert result["wakeword"] is None


def test_intent_and_phrase_crud(client: TestClient) -> None:
    created = client.post(
        "/api/v1/intent/",
        headers={"If-Match": _etag(client, "/api/v1/intent/")},
        json={"name": "music_play", "description": "Play music", "phrases": ["play (music | some music)"]},
    )
    assert created.status_code == 201, created.text
    intent = created.json()
    assert [p["phrase"] for p in intent["phrases"]] == ["play (music | some music)"]
    assert client.post("/api/v1/recognition/test_text", json={"text": "play some music"}).json()["intent_name"] == (
        "music_play"
    )
    phrase = client.post(
        f"/api/v1/intent/{intent['id']}/phrase",
        headers={"If-Match": _etag(client, f"/api/v1/intent/{intent['id']}/phrase")},
        json={"phrase": "start the music"},
    ).json()
    assert len(client.get(f"/api/v1/intent/{intent['id']}/phrase").json()) == 2
    duplicate = client.post(
        f"/api/v1/intent/{intent['id']}/phrase",
        headers={"If-Match": _etag(client, f"/api/v1/intent/{intent['id']}/phrase")},
        json={"phrase": "start the music"},
    )
    assert duplicate.status_code == 409
    phrases_etag = _etag(client, f"/api/v1/intent/{intent['id']}/phrase")
    path = f"/api/v1/intent/{intent['id']}/phrase/{phrase['id']}"
    assert client.delete(path).status_code == 428  # missing If-Match
    assert client.delete(path, headers={"If-Match": phrases_etag}).status_code == 204
    intent_etag = _etag(client, f"/api/v1/intent/{intent['id']}")
    renamed = client.patch(
        f"/api/v1/intent/{intent['id']}", json={"name": "music_start"}, headers={"If-Match": intent_etag}
    ).json()
    assert renamed["name"] == "music_start"
    intent_etag = _etag(client, f"/api/v1/intent/{intent['id']}")
    assert client.delete(f"/api/v1/intent/{intent['id']}", headers={"If-Match": intent_etag}).status_code == 204


def test_invalid_phrase_is_rejected_and_nothing_changes(client: TestClient) -> None:
    intent = _by_name(client, "/api/v1/intent/", "light_turn_on")
    before = client.get(f"/api/v1/intent/{intent['id']}/phrase").json()
    response = client.post(
        f"/api/v1/intent/{intent['id']}/phrase",
        headers={"If-Match": _etag(client, f"/api/v1/intent/{intent['id']}/phrase")},
        json={"phrase": "turn on {unknown_slot}"},
    )
    assert response.status_code == 422
    assert "unknown slot" in json.dumps(response.json()["error"])
    assert client.get(f"/api/v1/intent/{intent['id']}/phrase").json() == before


def test_deleting_slot_in_use_is_rejected(client: TestClient) -> None:
    slot = _by_name(client, "/api/v1/slot/", "room_name")
    etag = _etag(client, f"/api/v1/slot/{slot['id']}")
    response = client.delete(f"/api/v1/slot/{slot['id']}", headers={"If-Match": etag})
    assert response.status_code == 422
    assert client.get(f"/api/v1/slot/{slot['id']}").status_code == 200


def test_invalid_intent_name_rejected(client: TestClient) -> None:
    assert (
        client.post(
            "/api/v1/intent/", headers={"If-Match": _etag(client, "/api/v1/intent/")}, json={"name": "Bad Name"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/intent/", headers={"If-Match": _etag(client, "/api/v1/intent/")}, json={"name": "no_match"}
        ).status_code
        == 422
    )


def test_slot_values_and_aliases(client: TestClient) -> None:
    slot = client.post(
        "/api/v1/slot/",
        headers={"If-Match": _etag(client, "/api/v1/slot/")},
        json={"name": "color", "description": "Light colour"},
    ).json()
    assert slot["slot_type"] == "list"
    value = client.post(
        f"/api/v1/slot/{slot['id']}/value",
        headers={"If-Match": _etag(client, f"/api/v1/slot/{slot['id']}/value")},
        json={"value": "red"},
    ).json()
    alias = client.post(
        f"/api/v1/slot/{slot['id']}/value/{value['id']}/alias",
        headers={"If-Match": _etag(client, f"/api/v1/slot/{slot['id']}/value/{value['id']}/alias")},
        json={"alias": "crimson"},
    ).json()
    assert alias["slot_value_id"] == value["id"]
    values = client.get(f"/api/v1/slot/{slot['id']}/value").json()
    assert values[0]["aliases"][0]["alias"] == "crimson"
    assert client.get(f"/api/v1/slot/{slot['id']}").json()["values"][0]["aliases"][0]["alias"] == "crimson"
    assert len(client.get(f"/api/v1/slot/{slot['id']}/value/{value['id']}/alias").json()) == 1

    intent = client.post(
        "/api/v1/intent/",
        headers={"If-Match": _etag(client, "/api/v1/intent/")},
        json={"name": "light_color", "phrases": ["make the lights {color}"]},
    )
    assert intent.status_code == 201
    result = client.post("/api/v1/recognition/test_text", json={"text": "make the lights crimson"}).json()
    assert result["slot_values"] == {"color": "red"}

    other = client.post(
        "/api/v1/slot/", headers={"If-Match": _etag(client, "/api/v1/slot/")}, json={"name": "other_slot"}
    ).json()
    other_values_etag = _etag(client, f"/api/v1/slot/{other['id']}/value")
    assert (
        client.delete(
            f"/api/v1/slot/{other['id']}/value/{value['id']}", headers={"If-Match": other_values_etag}
        ).status_code
        == 404
    )
    aliases_etag = _etag(client, f"/api/v1/slot/{slot['id']}/value/{value['id']}/alias")
    path = f"/api/v1/slot/{slot['id']}/value/{value['id']}/alias/{alias['id']}"
    assert client.delete(path, headers={"If-Match": aliases_etag}).status_code == 204
    assert (
        client.post(
            "/api/v1/slot/",
            headers={"If-Match": _etag(client, "/api/v1/slot/")},
            json={"name": "bad", "slot_type": "colour"},
        ).status_code
        == 422
    )


def test_conflicting_alias_rejected(client: TestClient) -> None:
    slot = _by_name(client, "/api/v1/slot/", "room_name")
    bedroom = next(v for v in slot["values"] if v["value"] == "bedroom")
    response = client.post(
        f"/api/v1/slot/{slot['id']}/value/{bedroom['id']}/alias",
        headers={"If-Match": _etag(client, f"/api/v1/slot/{slot['id']}/value/{bedroom['id']}/alias")},
        json={"alias": "kitchen"},
    )
    assert response.status_code == 422
    assert "maps to both" in json.dumps(response.json()["error"])


def test_profiles_activate_and_filters(client: TestClient) -> None:
    car = _by_name(client, "/api/v1/profile/", "car")
    activated = client.post(
        f"/api/v1/profile/{car['id']}/activate", headers={"If-Match": _etag(client, f"/api/v1/profile/{car['id']}")}
    ).json()
    assert activated["is_active"] is True
    active = [p["name"] for p in client.get("/api/v1/profile/").json() if p["is_active"]]
    assert active == ["car"]
    result = client.post("/api/v1/recognition/test_text", json={"text": "turn on the kitchen light"}).json()
    assert result["profile_name"] == "car"
    assert result["matched"] is False  # lights are not enabled in the car profile
    assert client.get("/api/v1/recognition/status").json()["profile_name"] == "car"

    bad_etag = _etag(client, f"/api/v1/profile/{car['id']}")
    bad = client.patch(
        f"/api/v1/profile/{car['id']}", json={"vad_settings": {"silence_end_ms": 1}}, headers={"If-Match": bad_etag}
    )
    assert bad.status_code == 422
    created = client.post(
        "/api/v1/profile/",
        json={"name": "night", "is_active": True, "confidence_thresholds": {"intent": 0.9}},
        headers={"If-Match": _etag(client, "/api/v1/profile/")},
    ).json()
    assert created["is_active"] is True
    assert _by_name(client, "/api/v1/profile/", "car")["is_active"] is False
    created_etag = _etag(client, f"/api/v1/profile/{created['id']}")
    # the active profile cannot be deleted (kernel contract)
    assert client.delete(f"/api/v1/profile/{created['id']}", headers={"If-Match": created_etag}).status_code == 409
    client.post(
        f"/api/v1/profile/{car['id']}/activate", headers={"If-Match": _etag(client, f"/api/v1/profile/{car['id']}")}
    )
    created_etag = _etag(client, f"/api/v1/profile/{created['id']}")
    assert client.delete(f"/api/v1/profile/{created['id']}", headers={"If-Match": created_etag}).status_code == 204


def test_settings_patch_validation_and_atomicity(client: TestClient) -> None:
    etag = _etag(client, "/api/v1/setting/")
    updated = client.patch("/api/v1/setting/", json={"cooldown_ms": "2500"}, headers={"If-Match": etag}).json()
    assert updated["cooldown_ms"] == "2500"
    etag = _etag(client, "/api/v1/setting/")
    assert client.patch("/api/v1/setting/", json={"cooldown_ms": "soon"}, headers={"If-Match": etag}).status_code == 422
    assert client.patch("/api/v1/setting/", json={"stt_mode": "fancy"}, headers={"If-Match": etag}).status_code == 422
    assert client.patch("/api/v1/setting/", json={"not_a_setting": "1"}, headers={"If-Match": etag}).status_code == 422
    updated = client.patch("/api/v1/setting/", json={"auto_capture_failures": "no"}, headers={"If-Match": etag}).json()
    assert updated["auto_capture_failures"] == "false"
    assert client.patch("/api/v1/setting/", json={"cooldown_ms": "1500"}).status_code == 428  # missing If-Match
    stale = client.patch("/api/v1/setting/", json={"cooldown_ms": "1500"}, headers={"If-Match": '"stale-etag"'})
    assert stale.status_code == 412
    assert client.patch("/api/v1/setting/", json={}, headers={"If-Match": etag}).status_code == 422
    etag = _etag(client, "/api/v1/setting/")
    # a patch containing an invalid value is rejected atomically: nothing else is applied either
    bad = client.patch(
        "/api/v1/setting/",
        json={"event_publication_enabled": "maybe", "cooldown_ms": "1"},
        headers={"If-Match": etag},
    )
    assert bad.status_code == 422
    settings = client.get("/api/v1/setting/").json()
    assert settings["event_publication_enabled"] == "false" and settings["cooldown_ms"] == "2500"
    etag = _etag(client, "/api/v1/setting/")
    restored = client.patch("/api/v1/setting/", json={"cooldown_ms": "1500"}, headers={"If-Match": etag}).json()
    assert restored["cooldown_ms"] == "1500"


def test_settings_key_endpoints(client: TestClient) -> None:
    """Per-key GET/PUT/DELETE /api/v1/setting/{key} (kernel opal_setting_*)."""
    read = client.get("/api/v1/setting/cooldown_ms")
    assert read.status_code == 200
    body = read.json()
    assert body["key"] == "cooldown_ms" and body["value"] == 1500  # native JSON, not stored string
    assert body["default"] == 1500 and body["is_default"] is True and body["pending_restart"] is False
    assert body["id"] and body["updated_at"]
    assert client.get("/api/v1/setting/not_a_setting").status_code == 404

    etag = _etag(client, "/api/v1/setting/")
    assert client.put("/api/v1/setting/cooldown_ms", json={"value": 4200}).status_code == 428
    assert (
        client.put("/api/v1/setting/cooldown_ms", json={"value": 4200}, headers={"If-Match": '"stale"'}).status_code
        == 412
    )
    updated = client.put("/api/v1/setting/cooldown_ms", json={"value": 4200}, headers={"If-Match": etag})
    assert updated.status_code == 200
    body = updated.json()
    assert body["value"] == 4200 and body["is_default"] is False
    assert client.get("/api/v1/setting/").json()["cooldown_ms"] == "4200"

    etag = _etag(client, "/api/v1/setting/")
    assert client.put("/api/v1/setting/not_a_setting", json={"value": 1}, headers={"If-Match": etag}).status_code == 404
    assert (
        client.put("/api/v1/setting/cooldown_ms", json={"value": "soon"}, headers={"If-Match": etag}).status_code == 422
    )
    assert (  # SettingUpdate is a strict {value} body
        client.put("/api/v1/setting/cooldown_ms", json={"value": 1, "extra": 2}, headers={"If-Match": etag}).status_code
        == 422
    )

    etag = _etag(client, "/api/v1/setting/")
    assert client.delete("/api/v1/setting/not_a_setting", headers={"If-Match": etag}).status_code == 404
    assert client.delete("/api/v1/setting/cooldown_ms").status_code == 428
    assert client.delete("/api/v1/setting/cooldown_ms", headers={"If-Match": etag}).status_code == 204
    body = client.get("/api/v1/setting/cooldown_ms").json()
    assert body["value"] == 1500 and body["is_default"] is True


def test_model_path_change_is_validated(client: TestClient) -> None:
    etag = _etag(client, "/api/v1/setting/")
    response = client.patch("/api/v1/setting/", json={"vosk_model_path": "/does/not/exist"}, headers={"If-Match": etag})
    assert response.status_code == 422
    assert "model directory not found" in json.dumps(response.json()["error"])
    profile = _by_name(client, "/api/v1/profile/", "default")
    profile_etag = _etag(client, f"/api/v1/profile/{profile['id']}")
    bad = client.patch(
        f"/api/v1/profile/{profile['id']}",
        json={"model_paths": {"vosk_model": "/nope"}},
        headers={"If-Match": profile_etag},
    )
    assert bad.status_code == 422


def test_test_text_example_output(client: TestClient) -> None:
    result = client.post("/api/v1/recognition/test_text", json={"text": "computer, turn on the kitchen light"}).json()
    expected = {
        "wakeword": "computer",
        "transcript": "turn on the kitchen light",
        "normalized_transcript": "turn on the kitchen light",
        "intent_name": "light_turn_on",
        "slot_values": {"room_name": "kitchen"},
        "matched": True,
    }
    assert {k: result[k] for k in expected} == expected
    assert 0 <= result["intent_confidence"] <= 1
    assert isinstance(result["total_latency_ms"], int)


def test_diagnostic_text_results_are_not_published_to_the_event_bus(client: TestClient) -> None:
    publisher = client.app.state.listener.event_publisher  # type: ignore[attr-defined]
    # even with publication enabled, diagnostic text results are never actionable
    assert patch_settings(client, event_publication_enabled="true").status_code == 200

    for save_log in (True, False):
        before = log_counts(client)["total"]
        matched = client.post(
            "/api/v1/recognition/test_text",
            json={"text": "turn on the kitchen light"},
            params={"save_log": save_log},
        ).json()
        assert matched["matched"] is True
        assert log_counts(client)["total"] == before + int(save_log)
        no_match = client.post(
            "/api/v1/recognition/test_text", json={"text": "order a pizza"}, params={"save_log": save_log}
        ).json()
        assert no_match["matched"] is False
    assert publisher.events == []


def test_diagnostic_audio_results_are_not_published_to_the_event_bus(client: TestClient, fake_provider: type) -> None:
    publisher = client.app.state.listener.event_publisher  # type: ignore[attr-defined]
    # even with publication enabled, diagnostic audio results are never actionable
    assert patch_settings(client, event_publication_enabled="true").status_code == 200
    fake_provider.script.append("turn on the kitchen light")
    wav = write_wav_bytes(b"".join([silence_frame()] * 5 + [tone_frame()] * 10 + [silence_frame()] * 15))

    before = log_counts(client)["total"]
    matched = client.post("/api/v1/recognition/test_audio", files={"file": ("command.wav", wav, "audio/wav")}).json()
    assert matched["matched"] is True and matched["source"] == "audio"
    assert log_counts(client)["total"] == before + 1
    no_match = client.post(
        "/api/v1/recognition/test_audio",
        files={"file": ("command.wav", wav, "audio/wav")},
        params={"save_log": False},
    ).json()
    assert no_match["matched"] is False
    assert log_counts(client)["total"] == before + 1
    assert publisher.events == []


def test_logs_and_performance(client: TestClient) -> None:
    # v1.7.1 removed the /api/v1/log read surface; recognition logs are asserted at the store layer.
    assert client.get("/api/v1/performance/last").json() is None
    for text in ("turn on the kitchen light", "open the pod bay doors", "what time is it"):
        client.post("/api/v1/recognition/test_text", json={"text": text})
    client.post("/api/v1/recognition/test_text", json={"text": "not saved"}, params={"save_log": False})
    assert log_counts(client) == {"total": 3, "matched": 2, "no_match": 1}

    logs = list_logs(client, limit=2)
    assert len(logs) == 2 and logs[0].normalized_transcript == "what time is it"
    full = list_logs(client, limit=10)
    assert len(full) == 3 and [r.id for r in full[:2]] == [r.id for r in logs]
    assert list_logs(client, intent_name="light_turn_on")[0].source == "text"

    summary = client.get("/api/v1/performance/summary").json()
    assert summary["count"] == 3 and summary["match_rate"] == round(2 / 3, 4)
    assert summary["intent_latency_ms"]["avg"] is not None
    assert len(client.get("/api/v1/performance/history").json()) == 3
    assert client.get("/api/v1/performance/last").json()["matched"] is True


def test_failed_utterance_crud(client: TestClient) -> None:
    created = client.post("/api/v1/failed_utterance/", json={"transcript": "dim the lights"}).json()
    assert created["reviewed"] is False and created["recognition_log_id"] is None
    updated = client.patch(
        f"/api/v1/failed_utterance/{created['id']}", json={"reviewed": True, "expected_intent": "light_turn_off"}
    ).json()
    assert updated["reviewed"] is True
    assert client.get("/api/v1/failed_utterance/", params={"reviewed": True}).json()[0]["id"] == created["id"]
    assert client.get("/api/v1/failed_utterance/", params={"reviewed": False}).json() == []
    bad_log = client.post("/api/v1/failed_utterance/", json={"transcript": "x", "recognition_log_id": 42})
    assert bad_log.status_code == 404
    bad_intent = client.post(
        f"/api/v1/failed_utterance/{created['id']}/promote",
        params={"intent_name": "nope"},
        headers={"If-Match": '"precondition"'},
    )
    assert bad_intent.status_code == 404
    assert client.delete(f"/api/v1/failed_utterance/{created['id']}").status_code == 204


def test_regression_crud(client: TestClient) -> None:
    created = client.post(
        "/api/v1/regression/",
        headers={"If-Match": _etag(client, "/api/v1/regression/")},
        json={"name": "t", "phrase": "what time is it", "expected_intent": "get_time", "enabled": False},
    ).json()
    assert client.post("/api/v1/regression/run").json()["total"] == 0
    assert client.post("/api/v1/regression/run", params={"enabled_only": False}).json()["passed"] == 1
    path = f"/api/v1/regression/{created['id']}"
    patched = client.patch(path, json={"enabled": True}, headers={"If-Match": _etag(client, path)})
    assert patched.json()["enabled"] is True
    assert client.get(path).json()["last_result"] is True
    assert client.delete(path, headers={"If-Match": _etag(client, path)}).status_code == 204


def test_health_report(client: TestClient) -> None:
    report = client.get("/api/v1/health/").json()
    names = {c["name"] for c in report["checks"]}
    assert {"sqlite", "migrations", "intents", "slots", "microphone", "listener"} <= names
    assert report["status"] in ("ok", "warning", "error")
    assert isinstance(client.get("/api/v1/health/audio_devices").json(), list)


def test_pipeline_start_stop_idempotent(client: TestClient, fake_provider: type) -> None:
    from opal_listener.service.audio.capture import AudioCaptureError

    service = client.app.state.listener  # type: ignore[attr-defined]

    class NoMic:
        frame_ms = 100
        description = "none"
        capture_rate = None

        def start(self) -> None:
            raise AudioCaptureError("no microphone")

        def stop(self) -> None:
            pass

    service._source_factory = lambda settings: NoMic()
    assert client.post("/api/v1/recognition/stop").json() == {"status": "not_running"}
    assert client.post("/api/v1/recognition/start").json() == {"status": "started"}
    assert client.post("/api/v1/recognition/start").json() == {"status": "already_running"}
    assert client.post("/api/v1/recognition/stop").json() == {"status": "stopped"}
    status = client.get("/api/v1/recognition/status").json()
    assert status["running"] is False
    assert status["provider"] == "fake"
