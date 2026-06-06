#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    print("Need requests: pip install requests")
    sys.exit(1)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from user_cookie_store import _probe_cookie_for_user
from scanner_paths import data_dir


DEFAULT_BASE_URL = "http://10.79.0.80"
DEFAULT_HOST_HEADER = "global-autolabeling-service.evad.xiaomi.srv"
BASELINE_QA_SEC_PER_RECORD = 286.9 / 439.0
BASELINE_CURRENT_RECORDS = 439
HASH_PREFIX_RE = re.compile(r"^([a-f0-9]{32})/", re.IGNORECASE)
SENSITIVE_USER_KEYS = {"scanner_cookie", "cookie", "scanner_session", "session"}


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _script_dir() -> Path:
    return Path(__file__).resolve().parent


def _normalize_base_url(base_url: str) -> str:
    base_url = (base_url or "").strip().rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        base_url = "http://" + base_url
    return base_url


def _normalize_username(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return raw
    if raw.startswith("jr-") and raw.endswith("-ty"):
        return raw
    return f"jr-{raw.removeprefix('jr-').removesuffix('-ty')}-ty"


def _short_name(username: str) -> str:
    username = _normalize_username(username)
    return username.removeprefix("jr-").removesuffix("-ty")


def _safe_name(username: str) -> str:
    return _normalize_username(username).replace("/", "_").replace("\\", "_")


def _redact_user(user: dict[str, Any]) -> dict[str, Any]:
    redacted = {k: v for k, v in user.items() if k not in SENSITIVE_USER_KEYS}
    cookie = str(user.get("scanner_cookie") or user.get("cookie") or "").strip()
    session_value = str(user.get("scanner_session") or user.get("session") or "").strip()
    redacted["has_scanner_cookie"] = bool(cookie)
    redacted["scanner_cookie_length"] = len(cookie)
    redacted["has_scanner_session"] = bool(session_value)
    redacted["scanner_session_length"] = len(session_value)
    return redacted


def _session_from_cookie(cookie: str) -> str:
    for part in (cookie or "").split(";"):
        if "=" not in part:
            continue
        key, value = part.strip().split("=", 1)
        if key.strip().lower() == "authorization":
            return value.strip()
    return ""


def _merge_hashes(existing: Any, new_values: list[str] | None) -> list[str]:
    merged: list[str] = []
    for item in existing if isinstance(existing, list) else []:
        text = str(item).strip()
        if text and text not in merged:
            merged.append(text)
    for item in new_values or []:
        text = str(item).strip().lower()
        if text and text not in merged:
            merged.append(text)
    return merged


def _extract_hashes_from_qa_payload(payload: dict[str, Any]) -> list[str]:
    hashes: set[str] = set()

    def add_from_key(value: Any) -> None:
        key = str(value or "").strip()
        if not key:
            return
        match = HASH_PREFIX_RE.match(key)
        if match:
            hashes.add(match.group(1).lower())

    for row in payload.get("issue_rows") or []:
        if isinstance(row, dict):
            add_from_key(row.get("qaAnnotationKey"))

    for record in payload.get("records") or []:
        if not isinstance(record, dict):
            continue
        for review in record.get("qaReviews") or []:
            if isinstance(review, dict):
                add_from_key(review.get("qaAnnotationKey"))

    return sorted(hashes)


def _load_cookie(cookie_file: str) -> str:
    path = Path(cookie_file)
    if not path.exists():
        raise FileNotFoundError(f"cookie-file does not exist: {path}")
    cookie = path.read_text(encoding="utf-8", errors="ignore").strip()
    if not cookie:
        raise ValueError(f"cookie-file is empty: {path}")
    return cookie


def _load_optional_cookie(cookie_file: str) -> str:
    if not cookie_file:
        return ""
    path = Path(cookie_file)
    if not path.exists():
        return ""
    raw = path.read_text(encoding="utf-8", errors="ignore").strip()
    if not raw:
        return ""
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except Exception:
            data = None
        if isinstance(data, dict):
            return str(data.get("scanner_cookie") or data.get("cookie") or "").strip()
    return raw


def _account_cookie_path(args: argparse.Namespace, username: str) -> Path:
    return Path(args.dashboard_account_dir) / f"{_normalize_username(username)}.json"


def _cookie_for_user(args: argparse.Namespace, username: str, default_cookie: str) -> tuple[str, str]:
    account_path = _account_cookie_path(args, username)
    account_cookie = _load_optional_cookie(str(account_path))
    if account_cookie:
        return account_cookie, str(account_path)
    if args.cookie_dir:
        root = Path(args.cookie_dir)
        candidates = [
            root / f"{_normalize_username(username)}.txt",
            root / f"{_normalize_username(username)}.cookie",
            root / f"{_short_name(username)}.txt",
            root / f"{_short_name(username)}.cookie",
        ]
        for path in candidates:
            cookie = _load_optional_cookie(str(path))
            if cookie:
                return cookie, str(path)
    if getattr(args, "allow_shared_cookie", False) and default_cookie:
        return default_cookie, str(Path(args.cookie_file))
    return "", "missing_per_user_cookie"


def _make_session(cookie: str, host_header: str) -> requests.Session:
    sess = requests.Session()
    sess.headers.update(
        {
            "Accept": "application/json",
            "Cookie": cookie,
            "User-Agent": "ld-multi-user-scan/1.0",
        }
    )
    if host_header:
        sess.headers["Host"] = host_header
    return sess


def _preview(value: Any, limit: int = 300) -> str:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value)
    return text.replace("\r", " ").replace("\n", " ")[:limit]


def _request_json(
    sess: requests.Session,
    method: str,
    base_url: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    timeout: int,
) -> tuple[bool, Any, str]:
    url = f"{base_url}{path}"
    try:
        if method.upper() == "POST":
            resp = sess.post(url, params=params or {}, json=body or {}, timeout=timeout)
        else:
            resp = sess.get(url, params=params or {}, timeout=timeout)
    except Exception as exc:
        return False, None, f"{type(exc).__name__}: {exc}"
    try:
        payload = resp.json()
    except Exception:
        return False, None, f"HTTP {resp.status_code}: {_preview(resp.text)}"
    if resp.status_code != 200:
        return False, payload, f"HTTP {resp.status_code}: {_preview(payload)}"
    return True, payload, ""


def _iter_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    containers = [payload]
    data = payload.get("data")
    if isinstance(data, dict):
        containers.append(data)
    elif isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    for container in containers:
        for key in ("results", "records", "items", "list", "content", "rows"):
            value = container.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    return []


def _extract_total_pages(payload: Any) -> int | None:
    if not isinstance(payload, dict):
        return None
    for container in (payload, payload.get("data")):
        if isinstance(container, dict) and container.get("totalPages") is not None:
            try:
                return int(container.get("totalPages"))
            except (TypeError, ValueError):
                return None
    return None


def _candidate_text(row: dict[str, Any]) -> str:
    keys = (
        "username",
        "userName",
        "uniqueName",
        "name",
        "email",
        "workerName",
        "workerEmail",
        "displayName",
    )
    return " ".join(str(row.get(key) or "") for key in keys).lower()


def _ids_from_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for source, target in (
        ("id", "user_id"),
        ("userId", "user_id"),
        ("workerId", "worker_id"),
        ("workerName", "worker_name"),
        ("uniqueName", "unique_name"),
        ("username", "username"),
        ("userName", "username"),
        ("name", "name"),
        ("email", "email"),
    ):
        if row.get(source) is not None and out.get(target) is None:
            out[target] = row.get(source)
    return out


def _local_account(username: str) -> dict[str, Any] | None:
    candidates = [
        Path("data") / "user" / f"{username}.json",
        Path("data") / "accounts" / f"{username}.json",
        Path("data") / "ld_memory" / "qa" / "accounts" / f"{username}.json",
    ]
    best: dict[str, Any] | None = None
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        if isinstance(data, dict):
            data.setdefault("username", username)
            data["source"] = f"local:{path}"
            if data.get("worker_id") or data.get("user_id") or data.get("ld_user_id"):
                return data
            if best is None:
                best = data
    return best


def resolve_user(
    *,
    sess: requests.Session,
    username: str,
    base_url: str,
    timeout: int,
    skip_network: bool,
    aggressive_lookup: bool,
) -> dict[str, Any]:
    username = _normalize_username(username)
    simple = _short_name(username)
    local = _local_account(username)
    probes: list[dict[str, Any]] = []
    if local:
        probes.append(local)
        if skip_network:
            local["username"] = username
            local["display_name"] = simple
            local["resolved"] = bool(local.get("user_id") or local.get("worker_id") or local.get("ld_user_id"))
            return local

    query_values = [username, simple]
    get_specs = [
        ("/appen/backend/user/all", "keyword"),
        ("/appen/backend/user/all", "username"),
        ("/appen/backend/um/bpo-worker/list", "keyword"),
    ]
    post_specs = [
        ("/appen/backend/job/internal-worker-candidates", "keyword"),
    ]
    if aggressive_lookup:
        get_specs.extend([
            ("/appen/backend/user/all", "uniqueName"),
            ("/appen/backend/user/all", "name"),
            ("/appen/backend/user/all", "emailsOrNames"),
        ])
        post_specs.extend([
            ("/appen/backend/job/internal-worker-candidates", "filter"),
            ("/appen/backend/job/internal-worker-candidates", "emailsOrNames"),
        ])

    errors: list[str] = []
    for value in query_values:
        for path, key in get_specs:
            params = {key: value, "pageIndex": 0, "pageSize": 20}
            ok, payload, error = _request_json(sess, "GET", base_url, path, params=params, timeout=timeout)
            if not ok:
                errors.append(f"GET {path} {key}: {error}")
                continue
            probes.extend(_iter_rows(payload))
        for path, key in post_specs:
            body = {key: value, "pageIndex": 0, "pageSize": 20}
            ok, payload, error = _request_json(sess, "POST", base_url, path, body=body, timeout=timeout)
            if not ok:
                errors.append(f"POST {path} {key}: {error}")
                continue
            probes.extend(_iter_rows(payload))

    wanted = {username.lower(), simple.lower()}
    for row in probes:
        if not isinstance(row, dict):
            continue
        text = _candidate_text(row)
        if any(value and value in text for value in wanted):
            resolved = _ids_from_row(row)
            if not (resolved.get("user_id") or resolved.get("worker_id") or resolved.get("ld_user_id")):
                continue
            resolved["username"] = username
            resolved["display_name"] = simple
            resolved["resolved"] = True
            return resolved

    if local:
        local["username"] = username
        local["display_name"] = simple
        local["resolved"] = bool(local.get("user_id") or local.get("worker_id") or local.get("ld_user_id"))
        local["resolve_errors"] = errors[-8:]
        return local

    return {
        "username": username,
        "display_name": simple,
        "resolved": False,
        "resolve_errors": errors[-8:],
    }


def _job_matches_user(job: dict[str, Any], user: dict[str, Any]) -> bool:
    worker_id = str(user.get("worker_id") or user.get("user_id") or user.get("ld_user_id") or "")
    if worker_id and str(job.get("workerId") or "") == worker_id:
        return True
    text = _candidate_text(job)
    return _short_name(user.get("username") or "").lower() in text or str(user.get("username") or "").lower() in text


def _fetch_worker_jobs_variant(
    *,
    sess: requests.Session,
    base_url: str,
    params: dict[str, Any],
    page_size: int,
    max_pages: int,
    timeout: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    path = "/appen/backend/job/worker-jobs"
    page_index = 0
    jobs: list[dict[str, Any]] = []
    errors: list[str] = []
    while True:
        page_params = dict(params)
        page_params.update({"pageIndex": page_index, "pageSize": page_size, "sortBy": "CONFIRM_TIME"})
        page_params.setdefault("jobStatusList", ["LAUNCH", "RUNNING", "PAUSE"])
        page_params.setdefault("statusList", ["CONFIRMED"])
        page_params.setdefault("jobName", "")
        ok, payload, error = _request_json(
            sess,
            "GET",
            base_url,
            path,
            params=page_params,
            timeout=timeout,
        )
        if not ok:
            errors.append(error)
            break
        rows = _iter_rows(payload)
        jobs.extend(rows)
        total_pages = _extract_total_pages(payload)
        page_index += 1
        if total_pages is not None and page_index >= total_pages:
            break
        if max_pages and page_index >= max_pages:
            break
        if not rows:
            break
    return jobs, errors


def fetch_jobs_for_user(
    *,
    sess: requests.Session,
    user: dict[str, Any],
    base_url: str,
    output_path: Path,
    page_size: int,
    max_pages: int,
    timeout: int,
    force: bool,
    aggressive_lookup: bool,
) -> dict[str, Any]:
    if output_path.exists() and not force:
        try:
            cached = json.loads(output_path.read_text(encoding="utf-8", errors="replace"))
            jobs = cached.get("results") or []
            if jobs:
                return {"ok": True, "path": str(output_path), "jobs": jobs, "source": "cache"}
        except Exception:
            pass

    username = user.get("username") or ""
    short = _short_name(username)
    worker_id = user.get("worker_id") or user.get("user_id") or user.get("ld_user_id")
    variants: list[dict[str, Any]] = []
    if worker_id:
        for key in ("workerId", "userId", "uid"):
            variants.append({key: worker_id})
    job_query_keys = ("workerName", "username", "uniqueName", "name", "filter") if aggressive_lookup else ("workerName", "username")
    for key in job_query_keys:
        variants.append({key: username})
        variants.append({key: short})

    errors: list[str] = []
    seen_keys: set[str] = set()
    timeout_failures = 0
    for variant in variants:
        key = json.dumps(variant, sort_keys=True)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        jobs, variant_errors = _fetch_worker_jobs_variant(
            sess=sess,
            base_url=base_url,
            params=variant,
            page_size=page_size,
            max_pages=max_pages,
            timeout=timeout,
        )
        errors.extend(variant_errors)
        if variant_errors and any("Timeout" in item or "ConnectTimeout" in item or "ReadTimeout" in item for item in variant_errors):
            timeout_failures += 1
            if timeout_failures >= 2 and not aggressive_lookup:
                break
        if not jobs:
            continue
        matching = [job for job in jobs if _job_matches_user(job, user)]
        if matching:
            payload = {
                "source": "multi-user-worker-jobs",
                "fetched_at": datetime.now().isoformat(),
                "username": username,
                "user": _redact_user(user),
                "query_params": variant,
                "totalElements": len(matching),
                "results": matching,
            }
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return {"ok": True, "path": str(output_path), "jobs": matching, "source": "network", "query": variant}

    return {"ok": False, "path": str(output_path), "jobs": [], "errors": errors[-10:]}


def _run(cmd: list[str], title: str, cwd: str) -> dict[str, Any]:
    started = time.time()
    proc = subprocess.run(cmd, cwd=cwd, check=False)
    elapsed = round(time.time() - started, 3)
    return {"title": title, "returncode": proc.returncode, "elapsed_sec": elapsed, "cmd": cmd}


def _infer_worker_id_from_jobs(jobs: list[dict[str, Any]], fallback: Any = "") -> str:
    counts = Counter(str(job.get("workerId")) for job in jobs if job.get("workerId") is not None)
    if counts:
        return counts.most_common(1)[0][0]
    return str(fallback or "")


def _clean_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _issue_row_names(row: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for key in ("issueTypes", "issueType", "type"):
        for item in _clean_list(row.get(key)):
            if item not in names:
                names.append(item)
    return names or ["unknown"]


def _record_key(row: dict[str, Any]) -> str:
    parts = [
        str(row.get("jobId") or row.get("job_id") or ""),
        str(row.get("qaTaskId") or row.get("taskId") or row.get("task_id") or ""),
        str(row.get("recordId") or row.get("record_id") or ""),
    ]
    return "::".join(parts)


def _full_data_totals(qa_payload: dict[str, Any]) -> dict[str, int]:
    path = str(qa_payload.get("input_full_data") or "").strip()
    if not path:
        return {}
    full_path = Path(path)
    if not full_path.exists():
        return {}
    try:
        full_data = json.loads(full_path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    if not isinstance(full_data, dict):
        return {}

    summary = full_data.get("summary") if isinstance(full_data.get("summary"), dict) else {}
    total_data = int(summary.get("total_jobs") or 0)
    total_records = int(summary.get("total_records") or 0)

    if not total_data:
        jobs_payload = full_data.get("jobs_payload") if isinstance(full_data.get("jobs_payload"), dict) else {}
        jobs_payload_results = jobs_payload.get("results")
        if isinstance(jobs_payload_results, list):
            total_data = len([job for job in jobs_payload_results if isinstance(job, dict)])
    if not total_data:
        jobs = full_data.get("jobs")
        if isinstance(jobs, list):
            total_data = len([job for job in jobs if isinstance(job, dict)])
    if not total_data:
        job_results = full_data.get("job_results")
        if isinstance(job_results, list):
            total_data = len([item for item in job_results if isinstance(item, dict)])
    if not total_records:
        job_results = full_data.get("job_results")
        if isinstance(job_results, list):
            total_records = sum(
                len([record for record in ((item.get("task_details") or {}).get("records") or []) if isinstance(record, dict)])
                for item in job_results
                if isinstance(item, dict)
            )
    if not total_records:
        records = full_data.get("records")
        if isinstance(records, list):
            total_records = len([record for record in records if isinstance(record, dict)])
    if (not total_data or not total_records) and full_path.name.startswith("full_data_"):
        sibling_report = full_path.with_name(full_path.name.replace("full_data_", "report_", 1))
        if sibling_report.exists():
            try:
                report_payload = json.loads(sibling_report.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                report_payload = {}
            sibling_summary = report_payload.get("summary") if isinstance(report_payload, dict) and isinstance(report_payload.get("summary"), dict) else {}
            if not total_data:
                total_data = int(sibling_summary.get("total_jobs") or 0)
            if not total_records:
                total_records = int(sibling_summary.get("total_records") or 0)

    return {
        "total_data": max(0, total_data),
        "total_records": max(0, total_records),
    }


def _build_dashboard_report(
    *,
    username: str,
    worker_id: str,
    qa_payload: dict[str, Any],
) -> dict[str, Any]:
    summary = qa_payload.get("summary") if isinstance(qa_payload.get("summary"), dict) else {}
    full_totals = _full_data_totals(qa_payload)
    issue_rows = [row for row in (qa_payload.get("issue_rows") or []) if isinstance(row, dict)]
    reviewed_records = int(summary.get("candidate_records") or summary.get("records_with_reviews") or 0)
    total_records = int(full_totals.get("total_records") or reviewed_records or 0)
    records_with_error = len({_record_key(row) for row in issue_rows}) if issue_rows else 0

    grouped: dict[str, dict[str, Any]] = {}
    top: dict[str, dict[str, Any]] = defaultdict(lambda: {"record_keys": set(), "total_severity": 0, "comments": []})

    for row in issue_rows:
        key = _record_key(row)
        names = _issue_row_names(row)
        comment = str(row.get("comment") or "").strip()
        job_id = str(row.get("jobId") or row.get("job_id") or "")
        task_id = str(row.get("qaTaskId") or row.get("taskId") or row.get("task_id") or row.get("recordId") or "")
        hash_id = str(row.get("qaAnnotationKey") or row.get("jobDisplayId") or job_id or "qa")
        if "/" in hash_id:
            hash_id = hash_id.split("/", 1)[0]

        rec = grouped.setdefault(
            key,
            {
                "task_id": task_id,
                "job_id": job_id,
                "record_id": row.get("recordId"),
                "job_display_id": row.get("jobDisplayId"),
                "job_name": row.get("jobName"),
                "project_id": row.get("projectId"),
                "project_name": row.get("projectName"),
                "worker_id": row.get("workerId") or worker_id,
                "status": "REJECTED",
                "qa_review_count": 0,
                "rounds": [],
                "errors": {},
                "error_comments": {},
                "history_issue_comments": [],
                "qa_comments": [],
                "total_loi": 0,
                "total_severity": 0,
                "_round_ids": set(),
                "_hash": hash_id,
            },
        )
        review_id = str(row.get("qaReviewId") or row.get("qaTaskId") or "")
        if review_id and review_id not in rec["_round_ids"]:
            rec["_round_ids"].add(review_id)
            rec["qa_review_count"] += 1
            rec["rounds"].append(
                {
                    "round": rec["qa_review_count"],
                    "type": row.get("qaMode") or "",
                    "qa_user": row.get("qaWorkerId") or "",
                    "date": row.get("lastModifiedTime") or row.get("labelingTime") or "",
                    "qa_review_id": review_id,
                }
            )

        for name in names:
            severity = int(row.get("pointCount") or 1)
            rec["errors"][name] = rec["errors"].get(name, 0) + severity
            if comment:
                rec["error_comments"].setdefault(name, [])
                if comment not in rec["error_comments"][name]:
                    rec["error_comments"][name].append(comment)
                if comment not in rec["qa_comments"]:
                    rec["qa_comments"].append(comment)
            rec["history_issue_comments"].append({"issue_type": name, "comment": comment})
            top[name]["record_keys"].add(key)
            top[name]["total_severity"] += severity
            if comment and comment not in top[name]["comments"]:
                top[name]["comments"].append(comment)

    records: list[dict[str, Any]] = []
    for rec in grouped.values():
        rec["total_loi"] = len(rec.get("errors") or {})
        rec["total_severity"] = sum((rec.get("errors") or {}).values())
        rec["history_issue_comments"] = rec["history_issue_comments"][:50]
        rec["qa_comments"] = rec["qa_comments"][:20]
        hash_id = rec.pop("_hash", "qa")
        rec.pop("_round_ids", None)
        records.append(rec)

    top_errors = [
        {
            "name": name,
            "records": len(data["record_keys"]),
            "total_severity": data["total_severity"],
            "comments": data["comments"][:5],
        }
        for name, data in top.items()
    ]
    top_errors.sort(key=lambda item: (-item["records"], -item["total_severity"], item["name"]))

    if not total_records:
        total_records = records_with_error
    total_data = int(full_totals.get("total_data") or len({str(r.get("job_id") or "") for r in records if r.get("job_id")}) or 0)
    records_passed = max(0, total_records - records_with_error)
    accuracy = round((records_passed / total_records * 100) if total_records else 0.0, 1)
    avg_qa_returns = round(sum(r.get("qa_review_count", 0) for r in records) / len(records), 2) if records else 0

    return {
        "source": "optimized-qa-review-issues",
        "generated_at": datetime.now().isoformat(),
        "target_user_id": worker_id,
        "username": username,
        "summary": {
            "total_data": total_data,
            "total_records": total_records,
            "reviewed_records": reviewed_records,
            "records_with_error": records_with_error,
            "records_passed": records_passed,
            "accuracy_pct": accuracy,
            "avg_qa_returns": avg_qa_returns,
            "issue_rows": len(issue_rows),
            "error_count": len(issue_rows),
        },
        "top_errors": top_errors[:15],
        "errors": records,
        "issue_records": records,
        "records": records,
        "new_records_this_run": records_with_error,
    }


def _write_dashboard_report(
    *,
    username: str,
    worker_id: str,
    qa_json_path: Path,
    report_dir: str,
    account_dir: str,
) -> dict[str, Any]:
    qa_payload = json.loads(qa_json_path.read_text(encoding="utf-8", errors="replace"))
    report = _build_dashboard_report(username=username, worker_id=worker_id, qa_payload=qa_payload)
    report_root = Path(report_dir)
    report_root.mkdir(parents=True, exist_ok=True)
    report_path = report_root / f"{username}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    account_root = Path(account_dir)
    account_root.mkdir(parents=True, exist_ok=True)
    account_path = account_root / f"{username}.json"
    account: dict[str, Any] = {}
    if account_path.exists():
        try:
            loaded = json.loads(account_path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(loaded, dict):
                account = loaded
        except Exception:
            account = {}
    account.update(
        {
            "username": username,
            "display_name": _short_name(username),
            "user_id": worker_id or account.get("user_id"),
            "worker_id": worker_id or account.get("worker_id"),
            "status": "idle",
            "last_scan": datetime.now().isoformat(),
        }
    )
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    top_errors = list(report.get("top_errors") or [])
    account.update(
        {
            "total_data": summary.get("total_data", 0),
            "total_records": summary.get("total_records", 0),
            "records_with_error": summary.get("records_with_error", 0),
            "records_passed": summary.get("records_passed", 0),
            "accuracy_pct": summary.get("accuracy_pct", 0),
            "error_count": summary.get("error_count", summary.get("issue_rows", 0)),
            "top_errors": top_errors[:3],
            "report_summary": summary,
            "report_generated_at": report.get("generated_at"),
            "report_file": str(report_path),
        }
    )
    if account.get("scanner_cookie") and "data\\accounts" in str(account.get("scanner_cookie_source") or ""):
        account["scanner_cookie_source"] = str(account_path)
    if "data\\accounts" in str(account.get("identity_source") or ""):
        account["identity_source"] = str(account_path)
    account.setdefault("hashes", [])
    account.setdefault("processed_qa_keys", [])
    account_path.write_text(json.dumps(account, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "report": str(report_path),
        "summary": summary,
        "top_error_count": len(report.get("top_errors") or []),
    }


def _save_account_identity(
    username: str,
    user: dict[str, Any],
    account_dir: str,
    *,
    cookie: str = "",
    cookie_source: str = "",
    cookie_valid: bool | None = None,
    identity_status: str = "",
    hashes: list[str] | None = None,
    last_scan: bool = False,
) -> None:
    account_root = Path(account_dir)
    account_root.mkdir(parents=True, exist_ok=True)
    path = account_root / f"{username}.json"
    account: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(loaded, dict):
                account = loaded
        except Exception:
            account = {}
    account.update(
        {
            "username": username,
            "display_name": _short_name(username),
            "user_id": str(user.get("user_id") or user.get("worker_id") or ""),
            "worker_id": str(user.get("worker_id") or user.get("user_id") or ""),
            "identity_source": user.get("identity_source") or user.get("source") or "",
            "status": account.get("status") or "idle",
        }
    )
    if cookie:
        account["scanner_cookie"] = cookie
        account["scanner_cookie_source"] = cookie_source or account.get("scanner_cookie_source") or "runtime"
        account["scanner_cookie_updated_at"] = datetime.now().isoformat()
        session_value = _session_from_cookie(cookie)
        if session_value:
            account["scanner_session"] = session_value
            account["session"] = session_value
            account["scanner_session_updated_at"] = datetime.now().isoformat()
    if cookie_valid is not None:
        account["scanner_cookie_valid"] = bool(cookie_valid)
        account["scanner_cookie_last_checked_at"] = datetime.now().isoformat()
    if identity_status:
        account["identity_status"] = identity_status
    account["hashes"] = _merge_hashes(account.get("hashes"), hashes)
    account.setdefault("processed_qa_keys", [])
    if last_scan:
        account["last_scan"] = datetime.now().isoformat()
    else:
        account.setdefault("last_scan", None)
    path.write_text(json.dumps(account, ensure_ascii=False, indent=2), encoding="utf-8")


def run_one_user(
    *,
    username: str,
    args: argparse.Namespace,
    run_dir: Path,
    default_cookie: str,
) -> dict[str, Any]:
    normalized = _normalize_username(username)
    user_dir = run_dir / _safe_name(normalized)
    user_dir.mkdir(parents=True, exist_ok=True)
    base_url = _normalize_base_url(args.base_url)
    cookie, cookie_source = _cookie_for_user(args, normalized, default_cookie)
    if not cookie:
        return {
            "username": normalized,
            "ok": False,
            "error": "No per-user cookie available. Store scanner_cookie in account JSON or add a matching file in --cookie-dir.",
            "finished_at": datetime.now().isoformat(),
        }
    sess = _make_session(cookie, args.host_header)

    result: dict[str, Any] = {
        "username": normalized,
        "started_at": datetime.now().isoformat(),
        "user_dir": str(user_dir),
        "cookie_source": cookie_source,
    }

    identity_probe = _probe_cookie_for_user(
        cookie=cookie,
        username=normalized,
        base_url=base_url,
        host_header=args.host_header,
        timeout=args.timeout,
        accept_empty=False,
    )
    result["cookie_identity"] = identity_probe
    if not identity_probe.get("match"):
        result["ok"] = False
        result["error"] = "Cookie is valid for a different user or cannot be verified for this username."
        result["finished_at"] = datetime.now().isoformat()
        return result

    user = resolve_user(
        sess=sess,
        username=normalized,
        base_url=base_url,
        timeout=args.timeout,
        skip_network=args.skip_user_lookup,
        aggressive_lookup=args.aggressive_user_lookup,
    )
    result["resolved_user"] = _redact_user(user)
    (user_dir / "user.json").write_text(json.dumps(_redact_user(user), ensure_ascii=False, indent=2), encoding="utf-8")

    jobs_path = user_dir / f"ld_jobs_{_safe_name(normalized)}.json"
    jobs_result = fetch_jobs_for_user(
        sess=sess,
        user=user,
        base_url=base_url,
        output_path=jobs_path,
        page_size=args.job_page_size,
        max_pages=args.job_max_pages,
        timeout=args.timeout,
        force=args.force,
        aggressive_lookup=args.aggressive_job_lookup,
    )
    result["jobs_result"] = {k: v for k, v in jobs_result.items() if k != "jobs"}
    jobs = jobs_result.get("jobs") or []
    result["job_count"] = len(jobs)
    if not jobs:
        result["ok"] = False
        result["error"] = "No jobs found for user. Need admin-visible user/job endpoint or a valid jobs cache."
        result["finished_at"] = datetime.now().isoformat()
        return result

    worker_id = _infer_worker_id_from_jobs(jobs, user.get("worker_id") or user.get("user_id") or user.get("ld_user_id"))
    if not worker_id:
        result["ok"] = False
        result["error"] = "User identity resolution failed: missing worker_id after user lookup and job metadata lookup."
        result["finished_at"] = datetime.now().isoformat()
        return result
    user["worker_id"] = worker_id
    if not user.get("user_id"):
        user["user_id"] = worker_id
        user["identity_source"] = "job-metadata"
    else:
        user["identity_source"] = user.get("source", "user-lookup")
    result["resolved_user"] = _redact_user(user)
    (user_dir / "user.json").write_text(json.dumps(_redact_user(user), ensure_ascii=False, indent=2), encoding="utf-8")
    _save_account_identity(
        normalized,
        user,
        args.dashboard_account_dir,
        cookie=cookie,
        cookie_source=cookie_source,
        cookie_valid=True,
        identity_status="ready",
    )
    full_data_path = user_dir / f"full_data_{worker_id or _safe_name(normalized)}.json"
    report_path = user_dir / f"report_{worker_id or _safe_name(normalized)}.json"
    qa_prefix = user_dir / f"qa_review_issues_{worker_id or _safe_name(normalized)}"

    if args.dry_run:
        result["ok"] = True
        result["dry_run"] = True
        result["planned_outputs"] = {
            "jobs": str(jobs_path),
            "full_data": str(full_data_path),
            "report": str(report_path),
            "qa_json": str(qa_prefix.with_suffix(".json")),
            "qa_csv": str(qa_prefix.with_suffix(".csv")),
        }
        result["finished_at"] = datetime.now().isoformat()
        return result

    python = sys.executable
    full_cmd = [
        python,
        str(_script_dir() / "fetch_current_full_data.py"),
        "--cookie-file",
        cookie_source,
        "--jobs-file",
        str(jobs_path),
        "--base-url",
        base_url,
        "--host-header",
        args.host_header,
        "--user-id",
        worker_id,
        "--page-size",
        str(args.task_page_size),
        "--timeout",
        str(args.timeout),
        "--workers",
        str(args.job_workers),
        "--output",
        str(full_data_path),
        "--report-output",
        str(report_path),
    ]
    if args.max_pages_per_job:
        full_cmd += ["--max-pages-per-job", str(args.max_pages_per_job)]
    if args.records_only:
        full_cmd.append("--records-only")
    full_step = _run(full_cmd, "full-data", str(Path.cwd()))
    result["full_data_step"] = full_step
    if full_step["returncode"] != 0:
        result["ok"] = False
        result["error"] = "full-data step failed"
        result["finished_at"] = datetime.now().isoformat()
        return result

    qa_cmd = [
        python,
        str(_script_dir() / "fetch_qa_review_issues.py"),
        "--full-data",
        str(full_data_path),
        "--cookie-file",
        cookie_source,
        "--base-url",
        base_url,
        "--host-header",
        args.host_header,
        "--output-prefix",
        str(qa_prefix),
        "--workers",
        str(args.record_workers),
        "--timeout",
        str(args.timeout),
    ]
    if args.all_records:
        qa_cmd.append("--all-records")
    if getattr(args, "max_records", 0):
        qa_cmd += ["--max-records", str(args.max_records)]
    if args.issue_rows_only:
        qa_cmd.append("--issue-rows-only")
    if args.no_fetch_statistics:
        qa_cmd.append("--no-fetch-statistics")
    qa_step = _run(qa_cmd, "qa-review-issues", str(Path.cwd()))
    result["qa_step"] = qa_step
    result["ok"] = qa_step["returncode"] == 0
    result["outputs"] = {
        "jobs": str(jobs_path),
        "full_data": str(full_data_path),
        "report": str(report_path),
        "qa_json": str(qa_prefix.with_suffix(".json")),
        "qa_csv": str(qa_prefix.with_suffix(".csv")),
    }
    if result["ok"]:
        derived_hashes: list[str] = []
        qa_json_path = qa_prefix.with_suffix(".json")
        try:
            qa_payload = json.loads(qa_json_path.read_text(encoding="utf-8", errors="replace"))
            derived_hashes = _extract_hashes_from_qa_payload(qa_payload)
        except Exception:
            derived_hashes = []
        _save_account_identity(
            normalized,
            user,
            args.dashboard_account_dir,
            cookie=cookie,
            cookie_source=cookie_source,
            cookie_valid=True,
            identity_status="ready",
            hashes=derived_hashes,
            last_scan=True,
        )
        result["derived_hash_count"] = len(derived_hashes)
        if derived_hashes:
            result["derived_hash_sample"] = derived_hashes[:10]
    if result["ok"] and args.dashboard_report_dir:
        try:
            result["dashboard_report"] = _write_dashboard_report(
                username=normalized,
                worker_id=worker_id,
                qa_json_path=qa_prefix.with_suffix(".json"),
                report_dir=args.dashboard_report_dir,
                account_dir=args.dashboard_account_dir,
            )
        except Exception as exc:
            result["ok"] = False
            result["error"] = f"dashboard report step failed: {type(exc).__name__}: {exc}"
    result["finished_at"] = datetime.now().isoformat()
    return result


def _performance_note(args: argparse.Namespace, user_count: int) -> dict[str, Any]:
    sequential_current = BASELINE_QA_SEC_PER_RECORD * BASELINE_CURRENT_RECORDS
    estimated_parallel = sequential_current / max(1, args.record_workers)
    return {
        "baseline_from_current_user": {
            "records": BASELINE_CURRENT_RECORDS,
            "qa_review_scan_sec": round(sequential_current, 1),
            "sec_per_record": round(BASELINE_QA_SEC_PER_RECORD, 3),
        },
        "configured_parallelism": {
            "user_workers": args.user_workers,
            "job_workers_per_user": args.job_workers,
            "record_workers_per_user": args.record_workers,
            "max_record_requests_in_flight": args.user_workers * args.record_workers,
            "max_job_requests_in_flight": args.user_workers * args.job_workers,
            "records_only_inventory": args.records_only,
            "issue_rows_only_output": args.issue_rows_only,
            "fetch_statistics": not args.no_fetch_statistics,
        },
        "rough_estimate_for_439_record_user": {
            "qa_review_scan_sec_at_record_workers": round(estimated_parallel * 1.25, 1),
            "note": "1.25x includes scheduling/retry overhead; real time depends on LD latency and throttling.",
        },
        "user_count": user_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve usernames and run optimized multi-user QA scans.")
    parser.add_argument("usernames", nargs="*", help="Usernames, e.g. hoangtrungmanh nguyennangnguyen")
    parser.add_argument("--user", action="append", default=[], help="Repeatable username option")
    parser.add_argument("--cookie-file", default=str(_script_dir() / "cookie.txt"))
    parser.add_argument("--cookie-dir", default="", help="Optional directory with per-user cookies, e.g. jr-name-ty.txt.")
    parser.add_argument("--allow-shared-cookie", action="store_true", help="Allow --cookie-file as a shared fallback. Default requires a per-user cookie.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--host-header", default=DEFAULT_HOST_HEADER)
    parser.add_argument("--out-dir", default=str(_script_dir() / "multi_user_runs"))
    parser.add_argument("--run-name", default="")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--user-workers", type=int, default=2)
    parser.add_argument("--job-workers", type=int, default=4)
    parser.add_argument("--record-workers", type=int, default=8)
    parser.add_argument("--job-page-size", type=int, default=200)
    parser.add_argument("--job-max-pages", type=int, default=0)
    parser.add_argument("--task-page-size", type=int, default=200)
    parser.add_argument("--max-pages-per-job", type=int, default=0)
    parser.add_argument("--max-records", type=int, default=0, help="Limit QA review record fetches for lightweight smoke scans.")
    parser.add_argument("--records-only", action="store_true", default=True)
    parser.add_argument("--with-job-summaries", dest="records_only", action="store_false")
    parser.add_argument("--all-records", action="store_true", default=True)
    parser.add_argument("--candidate-records-only", dest="all_records", action="store_false")
    parser.add_argument("--issue-rows-only", action="store_true", default=True)
    parser.add_argument("--keep-review-records", dest="issue_rows_only", action="store_false")
    parser.add_argument("--no-fetch-statistics", action="store_true", default=True)
    parser.add_argument("--fetch-statistics", dest="no_fetch_statistics", action="store_false")
    parser.add_argument("--force", action="store_true", help="Ignore cached jobs file")
    parser.add_argument("--skip-user-lookup", action="store_true", help="Debug only: use local account metadata without probing user directory endpoints.")
    parser.add_argument("--aggressive-user-lookup", action="store_true", help="Try broader user directory query parameter variants.")
    parser.add_argument("--aggressive-job-lookup", action="store_true", help="Try broader job query parameter variants.")
    parser.add_argument("--plan-only", action="store_true", help="Only print/write concurrency plan; no network calls")
    parser.add_argument("--dry-run", action="store_true", help="Resolve/fetch jobs only; do not run scan subprocesses")
    parser.add_argument("--dashboard-report-dir", default=str(data_dir() / "report"), help="data/report directory to write filtered QA error reports.")
    parser.add_argument("--dashboard-account-dir", default=str(data_dir() / "user"), help="data/user directory to update user scanner summaries.")
    args = parser.parse_args()

    usernames = [_normalize_username(u) for u in [*args.usernames, *args.user] if u.strip()]
    usernames = list(dict.fromkeys(usernames))
    if not usernames:
        print("No usernames provided.")
        return 1

    run_name = args.run_name or _stamp()
    run_dir = Path(args.out_dir) / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir / "manifest.json"

    manifest: dict[str, Any] = {
        "source": "optimized-multi-user-scan",
        "started_at": datetime.now().isoformat(),
        "run_dir": str(run_dir),
        "usernames": usernames,
        "performance": _performance_note(args, len(usernames)),
        "results": [],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(manifest["performance"], ensure_ascii=False, indent=2))
    print(f"Run dir: {run_dir}")

    if args.plan_only:
        manifest["results"] = [
            {
                "username": username,
                "ok": True,
                "plan_only": True,
                "user_dir": str(run_dir / _safe_name(username)),
            }
            for username in usernames
        ]
        manifest["finished_at"] = datetime.now().isoformat()
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Plan only. Manifest: {manifest_path}")
        return 0

    default_cookie = _load_optional_cookie(args.cookie_file)

    with ThreadPoolExecutor(max_workers=max(1, args.user_workers)) as executor:
        futures = {
            executor.submit(run_one_user, username=username, args=args, run_dir=run_dir, default_cookie=default_cookie): username
            for username in usernames
        }
        for future in as_completed(futures):
            username = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {
                    "username": username,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "finished_at": datetime.now().isoformat(),
                }
            manifest["results"].append(result)
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"{username}: {'ok' if result.get('ok') else 'failed'}")

    manifest["finished_at"] = datetime.now().isoformat()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    ok_count = sum(1 for item in manifest["results"] if item.get("ok"))
    print(f"Done: {ok_count}/{len(usernames)} users ok")
    print(f"Manifest: {manifest_path}")
    return 0 if ok_count == len(usernames) else 2


if __name__ == "__main__":
    raise SystemExit(main())
