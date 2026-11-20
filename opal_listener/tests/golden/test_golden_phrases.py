"""Golden text set + failed-utterance promotion loop."""

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


def test_failed_utterance_promotion_loop(client: TestClient) -> None:
    """no_match -> failed utterance -> promote -> phrase -> now matches."""
    text = "illuminate the kitchen"
    first = client.post("/api/v1/recognition/test_text", json={"text": text}).json()
    assert first["matched"] is False

    fu = client.post(
        "/api/v1/failed_utterance/", json={"transcript": text, "recognition_log_id": first["recognition_log_id"]}
    ).json()
    intent = next(i for i in client.get("/api/v1/intent/").json() if i["name"] == "light_turn_on")
    phrases_etag = client.get(f"/api/v1/intent/{intent['id']}/phrase").headers["etag"]
    promoted = client.post(
        f"/api/v1/failed_utterance/{fu['id']}/promote",
        json={"intent_id": intent["id"]},
        headers={"If-Match": phrases_etag},
    ).json()
    assert promoted["phrase"]["phrase"] == "illuminate the {room_name}"
    assert promoted["failed_utterance"]["promoted_to_phrase"] is True
    assert promoted["failed_utterance"]["reviewed"] is True

    again = client.post("/api/v1/recognition/test_text", json={"text": "illuminate the bedroom"}).json()
    assert again["intent_name"] == "light_turn_on"
    assert again["slot_values"] == {"room_name": "bedroom"}
