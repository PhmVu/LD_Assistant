from __future__ import annotations

from pathlib import Path
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes import chat as chat_routes
from services.ld_ai.ld_brain import BrainResult


class _FakeBrain:
    def answer(self, **kwargs):
        return BrainResult(
            answer="VALIDATED ANSWER",
            used_llm=True,
            error=None,
            drawing_candidates=[],
            tool_call={"tool": "draw_lane_marking"},
            polish_status="accepted",
            length_ratio=1.12,
            validation_warnings=[],
        )


def test_stream_uses_validated_brain_answer(monkeypatch):
    monkeypatch.setattr(chat_routes, "_brain_singleton", _FakeBrain())
    app = FastAPI()
    app.include_router(chat_routes.router)
    client = TestClient(app)

    with client.stream(
        "POST",
        "/api/ld/chat/stream",
        data={"message": "vẽ xương cá", "history": "[]"},
    ) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())

    assert "VALIDATED ANSWER" in body
    assert "polish_status" in body
    assert "accepted" in body
