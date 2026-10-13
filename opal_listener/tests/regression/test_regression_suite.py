"""Golden text set + saved-failure regression loop."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

GOLDEN = yaml.safe_load((Path(__file__).resolve().parent / "golden_phrases.yaml").read_text())["golden"]


@pytest.mark.parametrize("case", GOLDEN, ids=lambda c: c["text"])
def test_golden_phrase(client: TestClient, case: dict[str, Any]) -> None:
    result = client.post(
        "/api/v1/recognition/test_text", json={"text": case["text"]}, params={"save_log": False}
    ).json()
    assert result["matched"] is True, result
    assert result["intent_name"] == case["intent_name"], result
    assert result["slot_values"] == case["slot_values"], result
    assert result["intent_confidence"] >= 0.75


def test_golden_set_passes_via_regression_api(client: TestClient) -> None:
    for i, case in enumerate(GOLDEN):
        body = {
            "name": f"golden_{i}",
            "phrase": case["text"],
            "expected_intent": case["intent_name"],
            "expected_slots": case["slot_values"],
        }
        assert client.post("/api/v1/regression/", json=body).status_code == 201
    run = client.post("/api/v1/regression/run").json()
    assert run["failed"] == 0, [r for r in run["results"] if not r["passed"]]
    assert run["total"] == len(GOLDEN)
    assert all(t["last_result"] is True for t in client.get("/api/v1/regression/").json())


def test_negative_regression_test_expects_no_match(client: TestClient) -> None:
    client.post(
        "/api/v1/regression/",
        json={"name": "weather", "phrase": "what is the weather", "expected_intent": "no_match"},
    )
    assert client.post("/api/v1/regression/run").json()["passed"] == 1


def test_failed_utterance_promotion_loop(client: TestClient) -> None:
    """no_match -> failed utterance -> promote -> phrase + regression test -> now matches."""
    text = "illuminate the kitchen"
    first = client.post("/api/v1/recognition/test_text", json={"text": text}).json()
    assert first["matched"] is False

    fu = client.post(
        "/api/v1/failed_utterances/", json={"transcript": text, "recognition_log_id": first["recognition_log_id"]}
    ).json()
    promoted = client.post(
        f"/api/v1/failed_utterances/{fu['id']}/promote", params={"intent_name": "light_turn_on"}
    ).json()
    assert promoted["phrase"]["phrase"] == "illuminate the {room_name}"
    assert promoted["failed_utterance"]["promoted_to_phrase"] is True
    assert promoted["failed_utterance"]["reviewed"] is True
    assert promoted["regression_test"]["expected_slots"] == {"room_name": "kitchen"}

    again = client.post("/api/v1/recognition/test_text", json={"text": "illuminate the bedroom"}).json()
    assert again["intent_name"] == "light_turn_on"
    assert again["slot_values"] == {"room_name": "bedroom"}

    run = client.post("/api/v1/regression/run").json()
    assert run["passed"] == run["total"] == 1


def test_regression_detects_breaking_change(client: TestClient) -> None:
    client.post(
        "/api/v1/regression/",
        json={
            "name": "kitchen_on",
            "phrase": "turn on the kitchen light",
            "expected_intent": "light_turn_on",
            "expected_slots": {"room_name": "kitchen"},
        },
    )
    intent = next(i for i in client.get("/api/v1/intents/").json() if i["name"] == "light_turn_on")
    etag = client.get(f"/api/v1/intents/{intent['id']}").headers["etag"]
    assert (
        client.patch(f"/api/v1/intents/{intent['id']}", json={"enabled": False}, headers={"If-Match": etag}).status_code
        == 200
    )
    run = client.post("/api/v1/regression/run").json()
    assert run["failed"] == 1
    assert "expected intent" in run["results"][0]["failure_reason"]
