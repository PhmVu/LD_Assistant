from __future__ import annotations

import logging
import os
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.auth import create_access_token, get_current_user
from core.config import settings
from core.ld_identity import normalize_labeler_username
from core.users import get_user, load_users, save_users, upsert_user


router = APIRouter(tags=["Auth"])
log = logging.getLogger("ld.auth")
INVALID_LD_MEMBER_MESSAGE = "Bạn không phải là thành viên LD, bạn không thể đăng nhập"
EMPTY_USERNAME_MESSAGE = "Hãy nhập username của bạn"
_BASE_DIR = Path(__file__).resolve().parents[1]
_DATA_DIR = _BASE_DIR / "data" if (_BASE_DIR / "data").exists() else _BASE_DIR.parent / "data"
_USER_DIR = Path(os.getenv("QA_USER_DIR", str(_DATA_DIR / "user")))


def _login_failure(detail: str = INVALID_LD_MEMBER_MESSAGE) -> dict:
    return {"ok": False, "detail": detail}


def _local_ld_account(full_username: str) -> dict | None:
    account_path = _USER_DIR / f"{full_username}.json"
    if not account_path.exists():
        return None
    try:
        import json

        data = json.loads(account_path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        log.warning("Cannot read local LD user account %s", account_path)
        return None
    return data if isinstance(data, dict) else None


def _issue_login_token(full_username: str, display_name: str, ld_user_id: str) -> dict:
    user = upsert_user(full_username, ld_user_id or full_username)
    if full_username in {u.lower() for u in settings.LD_ADMIN_USERS}:
        users_db = load_users()
        if full_username in users_db:
            users_db[full_username]["role"] = "admin"
            save_users(users_db)
        user["role"] = "admin"
    if user.get("display_name") == full_username or display_name:
        users_db = load_users()
        if full_username in users_db:
            users_db[full_username]["display_name"] = display_name
            save_users(users_db)
        user["display_name"] = display_name

    token = create_access_token({
        "sub": full_username,
        "role": user.get("role", "user"),
    })

    return {
        "ok": True,
        "token": token,
        "user": {**user, "display_name": display_name},
    }


class LoginRequest(BaseModel):
    username: str


def _login_success(data: object) -> tuple[bool, str | None]:
    if not isinstance(data, dict):
        return False, None
    payload = data.get("data")
    payload = payload if isinstance(payload, dict) else {}
    user_id = (
        payload.get("userId")
        or payload.get("user_id")
        or payload.get("id")
        or data.get("userId")
        or data.get("user_id")
    )
    code = data.get("code")
    ok = (
        code in {0, 200, "0", "200"}
        or data.get("success") is True
        or bool(user_id)
    )
    return ok, str(user_id) if user_id else None


@router.post("/api/auth/login")
async def login(body: LoginRequest):
    try:
        full_username, display_name = normalize_labeler_username(body.username)
    except ValueError:
        return _login_failure(EMPTY_USERNAME_MESSAGE)

    local_account = _local_ld_account(full_username)
    if local_account:
        ld_user_id = (
            local_account.get("user_id")
            or local_account.get("worker_id")
            or local_account.get("ld_user_id")
            or full_username
        )
        return _issue_login_token(full_username, display_name, str(ld_user_id))

    response: httpx.Response | None = None
    try:
        async with httpx.AsyncClient(
            verify=False,
            timeout=settings.LD_LOGIN_TIMEOUT_SECONDS,
        ) as client:
            response = await client.post(
                settings.LD_LOGIN_URL,
                json={
                    "username": full_username,
                    "password": settings.APPEN_SHARED_PASSWORD,
                },
            )
    except httpx.RequestError as exc:
        log.warning("Cannot connect to LD auth server for %s: %s", full_username, exc)
        raise HTTPException(
            status_code=503,
            detail="Hệ thống chưa thể xác thực LD. Vui lòng kiểm tra VPN hoặc mạng rồi thử lại.",
        )

    if not 200 <= response.status_code < 300:
        return _login_failure()

    try:
        payload = response.json()
    except Exception:
        payload = {}
    ok, ld_user_id = _login_success(payload)
    if not ok:
        return _login_failure()

    return _issue_login_token(full_username, display_name, ld_user_id or full_username)


@router.get("/api/auth/me")
async def get_me(current_user: dict = Depends(get_current_user)):
    username = current_user.get("sub")
    user = get_user(username)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user
