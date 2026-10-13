"""Negative text set: must always result in no_match (and be handled gracefully)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

CASES = yaml.safe_load((Path(__file__).resolve().parent / "negative_phrases.yaml").read_text())["negative"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["text"] or "<empty>")
def test_negative_phrase_is_no_match(client: TestClient, case: dict[str, Any]) -> None:
    response = client.post("/api/v1/recognition/test_text", json={"text": case["text"]}, params={"save_log": False})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["matched"] is False, result
    assert result["intent_name"] == "no_match"
    assert result["slot_values"] == {}
    assert result["no_match_reason"]
    if "reason" in case:
        assert result["no_match_reason"] == case["reason"], result


def test_no_match_results_are_logged_as_failures_only_for_audio_sources(client: TestClient) -> None:
    client.post("/api/v1/recognition/test_text", json={"text": "open the front door"})
    logs = client.get("/api/v1/logs/", params={"matched": False}).json()["items"]
    assert logs and logs[0]["intent_name"] == "no_match"
    assert client.get("/api/v1/failed_utterances/").json() == []
