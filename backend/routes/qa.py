"""
QA Tracker route — integrated into LD backend.
Prefix: /api/qa

Architecture:
  1. Dashboard -> POST /api/qa/run_scanner
  2. Backend runs services/qa_scanner as the scanner runtime
  3. User summary is stored in data/user/<username>.json
  4. Filtered QA error report is stored in data/report/<username>.json
  5. Extension-style endpoints are legacy compatibility only
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import traceback
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from core.auth import get_current_user, require_admin
from core.config import settings
from core.ld_identity import normalize_labeler_username

log = logging.getLogger("ld.qa")
router = APIRouter(prefix="/api/qa", tags=["QA Tracker"])
QA_ROUTE_VERSION = "2026-05-24.ingest-debug-v8-history-driven-issue-report"

# ── Storage paths ──────────────────────────────────────────
# Docker layout: /app/data/...
# Local layout:  <repo>/LD/data/...
_BASE_DIR = Path(__file__).resolve().parents[1]  # backend dir in local, /app in docker
_DATA_DIR = _BASE_DIR / "data" if (_BASE_DIR / "data").exists() else _BASE_DIR.parent / "data"
USER_DIR = Path(os.getenv("QA_USER_DIR", str(_DATA_DIR / "user")))
REPORT_DIR = Path(os.getenv("QA_REPORT_DIR", str(_DATA_DIR / "report")))
ACCOUNTS_DIR = USER_DIR
REPORTS_DIR = REPORT_DIR
LEGACY_ACCOUNTS_DIR = _DATA_DIR / "accounts"
LEGACY_REPORTS_DIR = _DATA_DIR / "reports"
ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def _copy_legacy_json_files(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    for legacy in sorted(src.glob("*.json")):
        if legacy.name.endswith(".state.json"):
            continue
        target = dst / legacy.name
        if target.exists():
            continue
        try:
            target.write_text(legacy.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
        except Exception as exc:
            log.warning("Failed migrating legacy QA JSON %s -> %s: %s", legacy, target, exc)


def _normalize_user_storage_sources() -> None:
    for user_file in sorted(ACCOUNTS_DIR.glob("*.json")):
        if user_file.name.endswith(".state.json"):
            continue
        try:
            user = json.loads(user_file.read_text(encoding="utf-8", errors="replace"))
        except Exception as exc:
            log.warning("Failed reading QA user source metadata %s: %s", user_file, exc)
            continue
        if not isinstance(user, dict):
            continue
        changed = False
        defaults = {
            "total_data": 0,
            "total_records": 0,
            "records_with_error": 0,
            "records_passed": 0,
            "accuracy_pct": 0,
            "error_count": 0,
            "top_errors": [],
            "report_summary": {},
        }
        for key, value in defaults.items():
            if key not in user:
                user[key] = value
                changed = True
        for key in ("scanner_cookie_source", "identity_source"):
            value = str(user.get(key) or "")
            if "data\\accounts" in value or "data/accounts" in value:
                user[key] = str(user_file)
                changed = True
        if changed:
            user_file.write_text(json.dumps(user, ensure_ascii=False, indent=2), encoding="utf-8")


def _sync_user_summaries_from_reports() -> None:
    for report_file in sorted(REPORTS_DIR.glob("*.json")):
        if report_file.name.endswith(".state.json"):
            continue
        try:
            report = json.loads(report_file.read_text(encoding="utf-8", errors="replace"))
        except Exception as exc:
            log.warning("Failed reading QA report summary %s: %s", report_file, exc)
            continue
        if not isinstance(report, dict):
            continue
        username = str(report.get("username") or report_file.stem).strip()
        if not username:
            continue
        summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
        top_errors = list(report.get("top_errors") or [])
        user_path = ACCOUNTS_DIR / f"{username}.json"
        user: dict[str, Any] = {}
        if user_path.exists():
            try:
                loaded = json.loads(user_path.read_text(encoding="utf-8", errors="replace"))
                if isinstance(loaded, dict):
                    user = loaded
            except Exception:
                user = {}
        target_user_id = str(report.get("target_user_id") or "")
        user.update(
            {
                "username": username,
                "display_name": user.get("display_name") or username.removeprefix("jr-").removesuffix("-ty"),
                "user_id": target_user_id or str(user.get("user_id") or ""),
                "worker_id": target_user_id or str(user.get("worker_id") or ""),
                "total_data": summary.get("total_data", user.get("total_data", 0)),
                "total_records": summary.get("total_records", user.get("total_records", 0)),
                "records_with_error": summary.get("records_with_error", user.get("records_with_error", 0)),
                "records_passed": summary.get("records_passed", user.get("records_passed", 0)),
                "accuracy_pct": summary.get("accuracy_pct", user.get("accuracy_pct", 0)),
                "error_count": summary.get("error_count", summary.get("issue_rows", user.get("error_count", 0))),
                "top_errors": top_errors[:3],
                "report_summary": summary,
                "report_generated_at": report.get("generated_at"),
                "report_file": str(report_file),
                "status": user.get("status") or "idle",
            }
        )
        if user.get("scanner_cookie") and "data\\accounts" in str(user.get("scanner_cookie_source") or ""):
            user["scanner_cookie_source"] = str(user_path)
        if "data\\accounts" in str(user.get("identity_source") or ""):
            user["identity_source"] = str(user_path)
        user.setdefault("hashes", [])
        user.setdefault("processed_qa_keys", [])
        user_path.write_text(json.dumps(user, ensure_ascii=False, indent=2), encoding="utf-8")


def _legacy_record_has_issue(record: dict[str, Any]) -> bool:
    return (
        bool(record.get("errors"))
        or bool(record.get("error_comments"))
        or bool(record.get("history_issue_comments"))
        or bool(record.get("qa_comments"))
        or int(record.get("total_loi") or 0) > 0
    )


def _normalize_existing_reports() -> None:
    for report_file in sorted(REPORTS_DIR.glob("*.json")):
        if report_file.name.endswith(".state.json"):
            continue
        try:
            report = json.loads(report_file.read_text(encoding="utf-8", errors="replace"))
        except Exception as exc:
            log.warning("Failed normalizing QA report %s: %s", report_file, exc)
            continue
        if not isinstance(report, dict) or "datasets" not in report:
            continue

        issue_records = [r for r in (report.get("issue_records") or []) if isinstance(r, dict)]
        if not issue_records:
            issue_records = [r for r in (report.get("records") or []) if isinstance(r, dict) and _legacy_record_has_issue(r)]
        if not issue_records:
            for ds in report.get("datasets") or []:
                if not isinstance(ds, dict):
                    continue
                issue_records.extend(
                    r for r in (ds.get("records") or [])
                    if isinstance(r, dict) and _legacy_record_has_issue(r)
                )

        summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
        summary["error_count"] = summary.get("error_count", summary.get("issue_rows", len(issue_records)))
        normalized = {
            "source": report.get("source", "normalized-qa-error-report"),
            "generated_at": report.get("generated_at"),
            "target_user_id": report.get("target_user_id"),
            "username": report.get("username") or report_file.stem,
            "summary": summary,
            "top_errors": report.get("top_errors", []),
            "errors": issue_records,
            "issue_records": issue_records,
            "records": issue_records,
            "new_records_this_run": report.get("new_records_this_run", len(issue_records)),
        }
        report_file.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")


_copy_legacy_json_files(LEGACY_ACCOUNTS_DIR, ACCOUNTS_DIR)
_copy_legacy_json_files(LEGACY_REPORTS_DIR, REPORTS_DIR)
_normalize_user_storage_sources()
_normalize_existing_reports()
_sync_user_summaries_from_reports()

# ── In-memory scan job store ───────────────────────────────
# { job_id: { "status": "pending"|"running"|"done"|"error",
#             "accounts": [...username],
#             "progress": {username: {"pct":0,"text":"","done":bool}} } }
_scan_jobs: Dict[str, Dict] = {}

# ── Credential convention ──────────────────────────────────
DEFAULT_PASSWORD = settings.APPEN_SHARED_PASSWORD

def make_creds(username: str) -> dict:
    normalized, name = normalize_labeler_username(username)
    return {"username": normalized, "password": DEFAULT_PASSWORD, "display_name": name}

# ── Account helpers ────────────────────────────────────────
def account_path(username: str) -> Path:
    return ACCOUNTS_DIR / f"{username}.json"

def report_path(username: str) -> Path:
    return REPORTS_DIR / f"{username}.json"

def report_state_path(username: str) -> Path:
    return report_path(username)


def _load_json_or_default(path: Path, default: Any, label: str) -> Any:
    """
    Defensive JSON loader:
    - missing file -> default
    - empty/invalid JSON -> default (and log warning)
    """
    if not path.exists():
        return default
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception as e:
        log.warning("Failed reading %s file %s: %s", label, path, e)
        return default

    if not raw.strip():
        log.warning("Empty %s file: %s", label, path)
        return default

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        log.warning("Invalid JSON in %s file %s: %s", label, path, e)
        return default

def load_account(username: str) -> dict | None:
    p = account_path(username)
    data = _load_json_or_default(p, None, "account")
    if not isinstance(data, dict):
        return None
    # Ensure required fields always present
    data.setdefault("processed_qa_keys", [])
    data.setdefault("hashes", [])
    data.setdefault("status", "idle")
    return data

def save_account(data: dict):
    account_path(data["username"]).write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

def list_accounts() -> list[dict]:
    out: list[dict] = []
    for p in sorted(ACCOUNTS_DIR.glob("*.json")):
        data = _load_json_or_default(p, None, "account")
        if not isinstance(data, dict):
            continue
        data.setdefault("processed_qa_keys", [])
        data.setdefault("hashes", [])
        data.setdefault("status", "idle")
        out.append(data)
    return out


SENSITIVE_ACCOUNT_KEYS = {"scanner_cookie", "cookie", "scanner_session", "session"}


def public_account(data: dict) -> dict:
    """Account shape safe for API/UI responses."""
    out = {k: v for k, v in data.items() if k not in SENSITIVE_ACCOUNT_KEYS}
    cookie = str(data.get("scanner_cookie") or data.get("cookie") or "").strip()
    session_value = str(data.get("scanner_session") or data.get("session") or "").strip()
    out["has_scanner_cookie"] = bool(cookie)
    if cookie:
        out["scanner_cookie_length"] = len(cookie)
    out["has_scanner_session"] = bool(session_value)
    if session_value:
        out["scanner_session_length"] = len(session_value)
    return out

def load_report(username: str) -> dict:
    p = report_path(username)
    data = _load_json_or_default(p, {}, "report")
    return data if isinstance(data, dict) else {}

def load_report_state(username: str) -> dict:
    p = report_state_path(username)
    data = _load_json_or_default(p, None, "report state")
    if isinstance(data, dict):
        return data
    return load_report(username)

def save_report(username: str, report: dict):
    report_path(username).write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

def save_report_state(username: str, report: dict):
    report_state_path(username).write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

def blank_account(username: str, display_name: str = "") -> dict:
    return {
        "username": username,
        "display_name": display_name or username.removeprefix("jr-").removesuffix("-ty"),
        "user_id": None,
        "hashes": [],
        "processed_qa_keys": [],  # dedup: QA file keys already processed
        "last_scan": None,
        "status": "idle",
    }

# ── QA Aggregation ─────────────────────────────────────────
STATS_REVIEW_RE = re.compile(
    r"statistics\.review\.(\d+)\.(\d+)\.(\d+)_([^.]+)\.(\d+)"
    r"\.(LABELING|REWORK|QA_RW|QA_RO)\.(\d+)\.json$"
)
STATS_LEGACY_RE = re.compile(
    r"(\d+)\.(\d+)\.(\d+)_([^.]+)\.(\d+)"
    r"\.(LABELING|REWORK|QA_RW|QA_RO)\.(\d+)\.statistics\.json$"
)
FRAME_RE = re.compile(r"khung hình|帧属性", re.IGNORECASE)
REVIEW_TYPES = ("QA_RW", "QA_RO", "REWORK")
COMMENT_KEY_RE = re.compile(
    r"(comment|remark|feedback|reason|reject|note|message|desc)",
    re.IGNORECASE,
)
PRIMARY_COMMENT_KEYS = (
    "reviewComment", "review_comment",
    "qaComment", "qa_comment",
    "comment", "remarks", "remark",
    "feedback", "message", "note",
    "rejectReason", "reject_reason", "reason",
)

def parse_key(key: str) -> dict | None:
    slash = key.find("/")
    if slash < 0:
        return None
    hash_id, fname = key[:slash], key[slash + 1:]
    m = STATS_REVIEW_RE.match(fname) or STATS_LEGACY_RE.match(fname)
    if not m:
        return None
    ts, job_id, task_id, date_str, rnd, ftype, user_id = m.groups()
    return dict(
        key=key, hash=hash_id,
        timestamp=int(ts), job_id=job_id,
        task_id=task_id, date=date_str,
        round=int(rnd), type=ftype, user_id=user_id,
    )


def _normalize_comment_text(v: object) -> str:
    if not isinstance(v, str):
        return ""
    txt = re.sub(r"\s+", " ", v).strip()
    if not txt:
        return ""
    lowered = txt.lower()
    if lowered in {"null", "none", "nan", "n/a", "na"}:
        return ""
    return txt[:500]


def _dedup_texts(values: list[str], max_items: int = 10) -> list[str]:
    out = []
    seen = set()
    for v in values:
        t = _normalize_comment_text(v)
        if not t:
            continue
        k = t.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
        if len(out) >= max_items:
            break
    return out


def _collect_strings(node: object, depth: int = 0, max_depth: int = 3) -> list[str]:
    if depth > max_depth or node is None:
        return []
    if isinstance(node, str):
        t = _normalize_comment_text(node)
        return [t] if t else []
    if isinstance(node, dict):
        out = []
        for v in node.values():
            out.extend(_collect_strings(v, depth + 1, max_depth))
        return out
    if isinstance(node, list):
        out = []
        for item in node[:50]:
            out.extend(_collect_strings(item, depth + 1, max_depth))
        return out
    return []


def _collect_comments(node: object, depth: int = 0, max_depth: int = 5) -> list[str]:
    if depth > max_depth or node is None:
        return []
    out: list[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            ks = str(k)
            if COMMENT_KEY_RE.search(ks):
                out.extend(_collect_strings(v, 0, 3))
            if isinstance(v, (dict, list)):
                out.extend(_collect_comments(v, depth + 1, max_depth))
    elif isinstance(node, list):
        for item in node[:50]:
            out.extend(_collect_comments(item, depth + 1, max_depth))
    return out


def _primary_qa_comment(stats: dict) -> str:
    if not isinstance(stats, dict):
        return ""
    by_lower = {str(k).lower(): v for k, v in stats.items()}
    for key in PRIMARY_COMMENT_KEYS:
        v = by_lower.get(key.lower())
        if v is None:
            continue
        comments = _dedup_texts(_collect_strings(v, 0, 3), max_items=1)
        if comments:
            return comments[0]
    return ""


def parse_issues(stats: dict) -> dict:
    annot, frame = [], []
    all_issue_comments: list[str] = []
    for name, val in (stats.get("issues") or {}).items():
        if isinstance(val, (int, float)):
            count = int(val)
        elif isinstance(val, dict):
            if "count" in val:
                count = int(val["count"])
            else:
                count = sum(int(v) for v in val.values() if isinstance(v, (int, float)))
        else:
            count = 0
        if count == 0:
            continue
        clean = re.sub(r"[\u4e00-\u9fff]+", "", name).strip() or name.strip()
        issue_comments = _dedup_texts(_collect_comments(val), max_items=5)
        all_issue_comments.extend(issue_comments)
        issue_obj = {"name": clean, "issue_type": clean, "severity": count}
        if issue_comments:
            issue_obj["comment"] = issue_comments[0]
            issue_obj["comments"] = issue_comments
        (frame if FRAME_RE.search(name) else annot).append(issue_obj)
    frames_block = stats.get("frames") or {}
    rejected = int(frames_block.get("rejected", 0)) > 0
    qa_comments = _dedup_texts(_collect_comments(stats), max_items=20)
    primary_comment = _primary_qa_comment(stats)
    if primary_comment:
        qa_comments = [primary_comment] + [
            c for c in qa_comments if c.lower() != primary_comment.lower()
        ]
    issue_comments_all = _dedup_texts(all_issue_comments, max_items=20)
    return {
        "annotation_errors": sorted(annot, key=lambda x: -x["severity"]),
        "frame_errors": frame,
        "total_loi": len(annot),
        "total_severity": sum(e["severity"] for e in annot),
        "rejected": rejected,
        "qa_comment": qa_comments[0] if qa_comments else "",
        "qa_comments": qa_comments,
        "issue_comments": issue_comments_all,
    }

def make_round_key(f: dict) -> str:
    """Unique dedup key per QA round."""
    return f"{f['hash']}_{f['task_id']}_{f['round']}_{f['type']}"

def aggregate_from_records(all_records: list[dict]) -> dict:
    """Compute top-level summary from a flat list of record dicts."""
    with_err = [r for r in all_records if r.get("total_loi", 0) > 0]
    total_data = len({str(r.get("job_id", "")) for r in all_records if r.get("job_id")})
    acc = ((len(all_records) - len(with_err)) / len(all_records) * 100
           if all_records else 0.0)
    err_freq: dict = defaultdict(lambda: {"records": 0, "total_severity": 0, "comments": []})
    for r in all_records:
        counted_names = set()
        for ename, esev in (r.get("errors") or {}).items():
            err_freq[ename]["records"] += 1
            err_freq[ename]["total_severity"] += esev
            counted_names.add(ename)
            comments = (r.get("error_comments") or {}).get(ename, [])
            if comments:
                err_freq[ename]["comments"].extend(comments)
        for ename, comments in (r.get("error_comments") or {}).items():
            if ename not in counted_names:
                err_freq[ename]["records"] += 1
                counted_names.add(ename)
            if comments:
                err_freq[ename]["comments"].extend(comments)
        # Include issue types extracted from history/detail source
        # even if comment text is empty.
        seen_hist_issue_types = set()
        for item in (r.get("history_issue_comments") or []):
            if not isinstance(item, dict):
                continue
            ename = _normalize_comment_text(item.get("issue_type")) or "unknown"
            if ename in seen_hist_issue_types:
                continue
            seen_hist_issue_types.add(ename)
            if ename not in counted_names:
                err_freq[ename]["records"] += 1
                counted_names.add(ename)
            cmt = _normalize_comment_text(item.get("comment"))
            if cmt:
                err_freq[ename]["comments"].append(cmt)
    top_errors = sorted(
        [{
            "name": n,
            "records": v["records"],
            "total_severity": v["total_severity"],
            "comments": _dedup_texts(v.get("comments") or [], max_items=5),
        } for n, v in err_freq.items()],
        key=lambda x: -x["records"])[:15]
    return {
        "total_data": total_data,
        "total_records": len(all_records),
        "records_with_error": len(with_err),
        "records_passed": len(all_records) - len(with_err),
        "accuracy_pct": round(acc, 1),
        "avg_qa_returns": round(
            sum(r.get("qa_review_count", 0) for r in all_records) / len(all_records), 2
        ) if all_records else 0,
        "top_errors": top_errors,
    }


def record_has_issue(rec: dict) -> bool:
    return bool(
        rec.get("total_loi", 0) > 0
        or rec.get("errors")
        or rec.get("history_issue_comments")
    )


def build_public_report(full_report: dict) -> dict:
    """Return the stored report shape: summary + issue-only detail."""
    datasets = full_report.get("datasets") or []
    all_records = [rec for ds in datasets for rec in (ds.get("records") or [])]
    issue_records = [rec for rec in all_records if record_has_issue(rec)]
    issue_datasets = []
    for ds in datasets:
        issue_recs = [rec for rec in (ds.get("records") or []) if record_has_issue(rec)]
        if not issue_recs:
            continue
        issue_datasets.append({
            **{k: v for k, v in ds.items() if k != "records"},
            "records": issue_recs,
            "summary": {
                "total_records": len(issue_recs),
                "records_with_error": len(issue_recs),
                "accuracy_pct": 0.0,
                "avg_qa_returns": round(
                    sum(r.get("qa_review_count", 0) for r in issue_recs) / len(issue_recs), 2
                ) if issue_recs else 0,
            },
        })

    public_report = {
        "generated_at": full_report.get("generated_at"),
        "target_user_id": full_report.get("target_user_id"),
        "target_job_ids": full_report.get("target_job_ids", []),
        "target_task_ids": full_report.get("target_task_ids", []),
        "summary": full_report.get("summary", {}),
        "top_errors": full_report.get("top_errors", []),
        "issue_records": issue_records,
        "records": issue_records,
        "datasets": issue_datasets,
        "new_records_this_run": full_report.get("new_records_this_run", 0),
    }
    if "source" in full_report:
        public_report["source"] = full_report["source"]
    return public_report

def merge_ingest(username: str, uid: str, new_files: list[dict],
                 target_job_ids: Optional[set[str]] = None,
                 target_task_ids: Optional[set[str]] = None,
                 total_data_hint: Optional[int] = None,
                 total_records_hint: Optional[int] = None,
                 task_comment_map: Optional[dict] = None) -> dict:
    """
    Incrementally merge new QA files into the existing report.
    Deduplicates using account.processed_qa_keys.
    Returns updated report dict.
    """
    acc = load_account(username) or blank_account(username)
    processed = set(acc.get("processed_qa_keys", []))
    existing_report = load_report_state(username)
    task_comment_map = task_comment_map or {}

    # Parse and filter only NEW QA files for this uid's tasks
    all_parsed = []
    file_contents: dict = {}
    for item in new_files:
        key = item.get("key", "")
        p = parse_key(key)
        if p:
            all_parsed.append(p)
            file_contents[key] = item.get("content", {})

    target_job_ids = {str(j) for j in (target_job_ids or set()) if str(j).strip()}
    target_task_ids = {str(t) for t in (target_task_ids or set()) if str(t).strip()}

    # Labeling files → identify target task_ids (don't need dedup)
    labeling_task_ids: set = set()
    labeling_meta: dict[tuple[str, str], dict] = {}
    for f in all_parsed:
        if target_job_ids or target_task_ids:
            if (
                f["type"] in ("LABELING", "REWORK")
                and (
                    (target_job_ids and f["job_id"] in target_job_ids)
                    or (target_task_ids and f["task_id"] in target_task_ids)
                )
            ):
                key = (f["hash"], f["task_id"])
                labeling_task_ids.add(key)
                # Prefer LABELING as representative when available.
                if key not in labeling_meta or labeling_meta[key]["type"] != "LABELING":
                    labeling_meta[key] = f
        else:
            if f["user_id"] == uid and f["type"] in ("LABELING", "REWORK"):
                key = (f["hash"], f["task_id"])
                labeling_task_ids.add(key)
                if key not in labeling_meta or labeling_meta[key]["type"] != "LABELING":
                    labeling_meta[key] = f

    if not labeling_task_ids:
        if target_job_ids or target_task_ids:
            log.warning(
                "No labeling records found for job_ids=%d task_ids=%d",
                len(target_job_ids),
                len(target_task_ids),
            )
        else:
            log.warning("No labeling records found for uid=%s", uid)
        return existing_report

    # Review files for user's tasks — include REWORK because some projects
    # store QA feedback directly in REWORK files instead of QA_RW/QA_RO.
    new_review_files = [
        f for f in all_parsed
        if f["type"] in REVIEW_TYPES
        and (f["hash"], f["task_id"]) in labeling_task_ids
        and f["key"] not in processed
    ]

    # Load existing datasets from report
    datasets: Dict[str, dict] = {}
    for ds in (existing_report.get("datasets") or []):
        h = ds["hash"]
        datasets[h] = ds
        # index records by task_id
        ds["_records_idx"] = {r["task_id"]: r for r in ds.get("records", [])}

    # Ensure records exist for all selected labeling tasks,
    # even if they currently have no QA review rounds.
    for (hash_id, task_id), lab in labeling_meta.items():
        if hash_id not in datasets:
            datasets[hash_id] = {
                "hash": hash_id,
                "job_id": lab["job_id"],
                "records": [],
                "_records_idx": {},
            }
        ds = datasets[hash_id]
        idx = ds["_records_idx"]
        if task_id not in idx:
            rec: dict = {
                "task_id": task_id,
                "job_id": lab["job_id"],
                "qa_review_count": 0,
                "rounds": [],
                "errors": {},
                "error_comments": {},
                "frame_errors": {},
                "qa_comments": [],
                "total_loi": 0,
                "total_severity": 0,
                "status": "UNKNOWN",
            }
            idx[task_id] = rec
            ds["records"].append(rec)

    # Group new review files by (hash, task_id)
    review_by_task: dict = defaultdict(list)
    for f in new_review_files:
        review_by_task[(f["hash"], f["task_id"])].append(f)

    if not new_review_files:
        log.info("No new review files for %s — all already processed", username)

    newly_added_keys: list = []

    for (hash_id, task_id), review_files in review_by_task.items():
        # Ensure dataset exists
        if hash_id not in datasets:
            datasets[hash_id] = {
                "hash": hash_id,
                "job_id": review_files[0]["job_id"],
                "records": [],
                "_records_idx": {},
            }
        ds = datasets[hash_id]
        idx = ds["_records_idx"]

        # Ensure record exists
        if task_id not in idx:
            rec: dict = {
                "task_id": task_id,
                "job_id": review_files[0]["job_id"],
                "qa_review_count": 0,
                "rounds": [],
                "errors": {},
                "error_comments": {},
                "frame_errors": {},
                "qa_comments": [],
                "total_loi": 0,
                "total_severity": 0,
                "status": "UNKNOWN",
            }
            idx[task_id] = rec
            ds["records"].append(rec)
        rec = idx[task_id]
        rec.setdefault("error_comments", {})
        rec.setdefault("qa_comments", [])
        rec.setdefault("history_issue_comments", [])

        # Add each new QA round (dedup by round_key)
        existing_round_keys = {make_round_key({"hash": hash_id, "task_id": task_id,
                                               "round": rnd["round"], "type": rnd["type"]})
                               for rnd in rec["rounds"]}
        for qf in sorted(review_files, key=lambda x: x["round"]):
            rk = make_round_key(qf)
            if rk in existing_round_keys:
                continue  # double dedup
            errs = parse_issues(file_contents.get(qf["key"], {}))
            round_entry = {
                "round": qf["round"], "type": qf["type"],
                "qa_user": qf["user_id"], "date": qf["date"],
                **errs,
            }
            rec["rounds"].append(round_entry)
            rec["qa_review_count"] += 1
            # Merge errors
            for e in errs["annotation_errors"]:
                rec["errors"][e["name"]] = rec["errors"].get(e["name"], 0) + e["severity"]
                comments = e.get("comments") or ([e["comment"]] if e.get("comment") else [])
                if comments:
                    rec["error_comments"].setdefault(e["name"], [])
                    rec["error_comments"][e["name"]].extend(comments)
            for e in errs["frame_errors"]:
                rec["frame_errors"][e["name"]] = rec["frame_errors"].get(e["name"], 0) + e["severity"]
            if errs.get("qa_comments"):
                rec["qa_comments"].extend(errs["qa_comments"])
            if errs["rejected"]:
                rec["status"] = "REJECTED"
            existing_round_keys.add(rk)
            newly_added_keys.append(qf["key"])

        # Recompute record totals
        unique_errors = set(e["name"] for rnd in rec["rounds"]
                            for e in rnd.get("annotation_errors", []))
        rec["total_loi"] = len(unique_errors)
        rec["total_severity"] = sum(rec["errors"].values())
        for ename, vals in list((rec.get("error_comments") or {}).items()):
            rec["error_comments"][ename] = _dedup_texts(vals, max_items=10)
        rec["qa_comments"] = _dedup_texts(rec.get("qa_comments") or [], max_items=20)
        if rec["rounds"] and rec["status"] == "UNKNOWN":
            rec["status"] = "PASSED"

    # Merge external task-level comments from history/detail source.
    if isinstance(task_comment_map, dict) and task_comment_map:
        for ds in datasets.values():
            for rec in ds.get("records", []):
                tid = str(rec.get("task_id", ""))
                hist = task_comment_map.get(tid)
                if not isinstance(hist, dict):
                    continue
                rec.setdefault("qa_comments", [])
                rec.setdefault("errors", {})
                rec.setdefault("error_comments", {})
                rec.setdefault("history_issue_comments", [])

                rec["qa_comments"] = _dedup_texts(
                    (rec.get("qa_comments") or []) + (hist.get("qa_comments") or []),
                    max_items=25,
                )

                for item in hist.get("issue_comments") or []:
                    if not isinstance(item, dict):
                        continue
                    issue_type = _normalize_comment_text(item.get("issue_type")) or "unknown"
                    comment = _normalize_comment_text(item.get("comment"))
                    rec["history_issue_comments"].append({
                        "issue_type": issue_type,
                        "comment": comment or "",
                    })
                    if comment:
                        rec["error_comments"].setdefault(issue_type, [])
                        rec["error_comments"][issue_type].append(comment)

                seen_history_error_names = set((rec.get("errors") or {}).keys())
                for item in rec.get("history_issue_comments") or []:
                    if not isinstance(item, dict):
                        continue
                    issue_type = _normalize_comment_text(item.get("issue_type")) or "unknown"
                    if issue_type not in seen_history_error_names:
                        rec["errors"][issue_type] = rec["errors"].get(issue_type, 0) + 1
                        seen_history_error_names.add(issue_type)

                for ename, vals in list((rec.get("error_comments") or {}).items()):
                    rec["error_comments"][ename] = _dedup_texts(vals, max_items=10)

                # Dedup history issue list by (issue_type, comment)
                dedup_hist = []
                seen_hist = set()
                for it in rec.get("history_issue_comments") or []:
                    if not isinstance(it, dict):
                        continue
                    itype = _normalize_comment_text(it.get("issue_type")) or "unknown"
                    cmt = _normalize_comment_text(it.get("comment")) or ""
                    key = (itype.lower(), cmt.lower())
                    if key in seen_hist:
                        continue
                    seen_hist.add(key)
                    dedup_hist.append({"issue_type": itype, "comment": cmt})
                    if len(dedup_hist) >= 50:
                        break
                rec["history_issue_comments"] = dedup_hist
                rec["total_loi"] = len(rec.get("errors") or {})
                rec["total_severity"] = sum((rec.get("errors") or {}).values())
                if rec["history_issue_comments"]:
                    rec["status"] = "REJECTED"

    # Recompute dataset summaries and collect all records
    all_records = []
    for ds in datasets.values():
        ds.pop("_records_idx", None)
        recs = ds["records"]
        with_err = [r for r in recs if r.get("total_loi", 0) > 0]
        ds_acc = ((len(recs) - len(with_err)) / len(recs) * 100) if recs else 0.0
        ds["summary"] = {
            "total_records": len(recs),
            "records_with_error": len(with_err),
            "accuracy_pct": round(ds_acc, 1),
            "avg_qa_returns": round(
                sum(r["qa_review_count"] for r in recs) / len(recs), 2) if recs else 0,
        }
        all_records.extend(recs)

    # Build final report
    summary_stats = aggregate_from_records(all_records)
    if total_data_hint:
        summary_stats["total_data"] = max(summary_stats.get("total_data", 0), int(total_data_hint))
    if total_records_hint:
        total_records = max(summary_stats.get("total_records", 0), int(total_records_hint))
        error_records = summary_stats.get("records_with_error", 0)
        summary_stats["total_records"] = total_records
        summary_stats["records_passed"] = max(0, total_records - error_records)
        summary_stats["accuracy_pct"] = round(
            ((total_records - error_records) / total_records * 100) if total_records else 0.0,
            1,
        )
    report = {
        "generated_at": datetime.now().isoformat(),
        "target_user_id": uid,
        "target_job_ids": sorted(target_job_ids) if target_job_ids else [],
        "target_task_ids": sorted(target_task_ids) if target_task_ids else [],
        "summary": {k: v for k, v in summary_stats.items() if k != "top_errors"},
        "top_errors": summary_stats["top_errors"],
        "datasets": list(datasets.values()),
        "new_records_this_run": len(newly_added_keys),
    }

    # Persist
    save_report_state(username, report)
    save_report(username, build_public_report(report))

    # Update account with new processed keys
    all_processed = list(processed | set(newly_added_keys))
    acc["processed_qa_keys"] = all_processed
    acc["last_scan"] = datetime.now().isoformat()
    acc["status"] = "idle"
    if uid:
        acc["user_id"] = uid
    save_account(acc)

    return report

# ── Pydantic models ────────────────────────────────────────
class AccountIn(BaseModel):
    username: str
    display_name: Optional[str] = None
    user_id: Optional[str] = None
    hashes: Optional[list[str]] = None

class IngestPayload(BaseModel):
    username: str
    target_uid: Optional[str] = None
    user_id: Optional[str] = None
    hashes: Optional[list[str]] = None
    job_ids: Optional[list[str]] = None
    task_ids: Optional[list[str]] = None
    total_data_hint: Optional[int] = None
    total_records_hint: Optional[int] = None
    task_comments: Optional[dict] = None
    files: list[dict] = []

class ScanRequest(BaseModel):
    usernames: Optional[list[str]] = None  # None = all accounts

class JobUpdate(BaseModel):
    status: Optional[str] = None
    progress: Optional[dict] = None
    error: Optional[str] = None

# ── Routes ─────────────────────────────────────────────────

@router.get("/health")
def qa_health():
    return {
        "status": "ok",
        "qa_accounts": len(list_accounts()),
        "pending_jobs": sum(1 for j in _scan_jobs.values() if j["status"] == "pending"),
        "route_version": QA_ROUTE_VERSION,
        "route_file": str(Path(__file__).resolve()),
    }

# ── Accounts ──────────────────────────────────────────────

@router.get("/accounts")
def get_accounts(current_user: dict = Depends(get_current_user)):
    """List all accounts — requires login. Both admin and users see all for performance page."""
    accs = list_accounts()
    result = []
    for a in accs:
        rpt = load_report(a["username"])
        summary = rpt.get("summary") or a.get("report_summary") or {}
        top_errors = rpt.get("top_errors") or a.get("top_errors") or []
        result.append({
            **public_account(a),
            "report_summary": summary,
            "top_errors": top_errors[:3],
            "generated_at": rpt.get("generated_at") or a.get("report_generated_at"),
            "total_data": summary.get("total_data", a.get("total_data", 0)),
            "total_records": summary.get("total_records", a.get("total_records", 0)),
            "records_with_error": summary.get("records_with_error", a.get("records_with_error", 0)),
            "records_passed": summary.get("records_passed", a.get("records_passed", 0)),
            "accuracy_pct": summary.get("accuracy_pct", a.get("accuracy_pct", 0)),
            "error_count": summary.get("error_count", a.get("error_count", 0)),
        })
    return result

@router.get("/accounts/{username}")
def get_account(username: str, current_user: dict = Depends(get_current_user)):
    username = _normalize_scanner_username(username)
    # Non-admin can only view their own account
    if current_user.get("role") != "admin" and _normalize_scanner_username(current_user.get("sub", "")) != username:
        raise HTTPException(403, "Không đủ quyền xem account này")
    acc = load_account(username)
    if not acc:
        if current_user.get("role") != "admin":
            acc = blank_account(username)
            save_account(acc)
        else:
            raise HTTPException(404, f"Account {username!r} not found")
    return {**public_account(acc), "report": load_report(username)}

@router.post("/accounts", status_code=201)
def create_account(body: AccountIn, current_user: dict = Depends(get_current_user)):
    username = _normalize_scanner_username(body.username)
    if current_user.get("role") != "admin" and _normalize_scanner_username(current_user.get("sub", "")) != username:
        raise HTTPException(403, "Cannot create account for another user")
    acc = load_account(username) or blank_account(username)
    if body.display_name:
        acc["display_name"] = body.display_name
    if body.user_id:
        acc["user_id"] = body.user_id
    if body.hashes is not None:
        acc["hashes"] = body.hashes
    save_account(acc)
    # Mark as needing discovery if no user_id
    if not acc.get("user_id"):
        acc["status"] = "discovering"
        save_account(acc)
    return public_account(acc)

@router.patch("/accounts/{username}")
def update_account(username: str, data: dict):
    acc = load_account(username)
    if not acc:
        raise HTTPException(404, "Not found")
    acc.update({k: v for k, v in data.items()
                if k not in ("username", "processed_qa_keys", "scanner_cookie", "cookie")})
    save_account(acc)
    return public_account(acc)

@router.delete("/accounts/{username}")
def delete_account(username: str):
    for p in [account_path(username), report_path(username), report_state_path(username)]:
        if p.exists():
            p.unlink()
    return {"ok": True}

@router.get("/accounts/{username}/report")
def get_report(username: str, current_user: dict = Depends(get_current_user)):
    # Non-admin can only view their own report
    if current_user.get("role") != "admin" and current_user.get("sub") != username:
        raise HTTPException(403, "Không đủ quyền xem report này")
    rpt = load_report(username)
    if not rpt:
        raise HTTPException(404, "No report yet — trigger a scan first")
    return rpt

# ── Credentials ───────────────────────────────────────────

@router.get("/credentials")
def all_credentials():
    return [
        {**make_creds(a["username"]),
         "user_id": a.get("user_id"),
         "hashes": a.get("hashes", [])}
        for a in list_accounts()
    ]

@router.get("/credentials/{username}")
def get_credentials(username: str):
    acc = load_account(username)
    if not acc:
        raise HTTPException(404, "Not found")
    return {**make_creds(username),
            "user_id": acc.get("user_id"),
            "hashes": acc.get("hashes", [])}

# ── Scan Jobs ─────────────────────────────────────────────

@router.post("/scan")
def trigger_scan(body: ScanRequest):
    """
    Dashboard calls this to kick off a scan.
    Creates a job that the Chrome Extension background picks up.
    """
    accs = list_accounts()
    if not accs:
        raise HTTPException(400, "No accounts configured")

    if body.usernames:
        targets = [a for a in accs if a["username"] in body.usernames]
    else:
        targets = accs

    if not targets:
        raise HTTPException(400, "No matching accounts")

    job_id = str(uuid.uuid4())
    progress = {
        a["username"]: {
            "pct": 0, "text": "Đang chờ extension...", "done": False,
            "user_id": a.get("user_id"), "hashes": a.get("hashes", []),
            "processed_qa_keys": a.get("processed_qa_keys", []),
        }
        for a in targets
    }
    _scan_jobs[job_id] = {
        "status": "pending",
        "created_at": datetime.now().isoformat(),
        "accounts": [a["username"] for a in targets],
        "progress": progress,
    }

    # Mark accounts as scanning
    for a in targets:
        a["status"] = "scanning"
        save_account(a)

    log.info("Scan job created: %s for %d accounts", job_id, len(targets))
    return {"job_id": job_id, "accounts": len(targets)}

@router.get("/jobs")
def get_jobs(status: str = "pending"):
    """Extension polls this to pick up work."""
    return [
        {"job_id": jid, **job}
        for jid, job in _scan_jobs.items()
        if job["status"] == status
    ]

@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = _scan_jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return {"job_id": job_id, **job}

@router.patch("/jobs/{job_id}")
def update_job(job_id: str, body: JobUpdate):
    """Extension calls this to report progress."""
    job = _scan_jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if body.status:
        job["status"] = body.status
    if body.progress:
        job["progress"].update(body.progress)
    if body.error:
        job["error"] = body.error
    return {"ok": True}

@router.get("/scan/status")
def scan_status():
    """Dashboard polls this to show live progress."""
    active = [(jid, j) for jid, j in _scan_jobs.items()
              if j["status"] in ("pending", "running")]
    if not active:
        # Return last completed job if any
        done = [(jid, j) for jid, j in _scan_jobs.items()
                if j["status"] in ("done", "error")]
        if done:
            jid, j = sorted(done, key=lambda x: x[1].get("created_at", ""))[-1]
            return {"job_id": jid, **j, "accounts": list_accounts()[:0]}
        return {"status": "idle"}
    jid, j = active[-1]
    return {"job_id": jid, **j}

# ── Ingest ────────────────────────────────────────────────

@router.post("/ingest")
async def ingest(payload: IngestPayload):
    """
    Extension posts scan results here.
    Backend deduplicates and merges incrementally.
    """
    username = payload.username
    uid = payload.target_uid or payload.user_id or ""
    target_job_ids = {str(j) for j in (payload.job_ids or []) if str(j).strip()}
    target_task_ids = {str(t) for t in (payload.task_ids or []) if str(t).strip()}

    # Update account metadata from scan
    acc = load_account(username) or blank_account(username)
    if uid:
        acc["user_id"] = uid
    if payload.hashes:
        existing_hashes = set(acc.get("hashes", []))
        acc["hashes"] = list(existing_hashes | set(payload.hashes))
    save_account(acc)

    if not payload.files:
        acc["status"] = "idle"
        save_account(acc)
        return {"ok": True, "message": "No files to process"}

    try:
        report = merge_ingest(
            username, uid, payload.files,
            target_job_ids=target_job_ids or None,
            target_task_ids=target_task_ids or None,
            total_data_hint=payload.total_data_hint,
            total_records_hint=payload.total_records_hint,
            task_comment_map=payload.task_comments or None,
        )
    except Exception as e:
        log.exception("Ingest error for %s", username)
        acc = load_account(username) or blank_account(username)
        acc["status"] = "idle"
        save_account(acc)
        return {
            "ok": False,
            "error": str(e),
            "error_type": type(e).__name__,
            "traceback": traceback.format_exc(limit=10),
            "route_version": QA_ROUTE_VERSION,
            "route_file": str(Path(__file__).resolve()),
        }

    new_count = report.get("new_records_this_run", 0)
    log.info("Ingested %s: %d new QA records", username, new_count)
    return {
        "ok": True,
        "username": username,
        "new_records": new_count,
        "summary": report.get("summary"),
        "route_version": QA_ROUTE_VERSION,
        "route_file": str(Path(__file__).resolve()),
    }

# ── Discovery notification ─────────────────────────────────

@router.get("/discover")
def get_discovery_queue():
    """Extension polls this to find accounts needing user_id discovery."""
    return [
        {"username": a["username"],
         **make_creds(a["username"]),
         "display_name": a.get("display_name", "")}
        for a in list_accounts()
        if not a.get("user_id") or a.get("status") == "discovering"
    ]


# ── Cookie Management (Admin) ──────────────────────────────

class CookieUpdateRequest(BaseModel):
    cookie: str = ""  # Full cookie string tu browser
    session: Optional[str] = None
    user_id: Optional[str] = None
    worker_id: Optional[str] = None
    hashes: Optional[List[str]] = None

@router.post("/cookie")
def update_scanner_cookie(body: CookieUpdateRequest, _admin: dict = Depends(require_admin)):
    """
    Admin cập nhật cookie scanner thủ công.
    Copy Cookie header từ browser (F12 → Network → bất kỳ request → Copy Cookie header value).
    Backend luu vao data/scanner/cookie.txt de scanner su dung.
    """
    cookie_str = body.cookie.strip()
    if not cookie_str:
        raise HTTPException(400, "Cookie không được rỗng")
    
    # Validate có vẻ là cookie hợp lệ
    if len(cookie_str) < 20:
        raise HTTPException(400, "Cookie quá ngắn, kiểm tra lại")
    
    try:
        _COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _COOKIE_FILE.write_text(cookie_str, encoding="utf-8")
        log.info("Cookie updated by admin, length=%d", len(cookie_str))
        return {
            "ok": True,
            "message": "Cookie đã được cập nhật thành công",
            "cookie_file": str(_COOKIE_FILE),
            "length": len(cookie_str),
        }
    except Exception as e:
        log.exception("Failed to write cookie file")
        raise HTTPException(500, f"Không thể lưu cookie: {e}")

@router.get("/cookie/status")
def get_cookie_status(_admin: dict = Depends(require_admin)):
    """Kiểm tra trạng thái cookie file hiện tại."""
    if not _COOKIE_FILE.exists():
        return {"exists": False, "message": "Chưa có cookie file"}
    
    try:
        content = _COOKIE_FILE.read_text(encoding="utf-8").strip()
        import os
        mtime = os.path.getmtime(_COOKIE_FILE)
        from datetime import datetime
        updated_at = datetime.fromtimestamp(mtime).isoformat()
        return {
            "exists": True,
            "length": len(content),
            "updated_at": updated_at,
            "cookie_file": str(_COOKIE_FILE),
        }
    except Exception as e:
        return {"exists": True, "error": str(e)}


# ── Python Scanner Integration ─────────────────────────────
# State store cho scanner jobs đang chạy
_scanner_jobs: Dict[str, Dict] = {}

# Resolve tool path tương đối với backend
# Scanner code is now owned by backend/services/qa_scanner. Runtime files live
# in data/scanner, which keeps tools/ and tools_local/ removable.
_SCANNER_CODE_DIR = _BASE_DIR / "services" / "qa_scanner"
_SCANNER_RUNTIME_DIR = Path(os.getenv("QA_SCANNER_RUNTIME_DIR", str(_DATA_DIR / "scanner")))
_SCANNER_RUNS_DIR = _SCANNER_RUNTIME_DIR / "multi_user_runs"
_USER_COOKIES_DIR = Path(os.getenv("QA_SCANNER_COOKIE_DIR", str(_SCANNER_RUNTIME_DIR / "user_cookies")))
_SCANNER_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
_SCANNER_RUNS_DIR.mkdir(parents=True, exist_ok=True)
_USER_COOKIES_DIR.mkdir(parents=True, exist_ok=True)

_RESOLVE_USER_SCAN_SCRIPT = _SCANNER_CODE_DIR / "resolve_user_scan.py"
_LEGACY_SCANNER_SCRIPT = _SCANNER_CODE_DIR / "qa_python_scanner.py"
_OPTIMIZED_SCANNER_SCRIPT = _SCANNER_CODE_DIR / "scan_users_optimized.py"
_SCANNER_SCRIPT = _LEGACY_SCANNER_SCRIPT
_COOKIE_FILE = _SCANNER_RUNTIME_DIR / "cookie.txt"
_DEFAULT_PLATFORM_BASE_URL = "http://global-autolabeling-service.evad.xiaomi.srv"
_DEFAULT_PLATFORM_HOST_HEADER = "global-autolabeling-service.evad.xiaomi.srv"


def _normalize_scanner_username(username: str) -> str:
    try:
        normalized, _ = normalize_labeler_username(username)
        return normalized
    except ValueError:
        raise HTTPException(400, "Username khong hop le")


def _user_cookie_path(username: str) -> Path:
    return _USER_COOKIES_DIR / f"{_normalize_scanner_username(username)}.txt"


def _account_cookie_status(username: str) -> dict:
    normalized = _normalize_scanner_username(username)
    acc = load_account(normalized) or {}
    cookie = str(acc.get("scanner_cookie") or acc.get("cookie") or "").strip()
    session_value = str(acc.get("scanner_session") or acc.get("session") or "").strip()
    return {
        "exists": bool(cookie),
        "length": len(cookie),
        "session_exists": bool(session_value),
        "session_length": len(session_value),
        "user_id": acc.get("user_id"),
        "worker_id": acc.get("worker_id"),
        "hash_count": len(acc.get("hashes") or []),
        "updated_at": acc.get("scanner_cookie_updated_at"),
        "account_file": str(account_path(normalized)),
    }


def _dedupe_normalized_usernames(usernames: Optional[List[str]]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in usernames or []:
        normalized = _normalize_scanner_username(raw)
        if normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


def _scanner_account_ready(acc: dict | None) -> bool:
    if not acc:
        return False
    cookie = str(acc.get("scanner_cookie") or acc.get("cookie") or "").strip()
    identity = acc.get("worker_id") or acc.get("user_id") or acc.get("ld_user_id")
    return bool(cookie and identity)


def _append_progress(progress: dict, line: str, limit: int = 100) -> None:
    lines = list(progress.get("log") or [])
    lines.append(line)
    progress["log"] = lines[-limit:]


async def _ensure_scanner_account(
    scanner_id: str,
    username: str,
    base_url: Optional[str],
    host_header: Optional[str],
    timeout: int = 20,
) -> Optional[dict]:
    normalized = _normalize_scanner_username(username)
    job = _scanner_jobs[scanner_id]
    progress = job["progress"].setdefault(
        normalized,
        {"status": "pending", "log": [], "pct": 0},
    )
    progress.update({"status": "resolving", "pct": max(progress.get("pct", 0), 5)})

    acc = load_account(normalized) or blank_account(normalized)
    acc["status"] = "resolving"
    save_account(acc)

    if not _RESOLVE_USER_SCAN_SCRIPT.exists():
        error = f"Resolver script not found: {_RESOLVE_USER_SCAN_SCRIPT}"
        progress.update({"status": "error", "error": error, "pct": 100})
        acc["status"] = "error"
        acc["last_scan_error"] = error
        save_account(acc)
        return None

    resolved_base_url = base_url or _DEFAULT_PLATFORM_BASE_URL
    resolved_host_header = host_header or _DEFAULT_PLATFORM_HOST_HEADER
    args = [
        sys.executable,
        str(_RESOLVE_USER_SCAN_SCRIPT),
        normalized,
        "--prepare-only",
        "--account-dir",
        str(ACCOUNTS_DIR),
        "--cookie-dir",
        str(_USER_COOKIES_DIR),
        "--base-url",
        resolved_base_url,
        "--host-header",
        resolved_host_header,
        "--timeout",
        str(timeout),
        "--password",
        DEFAULT_PASSWORD,
        "--no-auto-capture-when-missing",
    ]

    log.info("Resolve scanner account start [%s/%s]", scanner_id, normalized)
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(_SCANNER_RUNTIME_DIR),
        )
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if not line:
                continue
            _append_progress(progress, line)
            if "playwright_auto_login" in line or "auto_login" in line:
                progress["pct"] = max(progress.get("pct", 0), 25)
            elif "prepare_user_context" in line:
                progress["pct"] = max(progress.get("pct", 0), 35)
        await proc.wait()
    except Exception as exc:
        log.exception("Resolve scanner account failed [%s/%s]", scanner_id, normalized)
        error = f"{type(exc).__name__}: {exc}"
        progress.update({"status": "error", "error": error, "pct": 100})
        acc = load_account(normalized) or acc
        acc["status"] = "error"
        acc["last_scan_error"] = error
        save_account(acc)
        return None

    fresh = load_account(normalized) or acc
    if proc.returncode != 0:
        error = f"account resolve failed with exit code {proc.returncode}"
        progress.update({"status": "error", "error": error, "pct": 100})
        fresh["status"] = "error"
        fresh["last_scan_error"] = error
        save_account(fresh)
        return None

    if not _scanner_account_ready(fresh):
        error = "account resolve did not produce cookie and user identity"
        progress.update({"status": "error", "error": error, "pct": 100})
        fresh["status"] = "error"
        fresh["last_scan_error"] = error
        save_account(fresh)
        return None

    fresh["status"] = "ready"
    fresh["identity_status"] = fresh.get("identity_status") or "ready"
    fresh.pop("last_scan_error", None)
    save_account(fresh)
    progress.update(
        {
            "status": "ready",
            "pct": max(progress.get("pct", 0), 40),
            "account": public_account(fresh),
        }
    )
    return fresh


def _session_from_cookie_header(cookie: str) -> str:
    for part in (cookie or "").split(";"):
        if "=" not in part:
            continue
        key, value = part.strip().split("=", 1)
        if key.strip().lower() == "authorization":
            return value.strip()
    return ""


@router.post("/cookie/{username}")
def update_user_cookie(username: str, body: CookieUpdateRequest, _admin: dict = Depends(require_admin)):
    """Store a platform cookie for one scanner username."""
    normalized = _normalize_scanner_username(username)
    cookie_str = body.cookie.strip()
    session_value = (body.session or "").strip()
    if not cookie_str and session_value:
        cookie_str = session_value if ("=" in session_value or ";" in session_value) else f"Authorization={session_value}"
    if cookie_str and not session_value:
        session_value = _session_from_cookie_header(cookie_str)
    if not cookie_str:
        raise HTTPException(400, "Cookie/session khong duoc rong")
    if len(cookie_str) < 20:
        raise HTTPException(400, "Cookie/session qua ngan, kiem tra lai")
    try:
        acc = load_account(normalized) or blank_account(normalized)
        if body.user_id:
            acc["user_id"] = str(body.user_id)
        if body.worker_id or body.user_id:
            acc["worker_id"] = str(body.worker_id or body.user_id)
        if body.hashes is not None:
            acc["hashes"] = list(dict.fromkeys(str(h).strip() for h in body.hashes if str(h).strip()))
        if session_value:
            acc["scanner_session"] = session_value
            acc["session"] = session_value
            acc["scanner_session_updated_at"] = datetime.now().isoformat()
        acc["scanner_cookie"] = cookie_str
        acc["scanner_cookie_updated_at"] = datetime.now().isoformat()
        acc["scanner_cookie_source"] = "account_json"
        acc["identity_status"] = "manual_ready" if acc.get("user_id") else "manual_cookie_ready"
        save_account(acc)
        log.info("Per-user scanner cookie updated for %s, length=%d", normalized, len(cookie_str))
        return {
            "ok": True,
            "username": normalized,
            "account_file": str(account_path(normalized)),
            "length": len(cookie_str),
            "session_length": len(session_value),
            "user_id": acc.get("user_id"),
            "worker_id": acc.get("worker_id"),
            "hash_count": len(acc.get("hashes") or []),
        }
    except Exception as e:
        log.exception("Failed to write per-user cookie for %s", normalized)
        raise HTTPException(500, f"Khong the luu cookie user: {e}")


@router.get("/cookie/{username}/status")
def get_user_cookie_status(username: str, _admin: dict = Depends(require_admin)):
    normalized = _normalize_scanner_username(username)
    return {"username": normalized, **_account_cookie_status(normalized)}


@router.get("/cookies/status")
def get_all_user_cookie_status(_admin: dict = Depends(require_admin)):
    usernames = {a["username"] for a in list_accounts()}
    return [
        {"username": username, **_account_cookie_status(username)}
        for username in sorted(usernames)
    ]


class RunScannerRequest(BaseModel):
    usernames: Optional[List[str]] = None
    cookie: Optional[str] = None
    base_url: Optional[str] = None
    host_header: Optional[str] = None
    discover_by_user: bool = False
    max_records: Optional[int] = None
    job_max_pages: Optional[int] = None
    max_pages_per_job: Optional[int] = None


def _build_scanner_args(acc: dict, cookie_file: Path,
                        cookie_override: Optional[str],
                        base_url: Optional[str],
                        host_header: Optional[str],
                        discover: bool,
                        backend_url: str) -> List[str]:
    """Build argv cho qa_python_scanner.py cho một account."""
    args = [
        sys.executable,
        str(_SCANNER_SCRIPT),
        "--username", acc["username"],
        "--password", DEFAULT_PASSWORD,
        "--backend", backend_url,
    ]
    if acc.get("user_id"):
        args += ["--user-id", str(acc["user_id"])]
    for h in (acc.get("hashes") or []):
        args += ["--hash", h]
    if cookie_override:
        args += ["--cookie", cookie_override]
    elif cookie_file.exists():
        args += ["--cookie-file", str(cookie_file)]
    if base_url:
        args += ["--base-url", base_url]
    if host_header:
        args += ["--host-header", host_header]
    if discover:
        args += ["--discover-by-user"]
    return args


async def _run_scanner_for_account(scanner_id: str, acc: dict,
                                    cookie_file: Path,
                                    cookie_override: Optional[str],
                                    base_url: Optional[str],
                                    host_header: Optional[str],
                                    discover: bool,
                                    backend_url: str):
    """Chạy scanner subprocess cho 1 account, cập nhật trạng thái vào _scanner_jobs."""
    username = acc["username"]
    job = _scanner_jobs[scanner_id]
    job["progress"][username] = {"status": "running", "log": [], "pct": 0}

    # Cập nhật status account thành scanning
    acc["status"] = "scanning"
    save_account(acc)

    args = _build_scanner_args(acc, cookie_file, cookie_override,
                               base_url, host_header, discover, backend_url)
    log.info("Scanner start [%s/%s]: %s", scanner_id, username, " ".join(args))

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(_SCANNER_RUNTIME_DIR),
        )
        output_lines: List[str] = []
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip()
            output_lines.append(line)
            # Cập nhật live log (giữ 100 dòng cuối)
            job["progress"][username]["log"] = output_lines[-100:]
            # Parse rough progress từ output
            if "Fetching content" in line or "fetched" in line:
                job["progress"][username]["pct"] = 50
            elif "Gửi" in line or "batch" in line.lower():
                job["progress"][username]["pct"] = 80
            elif "Report đã lưu" in line or "backend summary" in line.lower():
                job["progress"][username]["pct"] = 100

        await proc.wait()
        rc = proc.returncode
        if rc == 0:
            job["progress"][username]["status"] = "done"
            job["progress"][username]["pct"] = 100
            log.info("Scanner done [%s/%s] rc=%d", scanner_id, username, rc)
        else:
            job["progress"][username]["status"] = "error"
            job["progress"][username]["error"] = f"exit code {rc}"
            log.warning("Scanner error [%s/%s] rc=%d", scanner_id, username, rc)
    except Exception as e:
        log.exception("Scanner subprocess error [%s/%s]", scanner_id, username)
        job["progress"][username]["status"] = "error"
        job["progress"][username]["error"] = str(e)

    # Reset account status
    fresh_acc = load_account(username)
    if fresh_acc:
        fresh_acc["status"] = "idle"
        save_account(fresh_acc)


async def _run_scanner_all(scanner_id: str, accounts: List[dict],
                            cookie_file: Path,
                            cookie_override: Optional[str],
                            base_url: Optional[str],
                            host_header: Optional[str],
                            discover: bool,
                            backend_url: str):
    """Chạy scanner tuần tự cho từng account, cập nhật job status."""
    job = _scanner_jobs[scanner_id]
    job["status"] = "running"
    for acc in accounts:
        await _run_scanner_for_account(
            scanner_id, acc, cookie_file, cookie_override,
            base_url, host_header, discover, backend_url
        )
    # Kiểm tra kết quả
    all_done = all(
        p.get("status") in ("done", "error")
        for p in job["progress"].values()
    )
    job["status"] = "done" if all_done else "error"
    job["finished_at"] = datetime.now().isoformat()
    log.info("Scanner job %s finished: %s", scanner_id, job["status"])


def _build_optimized_scanner_args(scanner_id: str,
                                  accounts: List[dict],
                                  cookie_file: Path,
                                  cookie_override: Optional[str],
                                  base_url: Optional[str],
                                  host_header: Optional[str],
                                  max_records: Optional[int] = None,
                                  job_max_pages: Optional[int] = None,
                                  max_pages_per_job: Optional[int] = None) -> tuple[List[str], Path]:
    """Build argv for the optimized username -> QA issue rows scanner."""
    run_name = f"backend_{scanner_id[:8]}"
    run_dir = _SCANNER_RUNS_DIR / run_name
    args = [
        sys.executable,
        str(_OPTIMIZED_SCANNER_SCRIPT),
        *[a["username"] for a in accounts],
        "--out-dir",
        str(_SCANNER_RUNS_DIR),
        "--run-name",
        run_name,
        "--cookie-dir",
        str(_USER_COOKIES_DIR),
        "--timeout",
        "20",
        "--user-workers",
        str(min(2, max(1, len(accounts)))),
        "--job-workers",
        "4",
        "--record-workers",
        "6",
        "--dashboard-report-dir",
        str(REPORTS_DIR),
        "--dashboard-account-dir",
        str(ACCOUNTS_DIR),
    ]
    if cookie_override:
        tmp_cookie = _SCANNER_RUNTIME_DIR / f"cookie_override_{scanner_id[:8]}.txt"
        tmp_cookie.write_text(cookie_override.strip(), encoding="utf-8")
        args += ["--cookie-file", str(tmp_cookie)]
        args += ["--allow-shared-cookie"]
    if base_url:
        args += ["--base-url", base_url]
    if host_header:
        args += ["--host-header", host_header]
    if max_records and max_records > 0:
        args += ["--max-records", str(max_records)]
    if job_max_pages and job_max_pages > 0:
        args += ["--job-max-pages", str(job_max_pages)]
    if max_pages_per_job and max_pages_per_job > 0:
        args += ["--max-pages-per-job", str(max_pages_per_job)]
    return args, run_dir


async def _run_optimized_scanner_all(scanner_id: str, accounts: List[dict],
                                     cookie_file: Path,
                                     cookie_override: Optional[str],
                                     base_url: Optional[str],
                                     host_header: Optional[str],
                                     max_records: Optional[int] = None,
                                     job_max_pages: Optional[int] = None,
                                     max_pages_per_job: Optional[int] = None):
    """Run the optimized scanner once for all accounts."""
    job = _scanner_jobs[scanner_id]
    job["status"] = "running"
    job["mode"] = "optimized-qa-errors-only"
    job["phase"] = "prepare_accounts"
    ready_accounts: List[dict] = []
    for acc in accounts:
        username = acc["username"]
        job["progress"][username] = {"status": "pending", "log": [], "pct": 0}
        ensured = await _ensure_scanner_account(
            scanner_id,
            username,
            base_url,
            host_header,
        )
        if ensured:
            ready_accounts.append(ensured)

    if not ready_accounts:
        job["status"] = "error"
        job["phase"] = "finished"
        job["finished_at"] = datetime.now().isoformat()
        log.warning("Optimized scanner job %s has no ready accounts", scanner_id)
        return

    accounts = ready_accounts
    job["phase"] = "scan"
    for acc in accounts:
        username = acc["username"]
        progress = job["progress"][username]
        progress["status"] = "running"
        progress["pct"] = max(progress.get("pct", 0), 45)
        acc["status"] = "scanning"
        save_account(acc)

    args, run_dir = _build_optimized_scanner_args(
        scanner_id,
        accounts,
        cookie_file,
        cookie_override,
        base_url,
        host_header,
        max_records=max_records,
        job_max_pages=job_max_pages,
        max_pages_per_job=max_pages_per_job,
    )
    log.info("Optimized scanner start [%s]: %s", scanner_id, " ".join(args))
    output_lines: List[str] = []
    tmp_cookie = _SCANNER_RUNTIME_DIR / f"cookie_override_{scanner_id[:8]}.txt"

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(_SCANNER_RUNTIME_DIR),
        )
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip()
            output_lines.append(line)
            for acc in accounts:
                username = acc["username"]
                progress = job["progress"][username]
                progress["log"] = output_lines[-100:]
                if "qa-review-issues" in line or "Review requests" in line:
                    progress["pct"] = max(progress.get("pct", 0), 70)
                elif "full-data" in line or "task-detail" in line:
                    progress["pct"] = max(progress.get("pct", 0), 45)
                if line.startswith(f"{username}:"):
                    if " ok" in line:
                        progress["status"] = "done"
                        progress["pct"] = 100
                    elif "failed" in line:
                        progress["status"] = "error"
                        progress["pct"] = 100

        await proc.wait()
        manifest_path = run_dir / "manifest.json"
        job["manifest"] = str(manifest_path)
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                for result in manifest.get("results", []):
                    username = result.get("username")
                    if username not in job["progress"]:
                        continue
                    progress = job["progress"][username]
                    progress["status"] = "done" if result.get("ok") else "error"
                    progress["pct"] = 100
                    if result.get("error"):
                        progress["error"] = result.get("error")
                    if result.get("dashboard_report"):
                        progress["dashboard_report"] = result.get("dashboard_report")
                    if result.get("outputs"):
                        progress["outputs"] = result.get("outputs")
            except Exception as exc:
                job["manifest_error"] = str(exc)

        if proc.returncode != 0:
            for acc in accounts:
                progress = job["progress"][acc["username"]]
                if progress.get("status") not in ("done", "error"):
                    progress["status"] = "error"
                    progress["error"] = f"exit code {proc.returncode}"
                    progress["pct"] = 100

        all_ok = all(p.get("status") == "done" for p in job["progress"].values())
        job["status"] = "done" if all_ok else "error"
    except Exception as e:
        log.exception("Optimized scanner subprocess error [%s]", scanner_id)
        job["status"] = "error"
        job["error"] = str(e)
        for acc in accounts:
            progress = job["progress"][acc["username"]]
            progress["status"] = "error"
            progress["error"] = str(e)
            progress["pct"] = 100
    finally:
        if cookie_override and tmp_cookie.exists():
            try:
                tmp_cookie.unlink()
            except Exception:
                pass
        for acc in accounts:
            fresh_acc = load_account(acc["username"])
            if fresh_acc:
                fresh_acc["status"] = "idle"
                save_account(fresh_acc)
        job["phase"] = "finished"
        job["finished_at"] = datetime.now().isoformat()
        log.info("Optimized scanner job %s finished: %s", scanner_id, job["status"])


@router.post("/run_scanner")
async def run_scanner(body: RunScannerRequest, current_user: dict = Depends(get_current_user)):
    """
    Kích hoạt QA Python Scanner chạy trực tiếp từ server.
    Chỉ admin mới được dùng endpoint này.
    Quet TAT CA accounts (hoac accounts chi dinh).
    Optimized scanner writes user summary to data/user and filtered QA errors to data/report.
    Legacy scanner can still post results to /api/qa/ingest.
    
    Returns scanner_id để frontend poll GET /api/qa/scanner/{scanner_id} lấy trạng thái.
    """
    scanner_script = _OPTIMIZED_SCANNER_SCRIPT if _OPTIMIZED_SCANNER_SCRIPT.exists() else _SCANNER_SCRIPT
    if not scanner_script.exists():
        return {
            "ok": False,
            "error": f"Scanner script not found: {scanner_script}"
        }

    is_admin = current_user.get("role") == "admin"
    requested_usernames = _dedupe_normalized_usernames(body.usernames)
    if not is_admin:
        own_username = _normalize_scanner_username(current_user.get("sub", ""))
        if requested_usernames and requested_usernames != [own_username]:
            raise HTTPException(403, "Users can only run their own QA scan")
        requested_usernames = [own_username]
        if body.cookie:
            raise HTTPException(403, "Cookie override is admin-only")

    if requested_usernames:
        accs = []
        for username in requested_usernames:
            acc = load_account(username)
            if not acc:
                acc = blank_account(username)
                save_account(acc)
            accs.append(acc)
    else:
        accs = list_accounts()
    if not accs:
        return {"ok": False, "error": "No accounts to scan"}

    # Xác định backend URL (dùng localhost nội bộ)
    backend_url = "http://localhost:7788"

    scanner_id = str(uuid.uuid4())
    _scanner_jobs[scanner_id] = {
        "status": "pending",
        "created_at": datetime.now().isoformat(),
        "accounts": [a["username"] for a in accs],
        "progress": {a["username"]: {"status": "pending", "log": [], "pct": 0} for a in accs},
        "scanner_script": str(scanner_script),
    }

    # Chạy background task
    if scanner_script == _OPTIMIZED_SCANNER_SCRIPT:
        asyncio.create_task(
            _run_optimized_scanner_all(
                scanner_id, accs, _COOKIE_FILE,
                body.cookie, body.base_url, body.host_header,
                body.max_records, body.job_max_pages, body.max_pages_per_job,
            )
        )
    else:
        asyncio.create_task(
            _run_scanner_all(
                scanner_id, accs, _COOKIE_FILE,
                body.cookie, body.base_url, body.host_header,
                body.discover_by_user, backend_url,
            )
        )

    return {
        "ok": True,
        "scanner_id": scanner_id,
        "accounts": len(accs),
        "mode": "optimized-qa-errors-only" if scanner_script == _OPTIMIZED_SCANNER_SCRIPT else "legacy",
        "message": f"Scanner đã khởi động cho {len(accs)} account(s)"
    }


@router.get("/scanner/{scanner_id}")
def get_scanner_status(scanner_id: str):
    """Frontend poll để lấy trạng thái scanner job."""
    job = _scanner_jobs.get(scanner_id)
    if not job:
        raise HTTPException(404, "Scanner job not found")
    return {"scanner_id": scanner_id, **job}


@router.get("/scanner_active")
def get_active_scanners():
    """Trả về danh sách scanner jobs đang chạy hoặc gần đây nhất."""
    active = [(sid, j) for sid, j in _scanner_jobs.items()
              if j["status"] in ("pending", "running")]
    if active:
        return [{"scanner_id": sid, **j} for sid, j in active]
    # Trả về job cuối nếu không có job đang chạy
    done = sorted(_scanner_jobs.items(),
                  key=lambda x: x[1].get("created_at", ""), reverse=True)
    if done:
        sid, j = done[0]
        return [{"scanner_id": sid, **j}]
    return []
