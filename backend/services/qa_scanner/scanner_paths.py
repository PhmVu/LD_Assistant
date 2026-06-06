from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    backend_dir = Path(__file__).resolve().parents[2]
    backend_data = backend_dir / "data"
    if backend_data.exists():
        return backend_data
    return backend_dir.parent / "data"


def runtime_dir() -> Path:
    path = Path(os.getenv("QA_SCANNER_RUNTIME_DIR", str(data_dir() / "scanner")))
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_cookies_dir() -> Path:
    path = Path(os.getenv("QA_SCANNER_COOKIE_DIR", str(runtime_dir() / "user_cookies")))
    path.mkdir(parents=True, exist_ok=True)
    return path


def har_captures_dir() -> Path:
    path = runtime_dir() / "har_captures"
    path.mkdir(parents=True, exist_ok=True)
    return path


def browser_profiles_dir() -> Path:
    path = runtime_dir() / "browser_profiles"
    path.mkdir(parents=True, exist_ok=True)
    return path


def prepared_users_dir() -> Path:
    path = runtime_dir() / "prepared_users"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_cookie_file() -> Path:
    return runtime_dir() / "cookie.txt"

