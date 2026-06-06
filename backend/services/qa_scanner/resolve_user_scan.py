#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from user_cookie_store import _probe_cookie_for_user, _store_cookie, auto_resolve_cookie
from scanner_paths import browser_profiles_dir, data_dir, har_captures_dir, runtime_dir, user_cookies_dir


def _script_dir() -> Path:
    return Path(__file__).resolve().parent


def _normalize_username(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return raw
    if raw.startswith("jr-") and raw.endswith("-ty"):
        return raw
    return f"jr-{raw.removeprefix('jr-').removesuffix('-ty')}-ty"


def _run(cmd: list[str], title: str) -> dict[str, object]:
    started = datetime.now().isoformat()
    proc = subprocess.run(cmd, check=False)
    return {
        "title": title,
        "started_at": started,
        "returncode": proc.returncode,
        "cmd": cmd,
    }


def _account_path(account_dir: str, username: str) -> Path:
    return Path(account_dir) / f"{_normalize_username(username)}.json"


def _account_status(account_dir: str, username: str) -> dict[str, object]:
    path = _account_path(account_dir, username)
    if not path.exists():
        return {"exists": False, "account_file": str(path)}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:
        return {"exists": False, "account_file": str(path), "error": f"{type(exc).__name__}: {exc}"}
    if not isinstance(data, dict):
        return {"exists": False, "account_file": str(path), "error": "account JSON is not an object"}
    cookie = str(data.get("scanner_cookie") or data.get("cookie") or "").strip()
    session_value = str(data.get("scanner_session") or data.get("session") or "").strip()
    hashes = [str(h).strip() for h in (data.get("hashes") or []) if str(h).strip()]
    return {
        "exists": True,
        "account_file": str(path),
        "has_user_id": bool(str(data.get("user_id") or data.get("worker_id") or "").strip()),
        "has_cookie": bool(cookie),
        "cookie_length": len(cookie),
        "has_session": bool(session_value),
        "session_length": len(session_value),
        "hash_count": len(hashes),
        "identity_status": data.get("identity_status"),
    }


def _cookie_header_from_playwright(cookies: list[dict[str, object]], host: str) -> str:
    wanted: list[str] = []
    host = host.lower().lstrip(".")
    priority = ("Authorization", "mashup")
    seen: set[str] = set()

    def add_cookie(item: dict[str, object]) -> None:
        name = str(item.get("name") or "").strip()
        value = str(item.get("value") or "").strip()
        if not name or not value or name in seen:
            return
        seen.add(name)
        wanted.append(f"{name}={value}")

    for cookie_name in priority:
        for item in cookies:
            if str(item.get("name") or "") != cookie_name:
                continue
            domain = str(item.get("domain") or "").lower().lstrip(".")
            if not host or not domain or host.endswith(domain) or domain.endswith(host):
                add_cookie(item)

    for item in cookies:
        domain = str(item.get("domain") or "").lower().lstrip(".")
        if host and domain and not (host.endswith(domain) or domain.endswith(host)):
            continue
        add_cookie(item)
    return "; ".join(wanted)


def _dedupe_cookie_candidates(candidates: list[dict[str, object]]) -> list[dict[str, object]]:
    seen: set[str] = set()
    unique: list[dict[str, object]] = []
    for item in candidates:
        cookie = str(item.get("cookie") or "").strip()
        if len(cookie) < 20 or cookie in seen:
            continue
        seen.add(cookie)
        unique.append(item)
    return unique


def _capture_session_interactive(
    *,
    username: str,
    url: str,
    har_path: Path | None,
    account_dir: str,
    base_url: str,
    host_header: str,
    timeout: int,
    profile_dir: Path | None = None,
    capture_wait_sec: int = 90,
) -> dict[str, object]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Playwright not available: {type(exc).__name__}: {exc}. Install with 'pip install playwright' and 'python -m playwright install'.",
        }

    if har_path:
        har_path.parent.mkdir(parents=True, exist_ok=True)
    if profile_dir:
        profile_dir.mkdir(parents=True, exist_ok=True)
    host = host_header.lower().lstrip(".")
    request_cookie_candidates: list[dict[str, object]] = []

    def remember_request_cookie(request: object) -> None:
        try:
            request_url = str(getattr(request, "url", "") or "")
            if host and host not in request_url.lower():
                return
            try:
                headers = request.all_headers()
            except Exception:
                headers = getattr(request, "headers", {}) or {}
            cookie_header = ""
            auth_header = ""
            for key, value in dict(headers).items():
                lower = str(key).lower()
                if lower == "cookie":
                    cookie_header = str(value or "").strip()
                elif lower == "authorization":
                    auth_header = str(value or "").strip()
            if cookie_header:
                request_cookie_candidates.append(
                    {
                        "cookie": cookie_header,
                        "source": f"browser_request:{request_url.split('?', 1)[0]}",
                    }
                )
            elif auth_header:
                request_cookie_candidates.append(
                    {
                        "cookie": auth_header if auth_header.lower().startswith("authorization=") else f"Authorization={auth_header}",
                        "source": f"browser_authorization_header:{request_url.split('?', 1)[0]}",
                    }
                )
        except Exception:
            return

    with sync_playwright() as p:
        context_kwargs: dict[str, object] = {}
        if har_path:
            context_kwargs.update({"record_har_path": str(har_path), "record_har_content": "embed"})
        if profile_dir:
            context = p.chromium.launch_persistent_context(
                str(profile_dir),
                headless=False,
                **context_kwargs,
            )
            browser = None
        else:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(**context_kwargs)
        context.on("request", remember_request_cookie)
        page = context.new_page()
        page.goto(url)
        print("\n=== Session capture ===")
        print(f"Target user: {username}")
        print("1) Login with exactly this target user in the opened browser.")
        print("2) Open/refresh the worker job page or wait until the platform calls worker-jobs.")
        print(f"3) The tool will wait up to {capture_wait_sec}s, read the browser request Cookie header, validate the user, then store it.\n")
        deadline = time.time() + max(1, capture_wait_sec)
        while time.time() < deadline:
            has_auth_header = any("authorization=" in str(item.get("cookie") or "").lower() for item in request_cookie_candidates)
            if has_auth_header:
                break
            try:
                context_cookies = context.cookies()
                if any(str(item.get("name") or "").lower() == "authorization" for item in context_cookies):
                    break
            except Exception:
                pass
            try:
                page.wait_for_timeout(1000)
            except Exception:
                time.sleep(1)
        try:
            page.goto(
                f"http://{host_header}/appen/backend/job/worker-jobs"
                "?jobStatusList=LAUNCH&jobStatusList=RUNNING&jobStatusList=PAUSE"
                "&statusList=CONFIRMED&jobName=&sortBy=CONFIRM_TIME&pageIndex=0&pageSize=10",
                wait_until="networkidle",
                timeout=timeout * 1000,
            )
        except Exception:
            pass
        try:
            page.wait_for_timeout(500)
        except Exception:
            pass
        cookies = context.cookies()
        context.close()
        if browser is not None:
            browser.close()

    cookie_candidates = list(request_cookie_candidates)
    cookie_from_context = _cookie_header_from_playwright(cookies, host_header)
    if cookie_from_context:
        cookie_candidates.append({"cookie": cookie_from_context, "source": "browser_context_cookies"})
    cookie_candidates = _dedupe_cookie_candidates(cookie_candidates)
    if not cookie_candidates:
        return {
            "ok": False,
            "error": "No usable platform cookies were captured from the browser session or request headers.",
            "cookie_count": len(cookies),
            "har_path": str(har_path) if har_path else "",
            "profile_dir": str(profile_dir) if profile_dir else "",
        }
    probes: list[dict[str, object]] = []
    for candidate in cookie_candidates:
        cookie = str(candidate.get("cookie") or "")
        source = str(candidate.get("source") or "playwright_browser_session")
        probe = _probe_cookie_for_user(
            cookie=cookie,
            username=username,
            base_url=base_url,
            host_header=host_header,
            timeout=timeout,
            accept_empty=False,
        )
        probes.append({"source": source, "length": len(cookie), "probe": probe})
        if probe.get("match"):
            stored = _store_cookie(
                account_dir=account_dir,
                username=username,
                cookie=cookie,
                source=source,
            )
            return {
                "ok": bool(stored.get("ok")),
                "username": username,
                "cookie_count": len(cookies),
                "request_cookie_candidates": len(cookie_candidates),
                "stored": stored,
                "probe": probe,
                "matched_source": source,
                "har_path": str(har_path) if har_path else "",
                "profile_dir": str(profile_dir) if profile_dir else "",
            }
    return {
        "ok": False,
        "error": "Captured browser cookies exist, but none matched the requested username.",
        "cookie_count": len(cookies),
        "request_cookie_candidates": len(cookie_candidates),
        "probes": probes[-5:],
        "har_path": str(har_path) if har_path else "",
        "profile_dir": str(profile_dir) if profile_dir else "",
    }


def _auto_login_with_playwright(
    *,
    username: str,
    password: str,
    account_dir: str,
    base_url: str,
    host_header: str,
    timeout: int,
    headless: bool = True,
) -> dict[str, object]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Playwright not available: {type(exc).__name__}: {exc}.",
        }

    def cookie_header_from_context(cookies: list[dict[str, object]]) -> str:
        out: list[str] = []
        seen: set[str] = set()
        for cookie_name in ("Authorization", "mashup"):
            for item in cookies:
                if str(item.get("name") or "") != cookie_name:
                    continue
                value = str(item.get("value") or "").strip()
                if value and cookie_name not in seen:
                    seen.add(cookie_name)
                    out.append(f"{cookie_name}={value}")
        for item in cookies:
            name = str(item.get("name") or "").strip()
            value = str(item.get("value") or "").strip()
            if name and value and name not in seen:
                seen.add(name)
                out.append(f"{name}={value}")
        return "; ".join(out)

    request_cookie_candidates: list[dict[str, object]] = []
    host = host_header.lower().lstrip(".")
    login_url = f"{base_url.rstrip('/')}/appen/ui#/user/login"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()

        def remember_request_cookie(request: object) -> None:
            try:
                request_url = str(getattr(request, "url", "") or "")
                if host and host not in request_url.lower():
                    return
                headers = getattr(request, "headers", {}) or {}
                cookie_header = str(dict(headers).get("cookie") or "").strip()
                if cookie_header:
                    request_cookie_candidates.append(
                        {
                            "cookie": cookie_header,
                            "source": f"browser_auto_login_request:{request_url.split('?', 1)[0]}",
                        }
                    )
            except Exception:
                return

        context.on("request", remember_request_cookie)
        page = context.new_page()
        try:
            page.goto(login_url, wait_until="domcontentloaded", timeout=timeout * 1000)
            page.fill("#name", username, timeout=timeout * 1000)
            page.fill("#password", password, timeout=timeout * 1000)
            page.click("button[type=submit]", timeout=timeout * 1000)
            page.wait_for_timeout(5000)
        except Exception as exc:
            body_preview = ""
            try:
                body_preview = page.locator("body").inner_text(timeout=2000)[:300]
            except Exception:
                body_preview = ""
            context.close()
            browser.close()
            return {
                "ok": False,
                "error": f"Auto login failed: {type(exc).__name__}: {exc}",
                "body_preview": body_preview,
            }

        cookies = context.cookies()
        cookie_candidates = list(request_cookie_candidates)
        context_cookie = cookie_header_from_context(cookies)
        if context_cookie:
            cookie_candidates.append({"cookie": context_cookie, "source": "playwright_auto_login_context"})
        cookie_candidates = _dedupe_cookie_candidates(cookie_candidates)
        context.close()
        browser.close()

    probes: list[dict[str, object]] = []
    for candidate in cookie_candidates:
        cookie = str(candidate.get("cookie") or "")
        source = str(candidate.get("source") or "playwright_auto_login")
        probe = _probe_cookie_for_user(
            cookie=cookie,
            username=username,
            base_url=base_url,
            host_header=host_header,
            timeout=timeout,
            accept_empty=False,
        )
        probes.append({"source": source, "length": len(cookie), "probe": probe})
        if probe.get("match"):
            stored = _store_cookie(
                account_dir=account_dir,
                username=username,
                cookie=cookie,
                source=source,
            )
            return {
                "ok": bool(stored.get("ok")),
                "username": username,
                "cookie_count": len(cookies),
                "matched_source": source,
                "stored": stored,
                "probe": probe,
            }

    return {
        "ok": False,
        "error": "Auto login did not produce a matching user cookie.",
        "cookie_count": len(cookies),
        "candidate_count": len(cookie_candidates),
        "probes": probes[-5:],
    }


def _flatten(values: Iterable[str] | None) -> list[str]:
    return [str(v) for v in (values or []) if str(v).strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve cookie from HAR and run scan for username(s).")
    parser.add_argument("usernames", nargs="+", help="Usernames, e.g. hoangtrungmanh")
    parser.add_argument("--cookie-dir", default=str(user_cookies_dir()))
    parser.add_argument("--cookie-file", action="append", default=[])
    parser.add_argument("--har-dir", action="append", default=[])
    parser.add_argument("--har-file", action="append", default=[])
    parser.add_argument("--capture-session", action="store_true", help="Open browser, let the target user login, capture and store that user's cookie.")
    parser.add_argument("--capture-har", action="store_true", help="Also record a HAR while capturing the browser session.")
    parser.add_argument("--capture-url", default="http://global-autolabeling-service.evad.xiaomi.srv/appen/ui")
    parser.add_argument(
        "--capture-profile-dir",
        default="",
        help="Optional persistent Playwright profile dir. Default uses data/scanner/browser_profiles/<username>.",
    )
    parser.add_argument("--capture-wait-sec", type=int, default=90, help="Seconds to keep the browser open while waiting for login/cookies.")
    parser.add_argument("--har-output-dir", default=str(har_captures_dir()))
    parser.add_argument("--account-dir", default=str(data_dir() / "user"))
    parser.add_argument("--base-url", default="http://global-autolabeling-service.evad.xiaomi.srv")
    parser.add_argument("--host-header", default="global-autolabeling-service.evad.xiaomi.srv")
    parser.add_argument("--timeout", type=int, default=12)
    parser.add_argument("--password", default="Biaozhu123", help="Password used by browser auto-login.")
    parser.add_argument("--no-auto-login", action="store_true", help="Skip username/password browser auto-login before capture fallback.")
    parser.add_argument("--show-login-browser", action="store_true", help="Show browser during auto-login instead of headless mode.")
    parser.add_argument("--accept-empty", action="store_true")
    parser.add_argument("--prepare-only", action="store_true", help="Only resolve cookie + prepare user context")
    parser.add_argument("--dry-run", action="store_true", help="Run scan in dry-run mode")
    parser.add_argument(
        "--no-auto-capture-when-missing",
        action="store_true",
        help="Do not open browser capture automatically when no per-user cookie is found.",
    )
    args = parser.parse_args()

    usernames = [_normalize_username(u) for u in args.usernames if u.strip()]
    if not usernames:
        print("No usernames provided.")
        return 1

    har_dirs = _flatten(args.har_dir)
    if not har_dirs:
        for fallback in (str(har_captures_dir()), str(runtime_dir())):
            if Path(fallback).exists():
                har_dirs.append(fallback)

    overall: dict[str, object] = {
        "source": "resolve-user-scan",
        "started_at": datetime.now().isoformat(),
        "results": [],
    }

    for username in usernames:
        result: dict[str, object] = {"username": username}
        capture_info = None
        har_files = list(args.har_file)
        if args.capture_session or args.capture_har:
            profile_dir = Path(args.capture_profile_dir) if args.capture_profile_dir else browser_profiles_dir() / username
            har_path = (
                Path(args.har_output_dir) / f"{username}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.har"
                if args.capture_har
                else None
            )
            capture_info = _capture_session_interactive(
                username=username,
                url=args.capture_url,
                har_path=har_path,
                account_dir=args.account_dir,
                base_url=args.base_url,
                host_header=args.host_header,
                timeout=args.timeout,
                profile_dir=profile_dir,
                capture_wait_sec=args.capture_wait_sec,
            )
            result["session_capture"] = capture_info
            if not capture_info.get("ok"):
                result["ok"] = False
                result["error"] = capture_info.get("error") or "Session capture failed"
                overall["results"].append(result)
                continue
            if har_path:
                har_files.append(str(har_path))

        if capture_info and capture_info.get("ok"):
            result["cookie_resolution"] = {"ok": True, "source": "playwright_browser_session"}
        else:
            cookie_files = [*args.cookie_file, str(_account_path(args.account_dir, username))]
            resolve = auto_resolve_cookie(
                username=username,
                cookie_dir=args.cookie_dir,
                cookie_files=cookie_files,
                har_dirs=har_dirs,
                har_files=har_files,
                account_dir=args.account_dir,
                base_url=args.base_url,
                host_header=args.host_header,
                timeout=args.timeout,
                accept_empty=args.accept_empty,
            )
            result["cookie_resolution"] = {k: v for k, v in resolve.items() if k != "candidates"}
            if not resolve.get("ok"):
                if not args.no_auto_login:
                    login_info = _auto_login_with_playwright(
                        username=username,
                        password=args.password,
                        account_dir=args.account_dir,
                        base_url=args.base_url,
                        host_header=args.host_header,
                        timeout=args.timeout,
                        headless=not args.show_login_browser,
                    )
                    result["auto_login"] = login_info
                    if login_info.get("ok"):
                        result["cookie_resolution"] = {"ok": True, "source": "playwright_auto_login"}
                        capture_info = {"ok": True, "source": "playwright_auto_login"}
                    else:
                        capture_info = login_info
                if capture_info and capture_info.get("ok"):
                    result["session_capture"] = capture_info
                else:
                    if args.no_auto_capture_when_missing:
                        result["ok"] = False
                        result["error"] = (
                            (capture_info or {}).get("error")
                            or resolve.get("error")
                            or "No matching cookie found"
                        )
                        result["account_status"] = _account_status(args.account_dir, username)
                        overall["results"].append(result)
                        continue
                    profile_dir = Path(args.capture_profile_dir) if args.capture_profile_dir else browser_profiles_dir() / username
                    auto_har_path = (
                        Path(args.har_output_dir) / f"{username}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.har"
                        if args.capture_har
                        else None
                    )
                    capture_info = _capture_session_interactive(
                        username=username,
                        url=args.capture_url,
                        har_path=auto_har_path,
                        account_dir=args.account_dir,
                        base_url=args.base_url,
                        host_header=args.host_header,
                        timeout=args.timeout,
                        profile_dir=profile_dir,
                        capture_wait_sec=args.capture_wait_sec,
                    )
                    result["session_capture"] = capture_info
                    if not capture_info.get("ok"):
                        result["ok"] = False
                        result["error"] = capture_info.get("error") or resolve.get("error") or "No matching cookie found"
                        result["account_status"] = _account_status(args.account_dir, username)
                        overall["results"].append(result)
                        continue
                    result["cookie_resolution"] = {"ok": True, "source": capture_info.get("source") or capture_info.get("matched_source") or "playwright_browser_session"}

        prepare_cmd = [
            sys.executable,
            str(_script_dir() / "prepare_user_context.py"),
            username,
            "--account-dir",
            args.account_dir,
            "--cookie-dir",
            args.cookie_dir,
            "--base-url",
            args.base_url,
            "--host-header",
            args.host_header,
            "--timeout",
            str(args.timeout),
        ]
        prepare_step = _run(prepare_cmd, "prepare_user_context")
        result["prepare_step"] = prepare_step
        result["account_status_after_prepare"] = _account_status(args.account_dir, username)
        if prepare_step["returncode"] != 0:
            result["ok"] = False
            result["error"] = "prepare_user_context failed"
            overall["results"].append(result)
            continue

        if args.prepare_only:
            result["ok"] = True
            overall["results"].append(result)
            continue

        scan_cmd = [
            sys.executable,
            str(_script_dir() / "scan_users_optimized.py"),
            username,
            "--cookie-dir",
            args.cookie_dir,
            "--dashboard-account-dir",
            args.account_dir,
            "--base-url",
            args.base_url,
            "--host-header",
            args.host_header,
            "--timeout",
            str(args.timeout),
        ]
        if args.dry_run:
            scan_cmd.append("--dry-run")
        scan_step = _run(scan_cmd, "scan_users_optimized")
        result["scan_step"] = scan_step
        result["account_status_after_scan"] = _account_status(args.account_dir, username)
        result["ok"] = scan_step["returncode"] == 0
        if not result["ok"]:
            result["error"] = "scan_users_optimized failed"

        overall["results"].append(result)

    overall["finished_at"] = datetime.now().isoformat()
    print(json.dumps(overall, ensure_ascii=False, indent=2))
    return 0 if all(r.get("ok") for r in overall["results"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
