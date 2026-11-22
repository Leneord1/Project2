"""Unit tests for AutoGen-style LinkedIn microservice."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

# Force mock mode before importing the app module.
os.environ["MOCK_LLM"] = "true"
os.environ.pop("OPENAI_API_KEY", None)

from main import app  # noqa: E402


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Build TestClient with mock LLM enabled."""
    monkeypatch.setenv("MOCK_LLM", "true")
    return TestClient(app)


def test_health(client: TestClient) -> None:
    """Health endpoint reports ok."""
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "model" in body


def test_linkedin_requires_brand(client: TestClient) -> None:
    """Missing brand returns validation error."""
    response = client.post("/linkedin", json={})
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "validation_error"


def test_linkedin_mock_pipeline(client: TestClient) -> None:
    """Mock agents return required LinkedIn fields."""
    payload = {
        "brand": {
            "name": "FinEdge Mumbai",
            "voice": "authoritative",
            "tone": "professional",
            "forbidden_topics": ["guaranteed returns"],
            "cta_style": "soft CTA",
        },
        "context": {
            "topic_hints": ["UPI growth", "fraud detection"],
            "audience": "CFOs",
            "campaign": "Trust",
            "product_focus": "payments",
        },
        "options": {"idea_count": 3, "min_confidence": 0.7},
    }
    response = client.post("/linkedin", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert len(body["ideas"]) == 3
    assert body["draft"]
    assert 0.0 <= body["confidence"] <= 1.0
    assert body["hashtags"]
    assert body["meta"]["agents"] == ["ideation", "drafting", "hashtags", "critic"]
    assert body["meta"]["meets_threshold"] is True


def test_forbidden_topic_caps_confidence(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Forbidden topic in draft caps confidence."""
    from main import LinkedInRequest, mock_pipeline

    def polluted(req: LinkedInRequest):
        ideas, _draft, hashtags, _confidence, reason = mock_pipeline(req)
        return ideas, "Discuss guaranteed returns carefully.", hashtags, 0.9, reason

    monkeypatch.setattr("main.mock_pipeline", polluted)
    payload = {
        "brand": {
            "name": "FinEdge Mumbai",
            "forbidden_topics": ["guaranteed returns"],
        },
        "context": {"topic_hints": ["payments"]},
        "options": {"idea_count": 1, "min_confidence": 0.7},
    }
    response = client.post("/linkedin", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["confidence"] <= 0.3
    assert "guaranteed returns" in body["meta"]["flags"]
