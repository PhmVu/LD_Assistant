from __future__ import annotations

import sys
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import users as users_core
from core.ld_identity import normalize_labeler_username
from routes import auth


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}
        self.cookies = {}

    def json(self):
        return self._payload


class FakeAsyncClient:
    response: FakeResponse = FakeResponse()
    error: Exception | None = None
    last_payload: dict | None = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str, json: dict):
        FakeAsyncClient.last_payload = json
        if FakeAsyncClient.error:
            raise FakeAsyncClient.error
        return FakeAsyncClient.response


def make_client(monkeypatch, tmp_path: Path) -> TestClient:
    monkeypatch.setattr(users_core, "USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(auth.httpx, "AsyncClient", FakeAsyncClient)
    app = FastAPI()
    app.include_router(auth.router)
    return TestClient(app)


def test_normalize_labeler_username():
    assert normalize_labeler_username("nguyenthanhtuan") == (
        "jr-nguyenthanhtuan-ty",
        "nguyenthanhtuan",
    )
    assert normalize_labeler_username("jr-nguyenthanhtuan") == (
        "jr-nguyenthanhtuan-ty",
        "nguyenthanhtuan",
    )
    assert normalize_labeler_username("jr-nguyenthanhtuan-ty") == (
        "jr-nguyenthanhtuan-ty",
        "nguyenthanhtuan",
    )


def test_login_success_uses_backend_shared_password(monkeypatch, tmp_path):
    FakeAsyncClient.error = None
    FakeAsyncClient.response = FakeResponse(200, {"code": 200, "data": {"userId": 123}})
    client = make_client(monkeypatch, tmp_path)

    response = client.post("/api/auth/login", json={"username": "nguyenthanhtuan"})

    assert response.status_code == 200
    body = response.json()
    assert body["user"]["username"] == "jr-nguyenthanhtuan-ty"
    assert body["user"]["display_name"] == "nguyenthanhtuan"
    assert FakeAsyncClient.last_payload == {
        "username": "jr-nguyenthanhtuan-ty",
        "password": auth.settings.APPEN_SHARED_PASSWORD,
    }


def test_login_rejects_non_team_member(monkeypatch, tmp_path):
    FakeAsyncClient.error = None
    FakeAsyncClient.response = FakeResponse(200, {"code": 401, "message": "bad user"})
    client = make_client(monkeypatch, tmp_path)

    response = client.post("/api/auth/login", json={"username": "notld"})

    assert response.status_code == 401
    assert response.json()["detail"] == "Bạn không phải thành viên team LD."
    assert not (tmp_path / "users.json").exists()


def test_login_network_error_is_not_mocked(monkeypatch, tmp_path):
    FakeAsyncClient.response = FakeResponse(200, {"code": 200, "data": {"userId": 1}})
    FakeAsyncClient.error = httpx.ConnectError("cannot resolve host")
    client = make_client(monkeypatch, tmp_path)

    response = client.post("/api/auth/login", json={"username": "nguyenthanhtuan"})

    assert response.status_code == 503
    assert "Không thể kết nối" in response.json()["detail"]
    assert not (tmp_path / "users.json").exists()
    FakeAsyncClient.error = None
