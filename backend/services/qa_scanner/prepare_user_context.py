#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scan_users_optimized import (
    DEFAULT_BASE_URL,
    DEFAULT_HOST_HEADER,
    _cookie_for_user,
    _infer_worker_id_from_jobs,
    _make_session,
    _normalize_base_url,
    _normalize_username,
    _save_account_identity,
    _session_from_cookie,
    _short_name,
    fetch_jobs_for_user,
    resolve_user,
)
from user_cookie_store import _probe_cookie_for_user, auto_resolve_cookie
from scanner_paths import data_dir, har_captures_dir, prepared_users_dir, user_cookies_dir

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _account_path(account_dir: str, username: str) -> Path:
    return Path(account_dir) / f"{_normalize_username(username)}.json"


def _load_account(account_dir: str, username: str) -> dict[str, Any]:
    normalized = _normalize_username(username)
    path = _account_path(account_dir, normalized)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {
        "username": normalized,
        "display_name": _short_name(normalized),
        "user_id": None,
        "hashes": [],
        "processed_qa_keys": [],
        "last_scan": None,
        "status": "idle",
    }


def _save_account(account_dir: str, username: str, account: dict[str, Any]) -> Path:
    path = _account_path(account_dir, username)
    path.parent.mkdir(parents=True, exist_ok=True)
    account["username"] = _normalize_username(username)
    account.setdefault("display_name", _short_name(username))
    account.setdefault("hashes", [])
    account.setdefault("processed_qa_keys", [])
    account.setdefault("status", "idle")
    path.write_text(json.dumps(account, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _validate_cookie(session: requests.Session, base_url: str, timeout: int) -> dict[str, Any]:
    url = f"{base_url}/appen/backend/job/worker-jobs"
    try:
        resp = session.get(
            url,
            params={
                "jobStatusList": ["LAUNCH", "RUNNING", "PAUSE"],
                "statusList": ["CONFIRMED"],
                "jobName": "",
                "sortBy": "CONFIRM_TIME",
                "pageIndex": 0,
                "pageSize": 1,
            },
            timeout=timeout,
        )
    except Exception as exc:
        return {"ok": False, "status": "network_error", "error": f"{type(exc).__name__}: {exc}"}
    try:
        payload = resp.json()
    except Exception:
        payload = resp.text
    return {
        "ok": resp.status_code == 200,
        "status": "ok" if resp.status_code == 200 else ("invalid_cookie" if resp.status_code == 401 else "http_error"),
        "status_code": resp.status_code,
        "error": "" if resp.status_code == 200 else str(payload)[:300],
    }


def _redact_account(account: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in account.items() if k not in {"scanner_cookie", "cookie", "scanner_session", "session"}}
    cookie = str(account.get("scanner_cookie") or account.get("cookie") or "").strip()
    session_value = str(account.get("scanner_session") or account.get("session") or "").strip()
    out["has_scanner_cookie"] = bool(cookie)
    out["scanner_cookie_length"] = len(cookie)
    out["has_scanner_session"] = bool(session_value)
    out["scanner_session_length"] = len(session_value)
    return out


def _write_report(path: str, report: dict[str, Any]) -> None:
    if not path:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare one user's scan context: account JSON, cookie status, identity, jobs.")
    parser.add_argument("username")
    parser.add_argument("--cookie-file", default="")
    parser.add_argument("--cookie-dir", default=str(user_cookies_dir()))
    parser.add_argument("--har-dir", action="append", default=[])
    parser.add_argument("--har-file", action="append", default=[])
    parser.add_argument("--no-auto-cookie", action="store_true")
    parser.add_argument("--allow-shared-cookie", action="store_true", help="Allow --cookie-file as a shared fallback. Default requires a per-user cookie.")
    parser.add_argument("--account-dir", default=str(data_dir() / "user"))
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--host-header", default=DEFAULT_HOST_HEADER)
    parser.add_argument("--timeout", type=int, default=12)
    parser.add_argument("--job-page-size", type=int, default=200)
    parser.add_argument("--job-max-pages", type=int, default=1)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--aggressive-user-lookup", action="store_true")
    parser.add_argument("--aggressive-job-lookup", action="store_true")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    username = _normalize_username(args.username)
    base_url = _normalize_base_url(args.base_url)
    account = _load_account(args.account_dir, username)
    account_path = _save_account(args.account_dir, username, account)

    # Reuse scanner cookie resolution by providing compatible arg names.
    compat_args = argparse.Namespace(
        cookie_dir=args.cookie_dir,
        cookie_file=args.cookie_file,
        dashboard_account_dir=args.account_dir,
        allow_shared_cookie=args.allow_shared_cookie,
    )
    default_cookie = ""
    if args.allow_shared_cookie and args.cookie_file:
        try:
            default_cookie = Path(args.cookie_file).read_text(encoding="utf-8", errors="ignore").strip()
        except Exception:
            default_cookie = ""
    cookie, cookie_source = _cookie_for_user(compat_args, username, default_cookie)

    cookie_auto_result: dict[str, Any] | None = None
    if not cookie and not args.no_auto_cookie:
        default_har_dir = har_captures_dir()
        har_dirs = list(args.har_dir)
        if default_har_dir.exists():
            har_dirs.append(str(default_har_dir))
        cookie_files = [args.cookie_file] if args.cookie_file else []
        cookie_auto_result = auto_resolve_cookie(
            username=username,
            cookie_dir=args.cookie_dir,
            cookie_files=cookie_files,
            har_dirs=har_dirs,
            har_files=args.har_file,
            account_dir=args.account_dir,
            base_url=base_url,
            host_header=args.host_header,
            timeout=args.timeout,
            accept_empty=False,
        )
        account = _load_account(args.account_dir, username)
        cookie, cookie_source = _cookie_for_user(compat_args, username, default_cookie)

    report: dict[str, Any] = {
        "source": "prepare-user-context",
        "generated_at": datetime.now().isoformat(),
        "username": username,
        "account_file": str(account_path),
        "cookie_source": cookie_source,
        "has_scanner_cookie": bool(cookie),
        "scanner_cookie_length": len(cookie),
        "account": _redact_account(account),
    }
    if cookie_auto_result is not None:
        report["cookie_auto_resolve"] = cookie_auto_result

    if not cookie:
        account["identity_status"] = "blocked_no_cookie"
        account["last_prepare_at"] = datetime.now().isoformat()
        _save_account(args.account_dir, username, account)
        report["ok"] = False
        report["error"] = "No matching per-user scanner_cookie found. Add this user's cookie to account JSON or provide a HAR/cookie pool that contains that user's session."
        report["account"] = _redact_account(account)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        _write_report(args.output, report)
        return 2

    session = _make_session(cookie, args.host_header)
    validation = _validate_cookie(session, base_url, args.timeout)
    report["cookie_validation"] = validation
    account["scanner_cookie_last_checked_at"] = datetime.now().isoformat()
    account["scanner_cookie_valid"] = bool(validation.get("ok"))
    if not validation.get("ok"):
        account["identity_status"] = "blocked_invalid_cookie"
        if validation.get("status") == "network_error":
            account["identity_status"] = "blocked_network"
        account["last_prepare_at"] = datetime.now().isoformat()
        _save_account(args.account_dir, username, account)
        report["ok"] = False
        report["error"] = (
            "Network error while validating scanner cookie."
            if validation.get("status") == "network_error"
            else "Scanner cookie is invalid or expired."
        )
        report["account"] = _redact_account(account)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        _write_report(args.output, report)
        return 3

    identity_probe = _probe_cookie_for_user(
        cookie=cookie,
        username=username,
        base_url=base_url,
        host_header=args.host_header,
        timeout=args.timeout,
        accept_empty=False,
    )
    report["cookie_identity"] = identity_probe
    if not identity_probe.get("match"):
        account["scanner_cookie_valid"] = False
        account["identity_status"] = "blocked_cookie_user_mismatch"
        account["last_prepare_at"] = datetime.now().isoformat()
        _save_account(args.account_dir, username, account)
        report["ok"] = False
        report["error"] = "Scanner cookie is valid but does not belong to the requested username."
        report["account"] = _redact_account(account)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        _write_report(args.output, report)
        return 3

    user = resolve_user(
        sess=session,
        username=username,
        base_url=base_url,
        timeout=args.timeout,
        skip_network=False,
        aggressive_lookup=args.aggressive_user_lookup,
    )
    report["resolved_user"] = {
        k: v
        for k, v in user.items()
        if k not in {"scanner_cookie", "cookie", "scanner_session", "session"}
    }

    jobs_path = prepared_users_dir() / username / f"ld_jobs_{username}.json"
    jobs_result = fetch_jobs_for_user(
        sess=session,
        user=user,
        base_url=base_url,
        output_path=jobs_path,
        page_size=args.job_page_size,
        max_pages=args.job_max_pages,
        timeout=args.timeout,
        force=args.force,
        aggressive_lookup=args.aggressive_job_lookup,
    )
    jobs = jobs_result.get("jobs") or []
    worker_id = _infer_worker_id_from_jobs(
        jobs,
        user.get("worker_id") or user.get("user_id") or user.get("ld_user_id"),
    )
    if not worker_id:
        identity_user = identity_probe.get("user") if isinstance(identity_probe, dict) else {}
        if isinstance(identity_user, dict):
            worker_id = str(
                identity_user.get("workerId")
                or identity_user.get("userId")
                or identity_user.get("id")
                or ""
            ).strip()
    report["jobs"] = {
        "ok": bool(jobs_result.get("ok")),
        "job_count": len(jobs),
        "path": jobs_result.get("path"),
        "source": jobs_result.get("source"),
        "errors": jobs_result.get("errors", []),
    }
    report["worker_id"] = worker_id

    if worker_id:
        user["worker_id"] = worker_id
        if not user.get("user_id"):
            user["user_id"] = worker_id
        user["identity_source"] = user.get("source") or "job-metadata"
        cookie_session = _session_from_cookie(cookie)
        normalized_account_path = _account_path(args.account_dir, username)
        cookie_source_name = (
            "account_json"
            if Path(cookie_source) == normalized_account_path
            else cookie_source
        )
        _save_account_identity(
            username,
            user,
            args.account_dir,
            cookie=cookie,
            cookie_source=cookie_source_name,
            cookie_valid=True,
            identity_status="ready",
        )
        account = _load_account(args.account_dir, username)
        if cookie_session:
            account["scanner_session"] = cookie_session
            account["session"] = cookie_session
            account["scanner_session_updated_at"] = datetime.now().isoformat()
        account["identity_status"] = "ready"
        account["last_prepare_at"] = datetime.now().isoformat()
        _save_account(args.account_dir, username, account)
        report["ok"] = True
        report["account"] = _redact_account(account)
    else:
        account["identity_status"] = "blocked_no_worker_id"
        account["last_prepare_at"] = datetime.now().isoformat()
        _save_account(args.account_dir, username, account)
        report["ok"] = False
        report["error"] = "No worker_id/user_id resolved from user lookup or job metadata."

    print(json.dumps(report, ensure_ascii=False, indent=2))
    _write_report(args.output, report)
    return 0 if report.get("ok") else 4


if __name__ == "__main__":
    raise SystemExit(main())
