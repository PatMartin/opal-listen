"""FastAPI endpoint tests: CRUD, validation-before-apply, errors, settings, logs, performance, health."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _by_name(client: TestClient, path: str, name: str) -> dict:
    return next(item for item in client.get(path).json() if item["name"] == name)


def _etag(client: TestClient, path: str) -> str:
    return client.get(path).headers["etag"]


def test_root_and_healthz(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/").json()["name"] == "opal_listener"


def test_web_ui_is_served(client: TestClient) -> None:
    response = client.get(client.get("/").json()["ui"])
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
    assert [w["name"] for w in client.get("/api/v1/wakewords/").json()] == ["computer"]
    assert {i["name"] for i in client.get("/api/v1/intents/").json()} >= {"light_turn_on", "get_time"}
    assert _by_name(client, "/api/v1/profiles/", "default")["is_active"] is True
    assert client.get("/api/v1/settings/").json()["cooldown_ms"] == "1500"


def test_wakeword_crud(client: TestClient) -> None:
    created = client.post("/api/v1/wakewords/", json={"name": "Hey Opal", "confidence_threshold": 0.7})
    assert created.status_code == 201
    assert created.headers["location"] == f"/api/v1/wakewords/{created.json()['id']}"
    ww = created.json()
    assert ww["name"] == "hey opal"
    assert client.get(f"/api/v1/wakewords/{ww['id']}").json()["confidence_threshold"] == 0.7
    assert client.post("/api/v1/wakewords/", json={"name": "hey opal"}).status_code == 409
    path = f"/api/v1/wakewords/{ww['id']}"
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
        "/api/v1/intents/",
        json={"name": "music_play", "description": "Play music", "phrases": ["play (music | some music)"]},
    )
    assert created.status_code == 201, created.text
    intent = created.json()
    assert [p["phrase"] for p in intent["phrases"]] == ["play (music | some music)"]
    assert client.post("/api/v1/recognition/test_text", json={"text": "play some music"}).json()["intent_name"] == (
        "music_play"
    )
    phrase = client.post(f"/api/v1/intents/{intent['id']}/phrases", json={"phrase": "start the music"}).json()
    assert len(client.get(f"/api/v1/intents/{intent['id']}/phrases").json()) == 2
    duplicate = client.post(f"/api/v1/intents/{intent['id']}/phrases", json={"phrase": "start the music"})
    assert duplicate.status_code == 409
    phrases_etag = _etag(client, f"/api/v1/intents/{intent['id']}/phrases")
    path = f"/api/v1/intents/{intent['id']}/phrases/{phrase['id']}"
    assert client.delete(path).status_code == 428  # missing If-Match
    assert client.delete(path, headers={"If-Match": phrases_etag}).status_code == 204
    intent_etag = _etag(client, f"/api/v1/intents/{intent['id']}")
    renamed = client.patch(
        f"/api/v1/intents/{intent['id']}", json={"name": "music_start"}, headers={"If-Match": intent_etag}
    ).json()
    assert renamed["name"] == "music_start"
    intent_etag = _etag(client, f"/api/v1/intents/{intent['id']}")
    assert client.delete(f"/api/v1/intents/{intent['id']}", headers={"If-Match": intent_etag}).status_code == 204


def test_invalid_phrase_is_rejected_and_nothing_changes(client: TestClient) -> None:
    intent = _by_name(client, "/api/v1/intents/", "light_turn_on")
    before = client.get(f"/api/v1/intents/{intent['id']}/phrases").json()
    response = client.post(f"/api/v1/intents/{intent['id']}/phrases", json={"phrase": "turn on {unknown_slot}"})
    assert response.status_code == 422
    assert "unknown slot" in response.json()["detail"][0]["msg"]
    assert client.get(f"/api/v1/intents/{intent['id']}/phrases").json() == before


def test_deleting_slot_in_use_is_rejected(client: TestClient) -> None:
    slot = _by_name(client, "/api/v1/slots/", "room_name")
    etag = _etag(client, f"/api/v1/slots/{slot['id']}")
    response = client.delete(f"/api/v1/slots/{slot['id']}", headers={"If-Match": etag})
    assert response.status_code == 422
    assert client.get(f"/api/v1/slots/{slot['id']}").status_code == 200


def test_invalid_intent_name_rejected(client: TestClient) -> None:
    assert client.post("/api/v1/intents/", json={"name": "Bad Name"}).status_code == 422
    assert client.post("/api/v1/intents/", json={"name": "no_match"}).status_code == 422


def test_slot_values_and_aliases(client: TestClient) -> None:
    slot = client.post("/api/v1/slots/", json={"name": "color", "description": "Light colour"}).json()
    assert slot["slot_type"] == "list"
    value = client.post(f"/api/v1/slots/{slot['id']}/values", json={"value": "red"}).json()
    alias = client.post(f"/api/v1/slots/{slot['id']}/values/{value['id']}/aliases", json={"alias": "crimson"}).json()
    assert alias["slot_value_id"] == value["id"]
    values = client.get(f"/api/v1/slots/{slot['id']}/values").json()
    assert values[0]["aliases"][0]["alias"] == "crimson"
    assert client.get(f"/api/v1/slots/{slot['id']}").json()["values"][0]["aliases"][0]["alias"] == "crimson"
    assert len(client.get(f"/api/v1/slots/{slot['id']}/values/{value['id']}/aliases").json()) == 1

    intent = client.post("/api/v1/intents/", json={"name": "light_color", "phrases": ["make the lights {color}"]})
    assert intent.status_code == 201
    result = client.post("/api/v1/recognition/test_text", json={"text": "make the lights crimson"}).json()
    assert result["slot_values"] == {"color": "red"}

    other = client.post("/api/v1/slots/", json={"name": "other_slot"}).json()
    other_values_etag = _etag(client, f"/api/v1/slots/{other['id']}/values")
    assert (
        client.delete(
            f"/api/v1/slots/{other['id']}/values/{value['id']}", headers={"If-Match": other_values_etag}
        ).status_code
        == 404
    )
    aliases_etag = _etag(client, f"/api/v1/slots/{slot['id']}/values/{value['id']}/aliases")
    path = f"/api/v1/slots/{slot['id']}/values/{value['id']}/aliases/{alias['id']}"
    assert client.delete(path, headers={"If-Match": aliases_etag}).status_code == 204
    assert client.post("/api/v1/slots/", json={"name": "bad", "slot_type": "colour"}).status_code == 422


def test_conflicting_alias_rejected(client: TestClient) -> None:
    slot = _by_name(client, "/api/v1/slots/", "room_name")
    bedroom = next(v for v in slot["values"] if v["value"] == "bedroom")
    response = client.post(f"/api/v1/slots/{slot['id']}/values/{bedroom['id']}/aliases", json={"alias": "kitchen"})
    assert response.status_code == 422
    assert "maps to both" in response.json()["detail"][0]["msg"]


def test_profiles_activate_and_filters(client: TestClient) -> None:
    car = _by_name(client, "/api/v1/profiles/", "car")
    activated = client.post(f"/api/v1/profiles/{car['id']}/activate").json()
    assert activated["is_active"] is True
    active = [p["name"] for p in client.get("/api/v1/profiles/").json() if p["is_active"]]
    assert active == ["car"]
    result = client.post("/api/v1/recognition/test_text", json={"text": "turn on the kitchen light"}).json()
    assert result["profile_name"] == "car"
    assert result["matched"] is False  # lights are not enabled in the car profile
    assert client.get("/api/v1/recognition/status").json()["profile_name"] == "car"

    bad_etag = _etag(client, f"/api/v1/profiles/{car['id']}")
    bad = client.patch(
        f"/api/v1/profiles/{car['id']}", json={"vad_settings": {"silence_end_ms": 1}}, headers={"If-Match": bad_etag}
    )
    assert bad.status_code == 422
    created = client.post(
        "/api/v1/profiles/",
        json={"name": "night", "is_active": True, "confidence_thresholds": {"intent": 0.9}},
    ).json()
    assert created["is_active"] is True
    assert _by_name(client, "/api/v1/profiles/", "car")["is_active"] is False
    created_etag = _etag(client, f"/api/v1/profiles/{created['id']}")
    assert client.delete(f"/api/v1/profiles/{created['id']}", headers={"If-Match": created_etag}).status_code == 204


def test_settings_patch_validation_and_atomicity(client: TestClient) -> None:
    etag = _etag(client, "/api/v1/settings/")
    updated = client.patch("/api/v1/settings/", json={"cooldown_ms": "2500"}, headers={"If-Match": etag}).json()
    assert updated["cooldown_ms"] == "2500"
    etag = _etag(client, "/api/v1/settings/")
    assert (
        client.patch("/api/v1/settings/", json={"cooldown_ms": "soon"}, headers={"If-Match": etag}).status_code == 422
    )
    assert client.patch("/api/v1/settings/", json={"stt_mode": "fancy"}, headers={"If-Match": etag}).status_code == 422
    assert client.patch("/api/v1/settings/", json={"not_a_setting": "1"}, headers={"If-Match": etag}).status_code == 422
    updated = client.patch("/api/v1/settings/", json={"auto_capture_failures": "no"}, headers={"If-Match": etag}).json()
    assert updated["auto_capture_failures"] == "false"
    assert client.patch("/api/v1/settings/", json={"cooldown_ms": "1500"}).status_code == 428  # missing If-Match
    stale = client.patch("/api/v1/settings/", json={"cooldown_ms": "1500"}, headers={"If-Match": '"stale-etag"'})
    assert stale.status_code == 412
    assert client.patch("/api/v1/settings/", json={}, headers={"If-Match": etag}).status_code == 422
    etag = _etag(client, "/api/v1/settings/")
    restored = client.patch("/api/v1/settings/", json={"cooldown_ms": "1500"}, headers={"If-Match": etag}).json()
    assert restored["cooldown_ms"] == "1500"


def test_model_path_change_is_validated(client: TestClient) -> None:
    etag = _etag(client, "/api/v1/settings/")
    response = client.patch(
        "/api/v1/settings/", json={"vosk_model_path": "/does/not/exist"}, headers={"If-Match": etag}
    )
    assert response.status_code == 422
    assert "model directory not found" in response.json()["detail"][0]["msg"]
    profile = _by_name(client, "/api/v1/profiles/", "default")
    profile_etag = _etag(client, f"/api/v1/profiles/{profile['id']}")
    bad = client.patch(
        f"/api/v1/profiles/{profile['id']}",
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


def test_intent_match_is_published_to_the_event_bus(client: TestClient) -> None:
    from opal_listener.service.listener_service import INTENT_MATCH_TOPIC

    publisher = client.app.state.listener.event_publisher  # type: ignore[attr-defined]

    no_match = client.post("/api/v1/recognition/test_text", json={"text": "order a pizza"}).json()
    assert no_match["matched"] is False
    assert publisher.events == []

    result = client.post("/api/v1/recognition/test_text", json={"text": "turn on the kitchen light"}).json()
    assert result["matched"] is True
    assert len(publisher.events) == 1
    topic, payload = publisher.events[0]
    assert topic == INTENT_MATCH_TOPIC
    assert payload["intent_name"] == "light_turn_on"
    assert payload["slot_values"] == {"room_name": "kitchen"}
    assert payload["matched"] is True


def test_logs_and_performance(client: TestClient) -> None:
    assert client.get("/api/v1/performance/last").json() is None
    for text in ("turn on the kitchen light", "open the pod bay doors", "what time is it"):
        client.post("/api/v1/recognition/test_text", json={"text": text})
    client.post("/api/v1/recognition/test_text", json={"text": "not saved"}, params={"save_log": False})
    assert client.get("/api/v1/logs/count").json() == {"total": 3, "matched": 2, "no_match": 1}

    page = client.get("/api/v1/logs/", params={"limit": 2}).json()
    logs = page["items"]
    assert len(logs) == 2 and logs[0]["normalized_transcript"] == "what time is it"
    assert page["has_more"] is True and page["next_cursor"]
    next_page = client.get("/api/v1/logs/", params={"limit": 2, "cursor": page["next_cursor"]}).json()
    assert len(next_page["items"]) == 1 and next_page["has_more"] is False and next_page["next_cursor"] is None
    assert client.get("/api/v1/logs/", params={"intent_name": "light_turn_on"}).json()["items"][0]["source"] == "text"
    assert client.get(f"/api/v1/logs/{logs[0]['id']}").json()["id"] == logs[0]["id"]
    assert client.get("/api/v1/logs/999").status_code == 404
    assert client.get("/api/v1/logs/", params={"limit": 501}).status_code == 422
    assert client.get("/api/v1/logs/", params={"cursor": "not-a-real-cursor"}).status_code == 422
    # a cursor minted under a different filter set must be rejected
    mismatched = client.get("/api/v1/logs/", params={"intent_name": "light_turn_on", "cursor": page["next_cursor"]})
    assert mismatched.status_code == 422

    summary = client.get("/api/v1/performance/summary").json()
    assert summary["count"] == 3 and summary["match_rate"] == round(2 / 3, 4)
    assert summary["intent_latency_ms"]["avg"] is not None
    assert len(client.get("/api/v1/performance/history").json()["items"]) == 3
    assert client.get("/api/v1/performance/last").json()["matched"] is True


def test_failed_utterance_crud(client: TestClient) -> None:
    created = client.post("/api/v1/failed_utterances/", json={"transcript": "dim the lights"}).json()
    assert created["reviewed"] is False and created["recognition_log_id"] is None
    updated = client.patch(
        f"/api/v1/failed_utterances/{created['id']}", json={"reviewed": True, "expected_intent": "light_turn_off"}
    ).json()
    assert updated["reviewed"] is True
    assert client.get("/api/v1/failed_utterances/", params={"reviewed": True}).json()[0]["id"] == created["id"]
    assert client.get("/api/v1/failed_utterances/", params={"reviewed": False}).json() == []
    bad_log = client.post("/api/v1/failed_utterances/", json={"transcript": "x", "recognition_log_id": 42})
    assert bad_log.status_code == 404
    bad_intent = client.post(f"/api/v1/failed_utterances/{created['id']}/promote", params={"intent_name": "nope"})
    assert bad_intent.status_code == 404
    assert client.delete(f"/api/v1/failed_utterances/{created['id']}").status_code == 204


def test_regression_crud(client: TestClient) -> None:
    created = client.post(
        "/api/v1/regression/",
        json={"name": "t", "phrase": "what time is it", "expected_intent": "get_time", "enabled": False},
    ).json()
    assert client.post("/api/v1/regression/run").json()["total"] == 0
    assert client.post("/api/v1/regression/run", params={"enabled_only": False}).json()["passed"] == 1
    assert client.patch(f"/api/v1/regression/{created['id']}", json={"enabled": True}).json()["enabled"] is True
    assert client.get(f"/api/v1/regression/{created['id']}").json()["last_result"] is True
    assert client.delete(f"/api/v1/regression/{created['id']}").status_code == 204


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
