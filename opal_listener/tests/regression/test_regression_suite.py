"""Golden text set: every phrase must keep resolving to its expected intent and slots."""

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


def test_disabled_intent_breaks_golden_phrase(client: TestClient) -> None:
    """A config change that disables an intent must break its golden phrase, and
    re-enabling it must restore the match — the same regression the retired
    /api/v1/regression/run surface guarded."""
    intent = next(i for i in client.get("/api/v1/intent/").json() if i["name"] == "light_turn_on")
    etag = client.get(f"/api/v1/intent/{intent['id']}").headers["etag"]
    assert (
        client.patch(f"/api/v1/intent/{intent['id']}", json={"enabled": False}, headers={"If-Match": etag}).status_code
        == 200
    )
    try:
        result = client.post(
            "/api/v1/recognition/test_text",
            json={"text": "turn on the kitchen light"},
            params={"save_log": False},
        ).json()
        assert not (result["matched"] and result["intent_name"] == "light_turn_on"), result
    finally:
        etag = client.get(f"/api/v1/intent/{intent['id']}").headers["etag"]
        client.patch(f"/api/v1/intent/{intent['id']}", json={"enabled": True}, headers={"If-Match": etag})
