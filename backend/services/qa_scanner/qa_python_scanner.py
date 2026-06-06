#!/usr/bin/env python3
"""
QA Python Scanner — Chạy thẳng từ server, không cần browser/extension.
==========================================================================
Dùng session cookie từ browser để gọi thẳng API S3 của Xiaomi platform.

CÁCH DÙNG:
  1. Lấy cookie từ browser:
     - Mở platform → F12 → Application → Cookies → copy value của _ga hoặc session cookie
     - HOẶC: F12 → Network → chọn bất kỳ request → copy "Cookie" header
     
  2. Chạy:
     python3 qa_python_scanner.py --cookie "session=xxx; ..."
     
     Hoặc lưu cookie vào file:
     echo 'session=xxx; ...' > cookie.txt
     python3 qa_python_scanner.py --cookie-file cookie.txt

  3. Nếu muốn chỉ phân tích file đã có:
     python3 qa_python_scanner.py --from-file qa_raw_24353_xxx.json

OPTIONS:
  --user-id     UserId cần phân tích (default: 24353)
  --hash        Hash cụ thể (default: 9e786a86a9451da1f2f67ae8f17c57de)
    --cookie      Cookie string từ browser
    --cookie-file File chứa cookie hoặc Authorization header (dòng: Authorization=...)
  --from-file   Dùng raw JSON đã có, bỏ qua bước fetch
  --discover-by-user  Quét global để tự tìm tất cả hash theo user_id
  --discover-start-marker  Marker bắt đầu khi discover global
  --full-discover-by-user  Crawl tuần tự toàn bucket, có checkpoint resume
  --job-id      JobId từ history để lọc task theo job
  --job-ids-file File chứa job ids hoặc JSON history
  --history-file File JSON/HAR history/detail để map comment QA theo task_id
  --key-index-file  Dùng file list key S3, lọc user trước khi fetch
  --known-key   Fetch thẳng một statistics.review key đã biết
  --fetch-workers Số worker fetch content song song
  --output      Lưu report ra file (default: tự sinh tên)
  --save-raw    Lưu raw data ra file
  --backend     Gửi kết quả về LD backend (default: http://localhost:7788)
"""

import re
import json
import time
import argparse
import sys
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, parse_qsl, urlsplit

from scanner_paths import data_dir

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ImportError:
    print("❌ Cần cài requests: pip install requests")
    sys.exit(1)

# ─── CONFIG ────────────────────────────────────────────────────
BASE_URL = "http://global-autolabeling-service.evad.xiaomi.srv"
S3_BASE  = f"{BASE_URL}/appen/pointcloud/contributor_proxy/v1/lidar"
LOGIN_URL = f"{BASE_URL}/appen/ui/api/account/login"

DEFAULT_USER_ID = "24353"
DEFAULT_HASH    = "9e786a86a9451da1f2f67ae8f17c57de"
DEFAULT_BACKEND = "http://localhost:7788"
SCRIPT_VERSION = "2026-05-28.history-items-v9"

DEFAULT_WORKER_JOB_STATUS = ("LAUNCH", "RUNNING", "PAUSE")
DEFAULT_WORKER_STATUS = ("CONFIRMED",)
DEFAULT_WORKER_SORT_BY = "CONFIRM_TIME"
DEFAULT_WORKER_PAGE_SIZE = 200

FRAME_RE = re.compile(r"khung hình|帧属性", re.IGNORECASE)
REVIEW_TYPES = ("QA_RW", "QA_RO", "REWORK")
COMMENT_KEY_RE = re.compile(
    r"(comment|remark|feedback|reason|reject|note|message|desc)",
    re.IGNORECASE,
)
LABEL_ITEMS_KEY_RE = re.compile(
    r"(label(ing)?|anno).*(item|items|count)"
    r"|item(s)?_count|record(s)?_count|task(s)?_count"
    r"|total.*(item|record|task)"
    r"|label.*(num|number|qty)",
    re.IGNORECASE,
)
LABEL_ITEMS_LIST_KEYS = {
    "records", "recordlist", "record_list",
    "items", "itemlist", "item_list",
    "tasks", "tasklist", "task_list",
    "labelingitems", "labeling_items", "label_items",
}
TASK_ID_KEY_RE = re.compile(r"(^|[_-])task([_-]?id)?$", re.IGNORECASE)
JOB_ID_KEY_RE = re.compile(r"(^|[_-])job([_-]?id)?$", re.IGNORECASE)
RECORD_ID_KEY_RE = re.compile(r"(^|[_-])(record|task)([_-]?id)?$", re.IGNORECASE)
USER_ID_KEY_RE = re.compile(r"(^|[_-])(user|contributor|annotator)([_-]?id)?$|^uid$", re.IGNORECASE)
ISSUE_TYPE_KEY_RE = re.compile(
    r"(issue|error).*(type|name|key|label)|^(issue|error)$",
    re.IGNORECASE,
)
DEFAULT_LD_TITLE_RE = r"BEVLE-ZS-.*Labeling Job"
AUDIT_ID_RE = re.compile(
    r"\b\d{2,}\.\d{2,}_[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{4,6}\b"
    r"|\b\d{2,}\.\d{2,}_[0-9]{8}T[0-9]{4,6}\b"
)
PRIMARY_COMMENT_KEYS = (
    "reviewComment", "review_comment",
    "qaComment", "qa_comment",
    "comment", "remarks", "remark",
    "feedback", "message", "note",
    "rejectReason", "reject_reason", "reason",
)
STATS_REVIEW_RE = re.compile(
    r"statistics\.review\.(\d+)\.(\d+)\.(\d+)_([^.]+)\.(\d+)"
    r"\.(LABELING|REWORK|QA_RW|QA_RO)\.(\d+)\.json$"
)
STATS_LEGACY_RE = re.compile(
    r"(\d+)\.(\d+)\.(\d+)_([^.]+)\.(\d+)"
    r"\.(LABELING|REWORK|QA_RW|QA_RO)\.(\d+)\.statistics\.json$"
)
STATS_KEY_RE = re.compile(
    r"\b[a-f0-9]{32}/(?:statistics\.review\.\d+\.\d+\.\d+_[^.]+\.\d+"
    r"\.(?:LABELING|REWORK|QA_RW|QA_RO)\.\d+\.json|"
    r"\d+\.\d+\.\d+_[^.]+\.\d+\.(?:LABELING|REWORK|QA_RW|QA_RO)"
    r"\.\d+\.statistics\.json)\b",
    re.IGNORECASE,
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


def _normalize_numeric_id(v: object) -> str:
    if isinstance(v, bool) or v is None:
        return ""
    if isinstance(v, (int, float)):
        iv = int(v)
        return str(iv) if iv >= 0 else ""
    if isinstance(v, str):
        m = re.search(r"\b(\d{2,})\b", v)
        return m.group(1) if m else ""
    return ""


def _normalize_int(v: object) -> int | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        iv = int(v)
        return iv if iv >= 0 else None
    if isinstance(v, str):
        m = re.search(r"\b(\d{1,})\b", v)
        if m:
            try:
                iv = int(m.group(1))
                return iv if iv >= 0 else None
            except Exception:
                return None
    return None


def _extract_labeling_items(node: dict) -> list[tuple[int, str]]:
    """
    Extract possible Labeling Items counts from a dict node.
    Returns list of (count, source_key).
    """
    out: list[tuple[int, str]] = []
    for k, v in node.items():
        key_raw = str(k or "")
        key = key_raw.strip().lower().replace(" ", "_").replace("-", "_")

        # Numeric count in labeled keys
        if LABEL_ITEMS_KEY_RE.search(key_raw):
            cnt = _normalize_int(v)
            if cnt is not None:
                out.append((cnt, key_raw))
                continue
            if isinstance(v, dict):
                for ck in ("count", "total", "total_count", "totalCount", "size", "num", "number"):
                    if ck in v:
                        cnt2 = _normalize_int(v.get(ck))
                        if cnt2 is not None:
                            out.append((cnt2, f"{key_raw}.{ck}"))
                            break

        # List length in known list keys
        if key in LABEL_ITEMS_LIST_KEYS and isinstance(v, list):
            out.append((len(v), key_raw))

        # Some APIs return nested records/items with count field
        if isinstance(v, dict):
            key2 = key
            if key2 in LABEL_ITEMS_LIST_KEYS:
                for ck in ("count", "total", "total_count", "totalCount", "size"):
                    if ck in v:
                        cnt3 = _normalize_int(v.get(ck))
                        if cnt3 is not None:
                            out.append((cnt3, f"{key_raw}.{ck}"))
                            break

    return out


def _extract_direct_ids(node: dict, key_re: re.Pattern) -> set[str]:
    out: set[str] = set()
    for k, v in node.items():
        if not key_re.search(str(k)):
            continue
        if isinstance(v, list):
            for it in v:
                nid = _normalize_numeric_id(it)
                if nid:
                    out.add(nid)
        else:
            nid = _normalize_numeric_id(v)
            if nid:
                out.add(nid)
    return out


def _extract_direct_issue_type(node: dict) -> str:
    for k, v in node.items():
        if str(k).strip().lower() == "type":
            if isinstance(v, str):
                t = re.sub(r"\s+", " ", v).strip()
                if t and len(t) <= 200:
                    return t
            elif isinstance(v, list):
                vals = []
                for it in v[:10]:
                    if isinstance(it, str):
                        t = re.sub(r"\s+", " ", it).strip()
                        if t and len(t) <= 200:
                            vals.append(t)
                vals = _dedup_texts(vals, max_items=5)
                if vals:
                    return " | ".join(vals)
        if not ISSUE_TYPE_KEY_RE.search(str(k)):
            continue
        if isinstance(v, str):
            t = re.sub(r"\s+", " ", v).strip()
            if t and len(t) <= 120:
                return t
        elif isinstance(v, (int, float)):
            return str(int(v))
    return ""


def _extract_direct_comments(node: dict) -> list[str]:
    out: list[str] = []
    for k, v in node.items():
        if COMMENT_KEY_RE.search(str(k)):
            out.extend(_collect_strings(v, 0, 3))
    return _dedup_texts(out, max_items=20)


def _safe_json_loads(raw: str) -> object | None:
    try:
        return json.loads(raw)
    except Exception:
        return None


def _iter_json_documents_from_file(path: str) -> list[object]:
    raw = Path(path).read_text(encoding="utf-8", errors="ignore")
    docs: list[object] = []

    parsed = _safe_json_loads(raw)
    if parsed is not None:
        docs.append(parsed)
    else:
        # Try NDJSON fallback.
        for line in raw.splitlines():
            s = line.strip()
            if not s:
                continue
            item = _safe_json_loads(s)
            if item is not None:
                docs.append(item)
    return docs


def load_user_id_from_account(username: str) -> str:
    candidates = [
        data_dir() / "user" / f"{username}.json",
        Path("data") / "user" / f"{username}.json",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        uid = data.get("user_id") or data.get("userId") or data.get("uid")
        if uid:
            return str(uid)
    return ""


def load_task_comments_from_history(path: str,
                                    target_job_ids: set[str] | None = None,
                                    target_uid: str | None = None) -> dict[str, dict]:
    """
    Parse history/detail responses (JSON/HAR/NDJSON) and extract comments by task_id.
    Output:
      {
        "12345": {
          "qa_comments": [...],
          "issue_comments": [{"issue_type": "...", "comment": "..."}]
        }
      }
    """
    docs = _iter_json_documents_from_file(path)
    result: dict[str, dict] = {}
    job_filter = {str(j) for j in (target_job_ids or set()) if str(j).strip()}
    uid_filter = str(target_uid).strip() if target_uid else ""

    def _extract_ids_from_pairs(pairs: list[tuple[str, object]]) -> tuple[set[str], set[str], set[str]]:
        task_ids: set[str] = set()
        job_ids: set[str] = set()
        user_ids: set[str] = set()
        for raw_key, raw_val in pairs:
            key = str(raw_key or "").strip().lower().replace("-", "_")
            nid = _normalize_numeric_id(raw_val)
            if not nid:
                continue
            if ("task" in key and "id" in key) or key == "task":
                task_ids.add(nid)
                continue
            if ("job" in key and "id" in key) or key == "job":
                job_ids.add(nid)
                continue
            if ("user" in key and "id" in key) or key in {"uid", "user"} or "contributor" in key:
                user_ids.add(nid)
                continue
        return task_ids, job_ids, user_ids

    def _extract_ids_from_url(url: str) -> tuple[set[str], set[str], set[str]]:
        if not isinstance(url, str) or not url:
            return set(), set(), set()
        try:
            query_pairs = parse_qsl(urlsplit(url).query, keep_blank_values=False)
        except Exception:
            query_pairs = []
        return _extract_ids_from_pairs(query_pairs)

    def _extract_issue_types_from_item(item: dict) -> list[str]:
        issue_types: list[str] = []
        raw_type = item.get("type")
        if isinstance(raw_type, str):
            t = _normalize_comment_text(raw_type)
            if t:
                issue_types.append(t)
        elif isinstance(raw_type, list):
            for it in raw_type[:20]:
                if isinstance(it, str):
                    t = _normalize_comment_text(it)
                    if t:
                        issue_types.append(t)
        fallback = _extract_direct_issue_type(item)
        if fallback:
            issue_types.append(fallback)
        return _dedup_texts(issue_types, max_items=20)

    def ensure_bucket(task_id: str) -> dict:
        b = result.setdefault(task_id, {"qa_comments": [], "issue_comments": []})
        b.setdefault("qa_comments", [])
        b.setdefault("issue_comments", [])
        return b

    def _clean_history_comments(comments: list[str]) -> list[str]:
        clean = []
        for c in _dedup_texts(comments, max_items=20):
            lc = c.strip().lower()
            if lc in {"ok", "success", "true", "false", "null", "none"}:
                continue
            clean.append(c)
        return clean

    def add_comments(task_ids: set[str], comments: list[str]):
        if not task_ids:
            return
        comments = _clean_history_comments(comments)
        if not comments:
            return
        for tid in task_ids:
            b = ensure_bucket(tid)
            b["qa_comments"].extend(comments)

    def add_issue_comments(task_ids: set[str], issue_types: list[str], comments: list[str]):
        if not task_ids:
            return
        clean_issue_types = _dedup_texts(issue_types, max_items=20) or ["unknown"]
        clean_comments = _clean_history_comments(comments)
        for tid in task_ids:
            b = ensure_bucket(tid)
            if clean_comments:
                b["qa_comments"].extend(clean_comments)
                for itype in clean_issue_types:
                    for c in clean_comments:
                        b["issue_comments"].append({"issue_type": itype, "comment": c})
            else:
                for itype in clean_issue_types:
                    b["issue_comments"].append({"issue_type": itype, "comment": ""})

    def walk(node: object,
             current_tasks: set[str] | None = None,
             current_jobs: set[str] | None = None,
             current_uids: set[str] | None = None,
             depth: int = 0):
        if depth > 8 or node is None:
            return

        cur_tasks = set(current_tasks or set())
        cur_jobs = set(current_jobs or set())
        cur_uids = set(current_uids or set())

        if isinstance(node, dict):
            direct_tasks = _extract_direct_ids(node, TASK_ID_KEY_RE)
            direct_jobs = _extract_direct_ids(node, JOB_ID_KEY_RE)
            direct_uids = _extract_direct_ids(node, USER_ID_KEY_RE)
            if direct_tasks:
                cur_tasks |= direct_tasks
            if direct_jobs:
                cur_jobs |= direct_jobs
            if direct_uids:
                cur_uids |= direct_uids

            if job_filter and cur_jobs and not (cur_jobs & job_filter):
                pass_filter = False
            elif uid_filter and cur_uids and uid_filter not in cur_uids:
                pass_filter = False
            else:
                pass_filter = True

            if pass_filter and cur_tasks:
                comments = _extract_direct_comments(node)
                if comments:
                    add_comments(cur_tasks, comments)
                issue_type = _extract_direct_issue_type(node)
                if issue_type:
                    add_issue_comments(cur_tasks, [issue_type], comments)

                # Known review detail shape:
                # data.reviews[].items[].{type:[...], comment:"..."}
                items = node.get("items")
                if isinstance(items, list):
                    for item in items[:1000]:
                        if not isinstance(item, dict):
                            continue
                        item_issue_types = _extract_issue_types_from_item(item)
                        item_comments = _extract_direct_comments(item)
                        add_issue_comments(cur_tasks, item_issue_types, item_comments)

            for v in node.values():
                if isinstance(v, (dict, list)):
                    walk(v, cur_tasks, cur_jobs, cur_uids, depth + 1)
            return

        if isinstance(node, list):
            for item in node[:500]:
                walk(item, cur_tasks, cur_jobs, cur_uids, depth + 1)
            return

    for doc in docs:
        if isinstance(doc, dict) and isinstance(doc.get("log"), dict):
            # HAR format
            entries = doc.get("log", {}).get("entries") or []
            for ent in entries:
                if not isinstance(ent, dict):
                    continue
                req_tasks: set[str] = set()
                req_jobs: set[str] = set()
                req_uids: set[str] = set()
                req = ent.get("request")
                if isinstance(req, dict):
                    url = req.get("url")
                    if isinstance(url, str) and url:
                        tset, jset, uset = _extract_ids_from_url(url)
                        req_tasks |= tset
                        req_jobs |= jset
                        req_uids |= uset

                    query_items = req.get("queryString")
                    if isinstance(query_items, list):
                        pairs = []
                        for q in query_items:
                            if not isinstance(q, dict):
                                continue
                            pairs.append((q.get("name") or q.get("key") or "", q.get("value")))
                        tset, jset, uset = _extract_ids_from_pairs(pairs)
                        req_tasks |= tset
                        req_jobs |= jset
                        req_uids |= uset

                    post_data = req.get("postData")
                    if isinstance(post_data, dict):
                        params = post_data.get("params")
                        if isinstance(params, list):
                            pairs = []
                            for p in params:
                                if not isinstance(p, dict):
                                    continue
                                pairs.append((p.get("name") or p.get("key") or "", p.get("value")))
                            tset, jset, uset = _extract_ids_from_pairs(pairs)
                            req_tasks |= tset
                            req_jobs |= jset
                            req_uids |= uset

                candidates: list[object] = []
                for side in ("response", "request"):
                    part = ent.get(side)
                    if not isinstance(part, dict):
                        continue
                    content = part.get("content") if side == "response" else part.get("postData")
                    if isinstance(content, dict):
                        text = content.get("text")
                        if isinstance(text, str) and text.strip():
                            if content.get("encoding") == "base64":
                                try:
                                    text = base64.b64decode(text).decode("utf-8", errors="ignore")
                                except Exception:
                                    pass
                            parsed = _safe_json_loads(text)
                            if parsed is not None:
                                candidates.append(parsed)
                for cand in candidates:
                    walk(cand, req_tasks, req_jobs, req_uids, depth=0)
        else:
            walk(doc, depth=0)

    # Dedup/compact
    for tid, bucket in result.items():
        bucket["qa_comments"] = _dedup_texts(bucket.get("qa_comments") or [], max_items=20)
        dedup_issue = []
        seen = set()
        for item in bucket.get("issue_comments") or []:
            if not isinstance(item, dict):
                continue
            issue_type = _normalize_comment_text(item.get("issue_type")) or ""
            comment = _normalize_comment_text(item.get("comment")) or ""
            k = (issue_type.lower(), comment.lower())
            if k in seen:
                continue
            seen.add(k)
            dedup_issue.append({
                "issue_type": issue_type or "unknown",
                "comment": comment,
            })
            if len(dedup_issue) >= 50:
                break
        bucket["issue_comments"] = dedup_issue

    return result


def extract_ld_jobs_from_history(path: str,
                                 title_pattern: str = DEFAULT_LD_TITLE_RE,
                                 job_type: str = "Labeling",
                                 target_uid: str | None = None) -> dict:
    """
    Extract LD labeling jobs and record/task ids from a platform history export.

    The parser is intentionally shape-tolerant: it supports plain JSON, NDJSON,
    and HAR files, then walks nested objects looking for a BEVLE-ZS...Labeling Job
    title plus Job Type=Labeling. When a matching job context is found, any
    taskId/recordId values inside it are treated as records done by the user.
    """
    docs = _iter_json_documents_from_file(path)
    title_re = re.compile(title_pattern, re.IGNORECASE)
    expected_job_type = (job_type or "").strip().lower()
    uid_filter = str(target_uid).strip() if target_uid else ""

    jobs: dict[str, dict] = {}
    records: dict[str, dict] = {}

    def _strings_from_key(node: dict, key_words: tuple[str, ...]) -> list[str]:
        values: list[str] = []
        for k, v in node.items():
            kl = str(k).strip().lower().replace("-", "_")
            if not any(word in kl for word in key_words):
                continue
            values.extend(_collect_strings(v, 0, 2))
        return _dedup_texts(values, max_items=20)

    def _find_ld_title(node: dict) -> str:
        for text in _strings_from_key(node, ("title", "name", "job_name", "project")):
            if title_re.search(text):
                return text
        for text in _collect_strings(node, 0, 2):
            if title_re.search(text):
                return text
        return ""

    def _find_job_type(node: dict) -> str:
        for text in _strings_from_key(node, ("job_type", "jobtype", "type")):
            if text:
                return text
        return ""

    def _direct_user_ids(node: dict) -> set[str]:
        return _extract_direct_ids(node, USER_ID_KEY_RE)

    def _direct_job_ids(node: dict) -> set[str]:
        return _extract_direct_ids(node, JOB_ID_KEY_RE)

    def _direct_record_ids(node: dict) -> set[str]:
        return _extract_direct_ids(node, RECORD_ID_KEY_RE)

    def _passes_uid_filter(context_uids: set[str], direct_uids: set[str]) -> bool:
        if not uid_filter:
            return True
        all_uids = set(context_uids) | set(direct_uids)
        return not all_uids or uid_filter in all_uids

    def _job_key(job_ids: set[str], title: str) -> str:
        if job_ids:
            return sorted(job_ids)[0]
        return title or "unknown"

    def _record_key(job_id: str, record_id: str) -> str:
        return f"{job_id}|{record_id}"

    def _register_job(job_ids: set[str], title: str, found_job_type: str,
                      label_items: int | None = None,
                      label_items_source: str = ""):
        key = _job_key(job_ids, title)
        item = jobs.setdefault(key, {
            "job_id": key if key != title else "",
            "title": title,
            "job_type": found_job_type or job_type,
            "record_ids": [],
            "labeling_items": None,
            "labeling_items_source": "",
        })
        if title and not item.get("title"):
            item["title"] = title
        if found_job_type and not item.get("job_type"):
            item["job_type"] = found_job_type
        if label_items is not None:
            cur = item.get("labeling_items")
            if cur is None or label_items > cur:
                item["labeling_items"] = label_items
                item["labeling_items_source"] = label_items_source
        return item

    def _register_records(job_ids: set[str], title: str, found_job_type: str, record_ids: set[str]):
        if not record_ids:
            return
        job_item = _register_job(job_ids, title, found_job_type)
        job_id = job_item.get("job_id") or _job_key(job_ids, title)
        for record_id in sorted(record_ids):
            rk = _record_key(job_id, record_id)
            records[rk] = {
                "job_id": job_id,
                "task_id": record_id,
                "record_id": record_id,
                "title": job_item.get("title", ""),
                "job_type": job_item.get("job_type", job_type),
            }
            if record_id not in job_item["record_ids"]:
                job_item["record_ids"].append(record_id)

    def walk(node: object,
             context_title: str = "",
             context_job_type: str = "",
             context_job_ids: set[str] | None = None,
             context_uids: set[str] | None = None,
             depth: int = 0):
        if depth > 12 or node is None:
            return
        job_ids = set(context_job_ids or set())
        uids = set(context_uids or set())
        title = context_title
        current_job_type = context_job_type

        if isinstance(node, dict):
            direct_uids = _direct_user_ids(node)
            if direct_uids:
                uids |= direct_uids
            if not _passes_uid_filter(uids, direct_uids):
                return

            found_title = _find_ld_title(node)
            if found_title:
                title = found_title

            found_job_type = _find_job_type(node)
            if found_job_type:
                current_job_type = found_job_type

            direct_job_ids = _direct_job_ids(node)
            if direct_job_ids:
                job_ids |= direct_job_ids

            is_ld_job = bool(title and title_re.search(title))
            if is_ld_job and expected_job_type:
                type_text = (current_job_type or "").lower()
                is_ld_job = not type_text or expected_job_type in type_text

            if is_ld_job:
                label_counts = _extract_labeling_items(node)
                if label_counts:
                    label_counts.sort(key=lambda x: x[0], reverse=True)
                    best_count, best_src = label_counts[0]
                    _register_job(job_ids, title, current_job_type, best_count, best_src)
                else:
                    _register_job(job_ids, title, current_job_type)
                _register_records(job_ids, title, current_job_type, _direct_record_ids(node))

            for v in node.values():
                if isinstance(v, (dict, list)):
                    walk(v, title, current_job_type, job_ids, uids, depth + 1)
            return

        if isinstance(node, list):
            for item in node[:5000]:
                walk(item, title, current_job_type, job_ids, uids, depth + 1)

    for doc in docs:
        if isinstance(doc, dict) and isinstance(doc.get("log"), dict):
            entries = doc.get("log", {}).get("entries") or []
            for ent in entries:
                if not isinstance(ent, dict):
                    continue
                for side in ("response", "request"):
                    part = ent.get(side)
                    if not isinstance(part, dict):
                        continue
                    content = part.get("content") if side == "response" else part.get("postData")
                    if not isinstance(content, dict):
                        continue
                    text = content.get("text")
                    if not isinstance(text, str) or not text.strip():
                        continue
                    if content.get("encoding") == "base64":
                        try:
                            text = base64.b64decode(text).decode("utf-8", errors="ignore")
                        except Exception:
                            pass
                    parsed = _safe_json_loads(text)
                    if parsed is not None:
                        walk(parsed)
        else:
            walk(doc)

    job_list = sorted(jobs.values(), key=lambda x: (str(x.get("title", "")), str(x.get("job_id", ""))))
    for item in job_list:
        item["record_ids"] = sorted(set(str(x) for x in item.get("record_ids", [])))
    record_list = sorted(records.values(), key=lambda x: (str(x.get("job_id", "")), str(x.get("task_id", ""))))
    label_items_total = sum(
        int(j.get("labeling_items"))
        for j in job_list
        if isinstance(j.get("labeling_items"), int)
    )
    jobs_with_label_items = sum(1 for j in job_list if isinstance(j.get("labeling_items"), int))
    return {
        "jobs": job_list,
        "records": record_list,
        "job_ids": sorted({str(j.get("job_id")) for j in job_list if j.get("job_id")}),
        "record_ids": sorted({str(r.get("task_id")) for r in record_list if r.get("task_id")}),
        "labeling_items_total": label_items_total,
        "jobs_with_labeling_items": jobs_with_label_items,
    }


def _extract_reviews_from_payload(payload: object) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("reviews"), list):
        return [x for x in data.get("reviews") if isinstance(x, dict)]
    if isinstance(payload.get("reviews"), list):
        return [x for x in payload.get("reviews") if isinstance(x, dict)]
    return []


def _extract_audit_ids_from_node(node: object, depth: int = 0, max_depth: int = 8) -> set[str]:
    out: set[str] = set()
    if depth > max_depth or node is None:
        return out
    if isinstance(node, str):
        for m in AUDIT_ID_RE.findall(node):
            out.add(m)
        return out
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(k, str):
                for m in AUDIT_ID_RE.findall(k):
                    out.add(m)
            out |= _extract_audit_ids_from_node(v, depth + 1, max_depth)
        return out
    if isinstance(node, list):
        for item in node[:3000]:
            out |= _extract_audit_ids_from_node(item, depth + 1, max_depth)
        return out
    return out


def _extract_issue_bucket_from_reviews(reviews: list[dict]) -> dict[str, list]:
    qa_comments: list[str] = []
    issue_comments: list[dict] = []

    for rv in reviews:
        if not isinstance(rv, dict):
            continue
        rv_comments = _extract_direct_comments(rv)
        if rv_comments:
            qa_comments.extend(rv_comments)

        items = rv.get("items")
        if not isinstance(items, list):
            continue
        for item in items[:5000]:
            if not isinstance(item, dict):
                continue
            issue_types = []
            raw_type = item.get("type")
            if isinstance(raw_type, str):
                t = _normalize_comment_text(raw_type)
                if t:
                    issue_types.append(t)
            elif isinstance(raw_type, list):
                for it in raw_type[:20]:
                    if isinstance(it, str):
                        t = _normalize_comment_text(it)
                        if t:
                            issue_types.append(t)
            fallback_issue = _extract_direct_issue_type(item)
            if fallback_issue:
                issue_types.append(fallback_issue)
            issue_types = _dedup_texts(issue_types, max_items=20) or ["unknown"]

            item_comments = _extract_direct_comments(item)
            if item_comments:
                qa_comments.extend(item_comments)

            clean_item_comments = _dedup_texts(item_comments, max_items=20)
            if clean_item_comments:
                for itype in issue_types:
                    for c in clean_item_comments:
                        issue_comments.append({"issue_type": itype, "comment": c})
            else:
                for itype in issue_types:
                    issue_comments.append({"issue_type": itype, "comment": ""})

    qa_comments = _dedup_texts(qa_comments, max_items=20)
    dedup_issue = []
    seen = set()
    for item in issue_comments:
        if not isinstance(item, dict):
            continue
        itype = _normalize_comment_text(item.get("issue_type")) or "unknown"
        cmt = _normalize_comment_text(item.get("comment")) or ""
        key = (itype.lower(), cmt.lower())
        if key in seen:
            continue
        seen.add(key)
        dedup_issue.append({"issue_type": itype, "comment": cmt})
        if len(dedup_issue) >= 80:
            break

    return {"qa_comments": qa_comments, "issue_comments": dedup_issue}


def _merge_task_comment_maps(*maps: dict[str, dict]) -> dict[str, dict]:
    merged: dict[str, dict] = {}
    for m in maps:
        if not isinstance(m, dict):
            continue
        for tid, bucket in m.items():
            if not isinstance(bucket, dict):
                continue
            out = merged.setdefault(str(tid), {"qa_comments": [], "issue_comments": []})
            out["qa_comments"].extend(bucket.get("qa_comments") or [])
            out["issue_comments"].extend(bucket.get("issue_comments") or [])

    for tid, bucket in merged.items():
        bucket["qa_comments"] = _dedup_texts(bucket.get("qa_comments") or [], max_items=30)
        dedup_issue = []
        seen = set()
        for item in bucket.get("issue_comments") or []:
            if not isinstance(item, dict):
                continue
            itype = _normalize_comment_text(item.get("issue_type")) or "unknown"
            cmt = _normalize_comment_text(item.get("comment")) or ""
            key = (itype.lower(), cmt.lower())
            if key in seen:
                continue
            seen.add(key)
            dedup_issue.append({"issue_type": itype, "comment": cmt})
            if len(dedup_issue) >= 80:
                break
        bucket["issue_comments"] = dedup_issue
    return merged


def _history_records_hint(ld_history: dict) -> int | None:
    if not isinstance(ld_history, dict):
        return None
    record_ids = ld_history.get("record_ids") or []
    if record_ids:
        return len(record_ids)
    total_items = ld_history.get("labeling_items_total")
    if isinstance(total_items, (int, float)) and int(total_items) > 0:
        return int(total_items)
    return None


def _build_audit_id_candidates(meta: dict) -> list[str]:
    job_id = str(meta.get("job_id", ""))
    task_id = str(meta.get("task_id", ""))
    date_str = str(meta.get("date", "")).strip()
    ts = int(meta.get("timestamp", 0) or 0)
    out = []

    def add(token: str):
        token = token.strip()
        if token:
            out.append(f"{job_id}.{task_id}_{token}")

    if len(date_str) == 8 and date_str.isdigit():
        y, m, d = date_str[:4], date_str[4:6], date_str[6:8]
        add(date_str)
        add(f"{y}-{m}-{d}")
        add(f"{y}-{m}-{d}T000000")

    if ts > 1_000_000_000:
        try:
            dt_utc = datetime.utcfromtimestamp(ts)
            add(dt_utc.strftime("%Y-%m-%dT%H%M%S"))
            add(dt_utc.strftime("%Y-%m-%dT%H%M"))
            add(dt_utc.strftime("%Y%m%dT%H%M%S"))
            add(dt_utc.strftime("%Y%m%dT%H%M"))
        except Exception:
            pass

    out.append(f"{job_id}.{task_id}")
    return list(dict.fromkeys(o for o in out if o))


def _collect_target_tasks_from_files(files_with_content: list[dict],
                                     target_uid: str,
                                     target_job_ids: set[str] | None = None,
                                     target_task_ids: set[str] | None = None) -> list[dict]:
    parsed = []
    for item in files_with_content:
        p = parse_stats_key(item.get("key", ""))
        if p:
            parsed.append(p)
    target_task_ids = {str(t) for t in (target_task_ids or set()) if str(t).strip()}
    if target_job_ids or target_task_ids:
        labels = [
            p for p in parsed
            if p["type"] in ("LABELING", "REWORK")
            and p["user_id"] == target_uid
            and (
                (target_job_ids and p["job_id"] in target_job_ids)
                or (target_task_ids and p["task_id"] in target_task_ids)
            )
        ]
    else:
        labels = [
            p for p in parsed
            if p["type"] in ("LABELING", "REWORK") and p["user_id"] == target_uid
        ]
    by_task = {}
    for p in labels:
        tk = (p["job_id"], p["task_id"])
        cur = by_task.get(tk)
        if cur is None or p["type"] == "LABELING":
            by_task[tk] = p
    return list(by_task.values())


def _task_metas_from_ld_history(ld_history: dict) -> list[dict]:
    out = []
    for item in ld_history.get("records") or []:
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("task_id") or item.get("record_id") or "").strip()
        job_id = str(item.get("job_id") or "").strip()
        if not task_id:
            continue
        out.append({
            "job_id": job_id,
            "task_id": task_id,
            "type": "LABELING",
            "user_id": "",
            "hash": "",
        })
    seen = set()
    deduped = []
    for meta in out:
        key = (meta.get("job_id"), meta.get("task_id"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(meta)
    return deduped


def fetch_task_comments_live(session: requests.Session,
                             task_metas: list[dict],
                             target_uid: str,
                             endpoint_paths: list[str] | None = None,
                             workers: int = 6,
                             timeout_sec: float = 10.0,
                             debug: bool = False) -> dict[str, dict]:
    """
    Try to auto-fetch review detail (issue type/comment) directly from platform APIs.
    This is best-effort because deployments may use slightly different routes/params.
    """
    if not task_metas:
        return {}

    base_headers = dict(session.headers)
    base_cookies = session.cookies.get_dict()
    default_paths = [
        "/appen/backend/reviews/detail",
        "/appen/backend/review/detail",
        "/appen/backend/reviews",
        "/appen/backend/review",
        "/appen/backend/project/reviews/detail",
        "/appen/backend/project/review/detail",
        "/appen/backend/project/reviews",
        "/appen/backend/project/review",
        "/appen/ui/backend/reviews/detail",
        "/appen/ui/backend/review/detail",
        "/appen/ui/backend/reviews",
        "/appen/ui/backend/review",
        "/api/label/appen/v1/reviews",
        "/appen/label/appen/v1/reviews",
    ]
    paths = []
    for p in (endpoint_paths or []) + default_paths:
        if not p:
            continue
        pp = p.strip()
        if not pp:
            continue
        if not pp.startswith("http://") and not pp.startswith("https://") and not pp.startswith("/"):
            pp = "/" + pp
        if pp not in paths:
            paths.append(pp)
    debug_targets = {str(m.get("task_id", "")) for m in task_metas[:3]}

    def _iter_calls(meta: dict):
        job_id = str(meta.get("job_id", ""))
        task_id = str(meta.get("task_id", ""))
        uid = str(target_uid or "").strip()
        base_params = {"jobId": job_id, "taskId": task_id}
        if uid:
            base_params["userId"] = uid
        for path in paths:
            yield path, base_params
        for audit_id in _build_audit_id_candidates(meta):
            for path in paths:
                yield path, {"auditId": audit_id}
            # OpenAPI fallback discovered in reviews.json.
            yield "/api/label/appen/v1/reviews", {"uuid": audit_id, "name": "review"}

    init_paths = [
        "/appen/backend/task-init",
        "/appen/backend/task_init",
        "/api/label/appen/v1/task-init",
        "/api/label/appen/v1/task_init",
        "/appen/backend/result_url",
        "/appen/backend/review/result_url",
    ]

    def _collect_live_audit_ids(meta: dict, dbg_cb=None) -> set[str]:
        job_id = str(meta.get("job_id", ""))
        task_id = str(meta.get("task_id", ""))
        uid = str(target_uid or "").strip()
        audit_ids = set(_build_audit_id_candidates(meta))
        param_candidates = [
            {"jobId": job_id, "taskId": task_id, "jobType": "QA", "allowQAEdit": "true"},
            {"jobId": job_id, "taskId": task_id, "jobType": "REWORK", "allowQAEdit": "true"},
            {"jobId": job_id, "taskId": task_id},
            {"jobId": job_id, "taskId": task_id, "userId": uid} if uid else {"jobId": job_id, "taskId": task_id},
            {"jobId": job_id, "jobType": "QA", "allowQAEdit": "true"},
        ]
        for path in init_paths:
            url = f"{BASE_URL}{path}"
            for params in param_candidates:
                try:
                    r = requests.get(
                        url,
                        params=params,
                        headers=base_headers,
                        cookies=base_cookies,
                        timeout=max(2.0, float(timeout_sec)),
                    )
                    if not r.ok:
                        if dbg_cb:
                            dbg_cb(f"{path} init -> HTTP {r.status_code}")
                        continue
                    txt = (r.text or "").strip()
                    if not txt:
                        if dbg_cb:
                            dbg_cb(f"{path} init -> empty")
                        continue
                    payload = _safe_json_loads(txt)
                    if payload is None:
                        # Some endpoints may return plain text containing auditId.
                        audit_ids |= set(AUDIT_ID_RE.findall(txt))
                        if dbg_cb and AUDIT_ID_RE.search(txt):
                            dbg_cb(f"{path} init -> auditId in text")
                        continue
                    audit_ids |= _extract_audit_ids_from_node(payload)
                    if dbg_cb:
                        dbg_cb(f"{path} init -> ok")
                except Exception:
                    if dbg_cb:
                        dbg_cb(f"{path} init -> exception")
                    continue
        return audit_ids

    def _one_task(meta: dict):
        task_id = str(meta.get("task_id", ""))
        if not task_id:
            return task_id, None
        dbg: list[str] = []

        def _dbg(msg: str):
            if debug and task_id in debug_targets and len(dbg) < 15:
                dbg.append(msg)

        live_audit_ids = _collect_live_audit_ids(meta, dbg_cb=_dbg)
        _dbg(f"audit_candidates={len(live_audit_ids)}")
        for audit_id in list(live_audit_ids):
            for path in paths:
                url = path if path.startswith("http://") or path.startswith("https://") else f"{BASE_URL}{path}"
                try:
                    r = requests.get(
                        url,
                        params={"auditId": audit_id},
                        headers=base_headers,
                        cookies=base_cookies,
                        timeout=max(2.0, float(timeout_sec)),
                    )
                    if not r.ok:
                        _dbg(f"{path}?auditId=... -> HTTP {r.status_code}")
                        continue
                    txt = (r.text or "").strip()
                    if not txt or (txt and not txt.startswith("{")):
                        _dbg(f"{path}?auditId=... -> non-json/empty")
                        continue
                    payload = _safe_json_loads(txt)
                    if payload is None:
                        _dbg(f"{path}?auditId=... -> json parse fail")
                        continue
                    reviews = _extract_reviews_from_payload(payload)
                    if not reviews:
                        _dbg(f"{path}?auditId=... -> no reviews")
                        continue
                    bucket = _extract_issue_bucket_from_reviews(reviews)
                    if bucket.get("qa_comments") or bucket.get("issue_comments"):
                        return task_id, bucket
                except Exception:
                    _dbg(f"{path}?auditId=... -> exception")
                    continue

            # OpenAPI fallback discovered in reviews.json.
            try:
                r = requests.get(
                    f"{BASE_URL}/api/label/appen/v1/reviews",
                    params={"uuid": audit_id, "name": "review"},
                    headers=base_headers,
                    cookies=base_cookies,
                    timeout=max(2.0, float(timeout_sec)),
                )
                if r.ok:
                    payload = _safe_json_loads((r.text or "").strip())
                    if payload is not None:
                        reviews = _extract_reviews_from_payload(payload)
                        if reviews:
                            bucket = _extract_issue_bucket_from_reviews(reviews)
                            if bucket.get("qa_comments") or bucket.get("issue_comments"):
                                return task_id, bucket
                    else:
                        _dbg("/api/label/appen/v1/reviews -> parse fail")
                else:
                    _dbg(f"/api/label/appen/v1/reviews -> HTTP {r.status_code}")
            except Exception:
                _dbg("/api/label/appen/v1/reviews -> exception")
                pass

        for path, params in _iter_calls(meta):
            url = path if path.startswith("http://") or path.startswith("https://") else f"{BASE_URL}{path}"
            try:
                r = requests.get(
                    url,
                    params=params,
                    headers=base_headers,
                    cookies=base_cookies,
                    timeout=max(2.0, float(timeout_sec)),
                )
                if not r.ok:
                    _dbg(f"{path} -> HTTP {r.status_code}")
                    continue
                txt = (r.text or "").strip()
                if not txt or (txt and not txt.startswith("{")):
                    _dbg(f"{path} -> non-json/empty")
                    continue
                payload = _safe_json_loads(txt)
                if payload is None:
                    _dbg(f"{path} -> json parse fail")
                    continue
                reviews = _extract_reviews_from_payload(payload)
                if not reviews:
                    _dbg(f"{path} -> no reviews")
                    continue
                bucket = _extract_issue_bucket_from_reviews(reviews)
                if bucket.get("qa_comments") or bucket.get("issue_comments"):
                    return task_id, bucket
            except Exception:
                _dbg(f"{path} -> exception")
                continue
        if debug and task_id in debug_targets and dbg:
            print(f"      debug task {task_id}: " + " | ".join(dbg[:10]))
        return task_id, None

    workers = max(1, min(int(workers or 1), 32, len(task_metas)))
    out: dict[str, dict] = {}
    checked = 0
    print(f"   🔎 Auto review-detail fetch: {len(task_metas)} tasks | workers={workers}")
    with ThreadPoolExecutor(max_workers=workers) as ex:
        fut_map = {ex.submit(_one_task, meta): meta for meta in task_metas}
        for fut in as_completed(fut_map):
            checked += 1
            task_id, bucket = fut.result()
            if task_id and isinstance(bucket, dict):
                out[task_id] = bucket
            if checked % 20 == 0 or checked == len(task_metas):
                print(f"      {checked}/{len(task_metas)} tasks checked, {len(out)} tasks enriched")
    return out

# ─── HTTP SESSION ───────────────────────────────────────────────
def _parse_cookie_and_auth(raw: str) -> tuple[str, str]:
    """
    Parse cookie/auth text from a file or input string.
    Accepts lines like:
      Cookie: a=b; c=d
      Authorization: <token>
      Authorization=<token>
    Returns (cookie_str, auth_header_value).
    """
    if not raw:
        return "", ""

    cookie_parts: list[str] = []
    auth_header = ""
    for line in raw.splitlines():
        s = line.strip()
        if not s:
            continue
        lower = s.lower()
        if lower.startswith("authorization"):
            if ":" in s:
                auth_header = s.split(":", 1)[1].strip()
            elif "=" in s:
                auth_header = s.split("=", 1)[1].strip()
            else:
                auth_header = s[len("authorization"):].strip()
            continue
        if lower.startswith("cookie"):
            if ":" in s:
                cookie_parts.append(s.split(":", 1)[1].strip())
            elif "=" in s:
                cookie_parts.append(s.split("=", 1)[1].strip())
            continue
        # Ignore common non-cookie headers if user pasted full request headers
        if lower.startswith(("connection", "host", "pragma", "referer", "user-agent", "accept", "accept-language")):
            continue
        if "=" in s and "authorization" not in lower:
            cookie_parts.append(s)

    cookie_str = "; ".join(p for p in cookie_parts if p)
    if auth_header and " " not in auth_header:
        # Auto add Bearer for JWT-like tokens
        if auth_header.count(".") >= 2:
            auth_header = f"Bearer {auth_header}"
    return cookie_str, auth_header


def make_session(cookie_str: str | None = None,
                 host_header: str | None = None,
                 auth_header: str | None = None) -> requests.Session:
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=0.5,
                  status_forcelist=[429, 500, 502, 503, 504])
    s.mount("http://", HTTPAdapter(max_retries=retry))
    referer_base = f"http://{host_header}" if host_header else BASE_URL
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        "Accept": "application/json, */*",
        "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
        "Referer": f"{referer_base}/appen/ui",
    })
    if host_header:
        s.headers["Host"] = host_header
    if cookie_str:
        for part in cookie_str.split(";"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                s.cookies.set(k.strip(), v.strip())
    if auth_header:
        s.headers["Authorization"] = auth_header
    return s


def try_login(session: requests.Session, username: str, password: str) -> bool:
    """Thử login nếu chưa có session."""
    try:
        r = session.post(LOGIN_URL, json={"username": username, "password": password},
                         timeout=10)
        if r.ok:
            data = r.json()
            if data.get("code") == 0 or data.get("success"):
                print("✅ Login thành công")
                return True
        print(f"⚠️  Login trả: {r.status_code} — {r.text[:100]}")
        return False
    except Exception as e:
        print(f"⚠️  Login lỗi: {e}")
        return False


# ─── S3 LISTING — FULL PAGINATION ──────────────────────────────
def list_all_files(session: requests.Session, hash_id: str,
                   sub_prefix: str = "statistics.review.",
                   max_pages: int = 500) -> list[str]:
    """
    List TẤT CẢ files với prefix = {hash_id}/{sub_prefix}.
    Phân trang bằng NextMarker đến khi IsTruncated = false.
    """
    if not max_pages or max_pages < 0:
        max_pages = 10**9
    all_keys = []
    marker = ""
    prefix = f"{hash_id}/{sub_prefix}"

    expected_prefix = prefix
    seeked_to_hash = False
    marker_mode = False
    for page in range(1, max_pages + 1):
        # Keep slash in prefix unescaped to match browser behavior:
        # prefix=<hash>/statistics.review. (NOT %2F)
        if marker_mode:
            # Fallback mode when gateway ignores prefix: drive by marker only.
            url = f"{S3_BASE}/object_content?list=true&maxKeys=1000"
        else:
            url = f"{S3_BASE}/object_content?list=true&prefix={prefix}&maxKeys=1000"
        if marker:
            # Some gateways are picky: keep "/" unescaped in marker.
            url += f"&marker={quote(marker, safe='/')}"

        try:
            r = session.get(url, timeout=30)
            if r.status_code == 401:
                print("❌ HTTP 401 — Cookie hết hạn hoặc sai. Cần login lại.")
                return all_keys
            if not r.ok:
                print(f"⚠️  List page {page}: HTTP {r.status_code}")
                break

            # Handle JSON hoặc XML
            text = r.text.strip()
            if text.startswith("{"):
                data = r.json()
                contents = data.get("Contents", [])
                is_truncated = data.get("IsTruncated", False)
                next_marker = data.get("NextMarker", "")
            elif text.startswith("<"):
                # XML response
                import xml.etree.ElementTree as ET
                ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
                try:
                    root = ET.fromstring(text)
                    # Try with namespace
                    keys_el = root.findall(".//s3:Key", ns) or root.findall(".//Key")
                    contents = [{"Key": el.text} for el in keys_el if el.text]
                    trunc_el = root.find(".//s3:IsTruncated", ns) or root.find(".//IsTruncated")
                    is_truncated = trunc_el is not None and trunc_el.text.lower() == "true"
                    nm_el = root.find(".//s3:NextMarker", ns) or root.find(".//NextMarker")
                    next_marker = nm_el.text if nm_el is not None else ""
                except ET.ParseError:
                    # Fallback: regex
                    keys = re.findall(r"<Key>([^<]+)</Key>", text)
                    contents = [{"Key": k} for k in keys]
                    is_truncated = "<IsTruncated>true</IsTruncated>" in text
                    nm = re.search(r"<NextMarker>([^<]+)</NextMarker>", text)
                    next_marker = nm.group(1) if nm else ""
            else:
                print(f"⚠️  Unexpected response format on page {page}")
                break

            if marker_mode and contents:
                first_key = (contents[0] or {}).get("Key", "")
                if first_key and first_key < expected_prefix:
                    print(f"   ⚠️ Marker bị ignore (first key: {first_key[:48]}...)")
                    print("   ↪ Dừng sớm; endpoint list không hỗ trợ nhảy tới hash này.")
                    break

            for item in contents:
                key = item.get("Key", "")
                if key and key.startswith(expected_prefix):
                    all_keys.append(key)

            # Some gateways ignore prefix and return bucket-global pages.
            # If first page clearly points to another hash, jump marker to target hash.
            if page == 1 and not all_keys and contents:
                first_key = (contents[0] or {}).get("Key", "")
                if first_key and not first_key.startswith(expected_prefix) and not seeked_to_hash:
                    print(f"   ⚠️ Prefix có vẻ bị bỏ qua (first key: {first_key[:48]}...)")
                    print(f"   ↪ Chuyển marker-mode từ: {expected_prefix}")
                    marker = expected_prefix
                    seeked_to_hash = True
                    marker_mode = True
                    continue

            if page % 5 == 0:
                print(f"   Page {page}: {len(all_keys)} keys total")

            if not is_truncated or not next_marker:
                print(f"   ✅ Done listing — {page} pages, {len(all_keys)} files")
                break
            marker = next_marker
            time.sleep(0.05)  # rate limit

        except Exception as e:
            print(f"⚠️  List page {page} error: {e}")
            break

    return all_keys


def list_global_statistics_keys(session: requests.Session,
                                max_pages: int = 500,
                                start_marker: str = "") -> list[str]:
    """
    List statistics.review keys ở phạm vi global bucket (không theo hash prefix).
    Dùng khi gateway bỏ qua prefix filter.
    """
    if not max_pages or max_pages < 0:
        max_pages = 10**9
    all_keys = []
    marker = start_marker or ""
    for page in range(1, max_pages + 1):
        url = f"{S3_BASE}/object_content?list=true&maxKeys=1000"
        if marker:
            # Keep "/" unescaped to avoid marker being ignored by some gateways.
            url += f"&marker={quote(marker, safe='/')}"

        try:
            r = session.get(url, timeout=30)
            if r.status_code == 401:
                print("❌ HTTP 401 — Cookie hết hạn hoặc sai. Cần login lại.")
                return all_keys
            if not r.ok:
                print(f"⚠️  Global list page {page}: HTTP {r.status_code}")
                break

            text = r.text.strip()
            if text.startswith("{"):
                data = r.json()
                contents = data.get("Contents", [])
                is_truncated = data.get("IsTruncated", False)
                next_marker = data.get("NextMarker", "")
            elif text.startswith("<"):
                import xml.etree.ElementTree as ET
                ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
                try:
                    root = ET.fromstring(text)
                    keys_el = root.findall(".//s3:Key", ns) or root.findall(".//Key")
                    contents = [{"Key": el.text} for el in keys_el if el.text]
                    trunc_el = root.find(".//s3:IsTruncated", ns) or root.find(".//IsTruncated")
                    is_truncated = trunc_el is not None and trunc_el.text.lower() == "true"
                    nm_el = root.find(".//s3:NextMarker", ns) or root.find(".//NextMarker")
                    next_marker = nm_el.text if nm_el is not None else ""
                except ET.ParseError:
                    keys = re.findall(r"<Key>([^<]+)</Key>", text)
                    contents = [{"Key": k} for k in keys]
                    is_truncated = "<IsTruncated>true</IsTruncated>" in text
                    nm = re.search(r"<NextMarker>([^<]+)</NextMarker>", text)
                    next_marker = nm.group(1) if nm else ""
            else:
                print(f"⚠️  Unexpected response format on global page {page}")
                break

            for item in contents:
                key = item.get("Key", "")
                if not key:
                    continue
                if "/statistics.review." in key or key.endswith(".statistics.json"):
                    all_keys.append(key)

            if page == 1 and marker and contents:
                first_key = (contents[0] or {}).get("Key", "")
                if first_key and first_key < marker:
                    print(f"   ⚠️ Marker có vẻ bị ignore (first key: {first_key[:48]}...)")
                    print("   ↪ Dừng sớm; hãy dùng --key-index-file hoặc --known-key.")
                    break

            if page % 5 == 0:
                print(f"   Global page {page}: {len(all_keys)} statistics keys")

            if not is_truncated or not next_marker:
                print(f"   ✅ Done global listing — {page} pages, {len(all_keys)} statistics keys")
                break
            marker = next_marker
            time.sleep(0.05)

        except Exception as e:
            print(f"⚠️  Global list page {page} error: {e}")
            break

    return all_keys


def _parse_s3_list_response(text: str) -> tuple[list[dict], bool, str]:
    text = text.strip()
    if text.startswith("{"):
        data = json.loads(text)
        return (
            data.get("Contents", []) or [],
            bool(data.get("IsTruncated", False)),
            data.get("NextMarker", "") or "",
        )

    if text.startswith("<"):
        import xml.etree.ElementTree as ET
        ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
        try:
            root = ET.fromstring(text)
            keys_el = root.findall(".//s3:Key", ns) or root.findall(".//Key")
            contents = [{"Key": el.text} for el in keys_el if el.text]
            trunc_el = root.find(".//s3:IsTruncated", ns) or root.find(".//IsTruncated")
            is_truncated = trunc_el is not None and trunc_el.text.lower() == "true"
            nm_el = root.find(".//s3:NextMarker", ns) or root.find(".//NextMarker")
            next_marker = nm_el.text if nm_el is not None else ""
            return contents, is_truncated, next_marker
        except ET.ParseError:
            keys = re.findall(r"<Key>([^<]+)</Key>", text)
            contents = [{"Key": k} for k in keys]
            is_truncated = "<IsTruncated>true</IsTruncated>" in text
            nm = re.search(r"<NextMarker>([^<]+)</NextMarker>", text)
            next_marker = nm.group(1) if nm else ""
            return contents, is_truncated, next_marker

    raise ValueError("unexpected S3 list response format")


def _task_key(hash_id: str, task_id: str) -> str:
    return f"{hash_id}|{task_id}"


def _save_discover_checkpoint(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def crawl_global_for_user(session: requests.Session,
                          target_uid: str,
                          max_pages: int,
                          resume_file: str,
                          save_every: int = 10,
                          target_job_ids: set[str] | None = None,
                          target_task_ids: set[str] | None = None) -> dict:
    """
    Crawl bucket listing sequentially because this gateway ignores arbitrary
    prefix/marker jumps. The checkpoint stores only metadata needed to resume
    and to match QA files once a user task is discovered.
    """
    if not max_pages or max_pages < 0:
        max_pages = 10**9
    path = Path(resume_file)
    if path.exists():
        state = json.loads(path.read_text(encoding="utf-8"))
        print(f"   Resume checkpoint: {path}")
        print(f"   Đã quét trước đó: {state.get('pages_scanned', 0)} pages")
    else:
        state = {
            "target_user_id": target_uid,
            "marker": "",
            "pages_scanned": 0,
            "stats_seen": 0,
            "done": False,
            "type_dist": {},
            "user_labeling": {},
            "review_by_task": {},
        }

    if state.get("target_user_id") and str(state["target_user_id"]) != str(target_uid):
        raise ValueError(
            f"Checkpoint user_id={state['target_user_id']} khác --user-id={target_uid}"
        )
    state["target_user_id"] = target_uid
    job_mode = bool(target_job_ids)
    task_mode = bool(target_task_ids)
    if job_mode:
        target_job_ids = {str(j) for j in target_job_ids if str(j).strip()}
        state_job_ids = set(str(j) for j in state.get("target_job_ids", []))
        if state_job_ids and state_job_ids != target_job_ids:
            raise ValueError("Checkpoint job_ids khác với job_ids hiện tại")
        state["target_job_ids"] = sorted(target_job_ids)
    if task_mode:
        target_task_ids = {str(t) for t in target_task_ids if str(t).strip()}
        state_task_ids = set(str(t) for t in state.get("target_task_ids", []))
        if state_task_ids and state_task_ids != target_task_ids:
            raise ValueError("Checkpoint task_ids khác với task_ids hiện tại")
        state["target_task_ids"] = sorted(target_task_ids)

    # Legacy checkpoint compacting: old format stored all review keys and became huge.
    if not state.get("user_labeling") and state.get("review_by_task"):
        state["review_by_task"] = {}

    marker = state.get("marker", "") or ""
    pages_this_run = 0
    if state.get("done"):
        print("   Checkpoint đã đánh dấu done; dùng index đã lưu để fetch.")
    else:
        print(f"   Crawl index tuần tự tối đa {max_pages} pages trong lần chạy này...")

    while not state.get("done") and pages_this_run < max_pages:
        url = f"{S3_BASE}/object_content?list=true&maxKeys=1000"
        if marker:
            url += f"&marker={quote(marker, safe='/')}"

        try:
            r = session.get(url, timeout=30)
            if r.status_code == 401:
                print("❌ HTTP 401 — Cookie hết hạn hoặc sai. Cần login lại.")
                break
            if not r.ok:
                print(f"⚠️  Crawl page error: HTTP {r.status_code}")
                break

            contents, is_truncated, next_marker = _parse_s3_list_response(r.text)
        except Exception as e:
            print(f"⚠️  Crawl error: {e}")
            break

        last_seen_key = ""
        tracked_tasks = {
            _task_key(p["hash"], p["task_id"])
            for p in state.get("user_labeling", {}).values()
        }
        for item in contents:
            key = item.get("Key", "")
            if key:
                last_seen_key = key
            p = parse_stats_key(key)
            if not p:
                continue

            state["stats_seen"] = int(state.get("stats_seen", 0)) + 1
            type_dist = state.setdefault("type_dist", {})
            type_dist[p["type"]] = int(type_dist.get(p["type"], 0)) + 1

            tk = _task_key(p["hash"], p["task_id"])
            is_target_label = False
            if job_mode or task_mode:
                if (
                    p["type"] in ("LABELING", "REWORK")
                    and p["user_id"] == target_uid
                    and (
                        (job_mode and p["job_id"] in target_job_ids)
                        or (task_mode and p["task_id"] in target_task_ids)
                    )
                ):
                    is_target_label = True
            else:
                if p["user_id"] == target_uid and p["type"] in ("LABELING", "REWORK"):
                    is_target_label = True

            if is_target_label:
                state.setdefault("user_labeling", {})[p["key"]] = p
                tracked_tasks.add(tk)

            if p["type"] in REVIEW_TYPES and tk in tracked_tasks:
                bucket = state.setdefault("review_by_task", {}).setdefault(tk, {})
                bucket[p["key"]] = p

        pages_this_run += 1
        state["pages_scanned"] = int(state.get("pages_scanned", 0)) + 1
        state["marker"] = next_marker
        if last_seen_key:
            state["last_seen_key"] = last_seen_key

        if not is_truncated or not next_marker:
            state["done"] = True

        if pages_this_run % 5 == 0 or state["done"]:
            user_tasks = {
                _task_key(p["hash"], p["task_id"])
                for p in state.get("user_labeling", {}).values()
            }
            print(
                f"   Crawl index page {state['pages_scanned']}: "
                f"stats={state.get('stats_seen', 0)} "
                f"user_files={len(state.get('user_labeling', {}))} "
                f"user_tasks={len(user_tasks)} "
                f"last={state.get('last_seen_key', '')[:44]}"
            )

        if pages_this_run % max(1, save_every) == 0 or state["done"]:
            _save_discover_checkpoint(path, state)

        if state["done"]:
            break
        marker = next_marker
        time.sleep(0.05)

    _save_discover_checkpoint(path, state)

    user_labeling = list(state.get("user_labeling", {}).values())
    user_task_ids = {_task_key(p["hash"], p["task_id"]) for p in user_labeling}
    qa_files = []
    for tk in user_task_ids:
        qa_files.extend((state.get("review_by_task", {}).get(tk) or {}).values())

    fetch_map = {}
    for f in user_labeling + qa_files:
        fetch_map[f["key"]] = f

    hashes = sorted({p["hash"] for p in user_labeling})
    return {
        "parsed": [],
        "type_dist": state.get("type_dist", {}),
        "user_labeling": user_labeling,
        "qa_files": qa_files,
        "to_fetch": list(fetch_map.values()),
        "hashes": hashes,
        "mode": "job_id" if job_mode else ("task_id" if task_mode else "user_id"),
        "done": bool(state.get("done")),
        "checkpoint": str(path),
        "pages_scanned": state.get("pages_scanned", 0),
    }


# ─── FETCH FILE CONTENT ─────────────────────────────────────────
def fetch_file_content(session: requests.Session, key: str,
                       retries: int = 2) -> dict | None:
    url = f"{S3_BASE}/object_content"
    for attempt in range(retries + 1):
        try:
            r = session.get(url, params={"fileName": key}, timeout=15)
            if r.ok:
                return r.json()
            if attempt < retries:
                time.sleep(0.5)
        except Exception:
            if attempt < retries:
                time.sleep(0.5)
    return None


def _fetch_file_content_with_copied_auth(
    key: str,
    base_headers: dict,
    base_cookies: dict,
    retries: int = 2,
) -> dict | None:
    """
    Thread-safe fetch helper: mỗi worker dùng request độc lập
    nhưng copy nguyên headers/cookies từ session gốc.
    """
    url = f"{S3_BASE}/object_content"
    for attempt in range(retries + 1):
        try:
            r = requests.get(
                url,
                params={"fileName": key},
                headers=base_headers,
                cookies=base_cookies,
                timeout=15,
            )
            if r.ok:
                return r.json()
            if attempt < retries:
                time.sleep(0.2 * (attempt + 1))
        except Exception:
            if attempt < retries:
                time.sleep(0.2 * (attempt + 1))
    return None


def load_known_keys(path: str) -> list[str]:
    """
    Đọc known keys từ file. Hỗ trợ:
      - text: mỗi dòng một key
      - JSON list: ["hash/statistics.review...json", {"Key": "..."}]
      - JSON object S3 list: {"Contents": [{"Key": "..."}]}
      - HAR/JSON/text bất kỳ có chứa key statistics
    """
    raw = Path(path).read_text(encoding="utf-8").strip()
    if not raw:
        return []

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        keys = STATS_KEY_RE.findall(raw)
        if keys:
            return list(dict.fromkeys(keys))
        return [line.strip() for line in raw.splitlines()
                if line.strip() and not line.lstrip().startswith("#")]

    def collect(obj) -> list[str]:
        found = []
        if isinstance(obj, dict):
            key = obj.get("Key") or obj.get("key")
            if isinstance(key, str):
                found.append(key)
            for value in obj.values():
                found.extend(collect(value))
        elif isinstance(obj, list):
            for item in obj:
                found.extend(collect(item))
        elif isinstance(obj, str):
            found.extend(STATS_KEY_RE.findall(obj))
        return found

    if isinstance(data, dict):
        if isinstance(data.get("Contents"), list):
            return [item["Key"] for item in data["Contents"]
                    if isinstance(item, dict) and item.get("Key")]
        if isinstance(data.get("keys"), list):
            return [str(k) for k in data["keys"] if k]
        return list(dict.fromkeys(collect(data)))

    if isinstance(data, list):
        keys = []
        for item in data:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                key = item.get("Key") or item.get("key")
                if key:
                    keys.append(key)
        if keys:
            return keys
        return list(dict.fromkeys(collect(data)))

    return []


def load_job_ids(path: str) -> list[str]:
    """
    Đọc job ids từ file. Hỗ trợ:
      - text: mỗi dòng một job_id
      - JSON bất kỳ có field jobId/job_id
    """
    raw = Path(path).read_text(encoding="utf-8").strip()
    if not raw:
        return []

    def _dedup(seq: list[str]) -> list[str]:
        return list(dict.fromkeys([x for x in seq if x]))

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        ids = []
        for line in raw.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            m = re.search(r"\b(\d{3,})\b", s)
            if m:
                ids.append(m.group(1))
        return _dedup(ids)

    def collect(obj) -> list[str]:
        found = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                kl = str(k).lower()
                if kl in ("jobid", "job_id"):
                    if isinstance(v, (int, float)):
                        found.append(str(int(v)))
                    elif isinstance(v, str):
                        m = re.search(r"\b(\d{3,})\b", v)
                        if m:
                            found.append(m.group(1))
                found.extend(collect(v))
        elif isinstance(obj, list):
            for item in obj:
                found.extend(collect(item))
        elif isinstance(obj, str):
            for m in re.findall(r'"job(?:_|)id"\s*:\s*"?(\d{3,})"?', obj, flags=re.IGNORECASE):
                found.append(m)
        return found

    ids = collect(data)
    if not ids and isinstance(data, list):
        for item in data:
            if isinstance(item, (int, float)):
                ids.append(str(int(item)))
            elif isinstance(item, str):
                m = re.search(r"\b(\d{3,})\b", item)
                if m:
                    ids.append(m.group(1))
    return _dedup(ids)


def _collect_job_ids_from_worker_jobs(results: list[dict]) -> list[str]:
    ids: list[str] = []
    for item in results or []:
        if not isinstance(item, dict):
            continue
        raw = item.get("jobId") or item.get("job_id")
        if raw is None:
            continue
        nid = _normalize_numeric_id(raw)
        if nid:
            ids.append(nid)
    return list(dict.fromkeys(ids))


def _collect_job_map_from_worker_jobs(results: list[dict]) -> dict[str, dict]:
    """Return map job_id -> {job_name, job_type} from worker-jobs payload."""
    out: dict[str, dict] = {}
    for item in results or []:
        if not isinstance(item, dict):
            continue
        job_id = _normalize_numeric_id(item.get("jobId") or item.get("job_id"))
        if not job_id:
            continue
        name = item.get("jobName") or item.get("job_name") or ""
        jtype = item.get("jobType") or item.get("job_type") or ""
        out[job_id] = {"job_name": str(name or ""), "job_type": str(jtype or "")}
    return out


def fetch_worker_jobs(session: requests.Session,
                      base_url: str,
                      page_size: int = DEFAULT_WORKER_PAGE_SIZE,
                      max_pages: int = 0,
                      job_status_list: list[str] | None = None,
                      status_list: list[str] | None = None,
                      job_name: str = "",
                      sort_by: str = DEFAULT_WORKER_SORT_BY) -> dict:
    job_status_list = list(job_status_list) if job_status_list else list(DEFAULT_WORKER_JOB_STATUS)
    status_list = list(status_list) if status_list else list(DEFAULT_WORKER_STATUS)
    base_url = base_url.strip().rstrip("/")
    endpoint = f"{base_url}/appen/backend/job/worker-jobs"

    all_results: list[dict] = []
    total_pages = None
    total_elements = None
    page_index = 0

    while True:
        params: list[tuple[str, str]] = []
        for v in job_status_list:
            params.append(("jobStatusList", v))
        for v in status_list:
            params.append(("statusList", v))
        params.append(("jobName", job_name))
        params.append(("sortBy", sort_by))
        params.append(("pageIndex", str(page_index)))
        params.append(("pageSize", str(page_size)))

        try:
            r = session.get(endpoint, params=params, timeout=20)
        except Exception as e:
            print(f"❌ worker-jobs request error: {e}")
            break

        if not r.ok:
            print(f"❌ worker-jobs HTTP {r.status_code}: {r.text[:300]}")
            break

        payload = _safe_json_loads(r.text or "")
        if not isinstance(payload, dict):
            print("❌ worker-jobs response không phải JSON object")
            break

        data = payload.get("data") or {}
        results = data.get("results") or []
        if total_pages is None:
            total_pages = data.get("totalPages")
            total_elements = data.get("totalElements")

        print(f"📄 Worker jobs page {page_index + 1}/{total_pages or '?'}: {len(results)} jobs")
        if results:
            all_results.extend([r for r in results if isinstance(r, dict)])

        page_index += 1
        if total_pages is not None and page_index >= int(total_pages):
            break
        if max_pages and page_index >= max_pages:
            break
        if not results:
            break

    return {
        "source": "worker-jobs",
        "fetched_at": datetime.now().isoformat(),
        "base_url": base_url,
        "totalPages": total_pages,
        "totalElements": total_elements,
        "pageSize": page_size,
        "jobStatusList": job_status_list,
        "statusList": status_list,
        "results": all_results,
    }


def fetch_history_pages(session: requests.Session, base_url: str,
                        page_size: int = 100, max_pages: int = 0) -> dict:
    """
    Try to fetch paginated history/review pages from several known endpoints.
    Returns aggregated JSON object with 'pages' list of raw responses.
    """
    candidates = [
        "/appen/ui/backend/history",
        "/appen/backend/history",
        "/appen/ui/backend/reviews",
        "/appen/backend/reviews",
        "/appen/backend/review/list",
        "/appen/backend/tasks/history",
        "/appen/backend/task/history",
        "/appen/backend/tasks/list",
        "/appen/backend/task/list",
        "/appen/backend/task/record/list",
        "/appen/backend/task/records",
        "/appen/backend/record/list",
        "/appen/ui/backend/tasks/history",
        "/appen/ui/backend/task/history",
        "/appen/ui/backend/tasks/list",
        "/appen/ui/backend/task/list",
        "/appen/ui/backend/task/record/list",
        "/api/label/appen/v1/reviews",
        "/api/label/appen/v1/task/history",
        "/api/label/appen/v1/task/list",
    ]
    out_pages: list[dict] = []
    base = base_url.rstrip("/")

    def _extract_results(payload: object) -> list | None:
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            data = payload.get("data")
            for container in (data, payload):
                if isinstance(container, dict):
                    for key in ("results", "items", "records", "recordList", "list", "rows"):
                        val = container.get(key)
                        if isinstance(val, list):
                            return val
                if isinstance(container, list):
                    return container
        return None

    param_templates = [
        ("GET", {"pageIndex": "{page}", "pageSize": "{size}"}),
        ("GET", {"page": "{page}", "pageSize": "{size}"}),
        ("GET", {"pageNum": "{page1}", "pageSize": "{size}"}),
        ("GET", {"pageNo": "{page1}", "pageSize": "{size}"}),
        ("GET", {"current": "{page1}", "pageSize": "{size}"}),
        ("GET", {"offset": "{offset}", "limit": "{size}"}),
        ("POST", {"pageIndex": "{page}", "pageSize": "{size}"}),
        ("POST", {"page": "{page}", "pageSize": "{size}"}),
        ("POST", {"pageNum": "{page1}", "pageSize": "{size}"}),
        ("POST", {"pageNo": "{page1}", "pageSize": "{size}"}),
        ("POST", {"current": "{page1}", "pageSize": "{size}"}),
        ("POST", {"offset": "{offset}", "limit": "{size}"}),
    ]

    def _build_params(tpl: dict, page_index: int) -> dict:
        params = {}
        for k, v in tpl.items():
            if v == "{page}":
                params[k] = page_index
            elif v == "{page1}":
                params[k] = page_index + 1
            elif v == "{size}":
                params[k] = page_size
            elif v == "{offset}":
                params[k] = page_index * page_size
            else:
                params[k] = v
        return params

    for ep in candidates:
        if ep.startswith("http://") or ep.startswith("https://"):
            url = ep
        else:
            url = f"{base}{ep}"
        print(f"📡 Try history endpoint: {url}")

        working = None
        for method, tpl in param_templates:
            params = _build_params(tpl, 0)
            try:
                if method == "POST":
                    r = session.post(url, json=params, timeout=15)
                else:
                    r = session.get(url, params=params, timeout=15)
            except Exception:
                continue
            if not r.ok:
                continue
            try:
                payload = r.json()
            except Exception:
                continue
            results = _extract_results(payload)
            if results is not None:
                working = (method, tpl)
                out_pages.append({
                    "endpoint": url,
                    "pageIndex": 0,
                    "method": method,
                    "params": params,
                    "payload": payload,
                })
                break

        if not working:
            print("   ↪ Không tìm được params phù hợp")
            continue

        method, tpl = working
        page_index = 1
        total_pages = None
        empty_streak = 0

        while True:
            params = _build_params(tpl, page_index)
            try:
                if method == "POST":
                    r = session.post(url, json=params, timeout=15)
                else:
                    r = session.get(url, params=params, timeout=15)
            except Exception as e:
                print(f"   ↪ request error: {e}")
                break
            if not r.ok:
                print(f"   ↪ HTTP {r.status_code}")
                break
            try:
                payload = r.json()
            except Exception:
                print("   ↪ non-json response; stop this endpoint")
                break
            out_pages.append({
                "endpoint": url,
                "pageIndex": page_index,
                "method": method,
                "params": params,
                "payload": payload,
            })

            results = _extract_results(payload)
            if results is None:
                if page_index > 2:
                    break
                page_index += 1
                continue

            if not results:
                empty_streak += 1
            else:
                empty_streak = 0

            data = payload if isinstance(payload, dict) else {}
            total_pages = (
                data.get("totalPages")
                or data.get("pages")
                or data.get("total_pages")
                or total_pages
            )

            page_index += 1
            if max_pages and page_index >= max_pages:
                break
            if isinstance(total_pages, int) and page_index >= int(total_pages):
                break
            if empty_streak >= 2:
                break

    return {
        "source": "history-api-aggregate",
        "fetched_at": datetime.now().isoformat(),
        "pages": out_pages,
    }


def _looks_like_history_record(obj: dict) -> bool:
    if not isinstance(obj, dict):
        return False
    id_keys = ("recordId", "record_id", "taskId", "task_id", "id")
    time_keys = ("annotationTime", "annotation_time", "lastModifiedTime",
                 "last_modified_time", "updateTime", "update_time")
    status_keys = ("status", "taskStatus", "task_status")
    has_id = any(k in obj for k in id_keys)
    has_time = any(k in obj for k in time_keys)
    has_status = any(k in obj for k in status_keys)
    return has_id and (has_time or has_status)


def _extract_history_records_from_payload(payload: object,
                                          job_context: dict | None = None) -> list[dict]:
    """Extract record rows with jobName + Labeling Items from history payloads."""
    out: list[dict] = []

    def _normalize_row(row: dict, ctx: dict | None = None) -> dict:
        ctx = ctx or {}
        record_id = (
            row.get("recordId") or row.get("record_id")
            or row.get("taskId") or row.get("task_id")
            or row.get("id")
        )
        record_id = _normalize_numeric_id(record_id) or str(record_id or "").strip()

        job_name = row.get("jobName") or row.get("job_name") or row.get("title")
        if not job_name:
            job_name = ctx.get("job_name") or ctx.get("jobName") or ""

        label_count = None
        label_src = ""
        counts = []
        if isinstance(row, dict):
            counts.extend(_extract_labeling_items(row))
        if isinstance(ctx, dict):
            counts.extend(_extract_labeling_items(ctx))
        if counts:
            counts.sort(key=lambda x: x[0], reverse=True)
            label_count, label_src = counts[0]

        return {
            "record_id": record_id,
            "job_name": str(job_name or ""),
            "labeling_items": label_count,
            "labeling_items_source": label_src,
        }

    def walk(node: object, ctx: dict | None = None, depth: int = 0):
        if depth > 6 or node is None:
            return
        if isinstance(node, dict):
            # update context if job fields present
            local_ctx = dict(ctx or {})
            if "jobName" in node or "job_name" in node:
                local_ctx["job_name"] = node.get("jobName") or node.get("job_name")
            if "title" in node and not local_ctx.get("job_name"):
                local_ctx["job_name"] = node.get("title")

            # inspect list containers
            for key in ("results", "items", "records", "recordList", "list", "rows", "data"):
                val = node.get(key)
                if isinstance(val, list) and val:
                    for item in val:
                        if isinstance(item, dict) and _looks_like_history_record(item):
                            out.append(_normalize_row(item, local_ctx))
                        else:
                            walk(item, local_ctx, depth + 1)

            for v in node.values():
                if isinstance(v, (dict, list)):
                    walk(v, local_ctx, depth + 1)
            return
        if isinstance(node, list):
            for item in node[:5000]:
                walk(item, ctx, depth + 1)
            return

    walk(payload, job_context or {}, 0)
    # Dedup by record_id + job_name
    dedup = {}
    for row in out:
        key = (row.get("record_id") or "", row.get("job_name") or "")
        if key in dedup:
            # prefer row with labeling_items
            cur = dedup[key]
            if cur.get("labeling_items") is None and row.get("labeling_items") is not None:
                dedup[key] = row
        else:
            dedup[key] = row
    return list(dedup.values())


def fetch_history_by_job_ids(session: requests.Session,
                             base_url: str,
                             job_ids: list[str],
                             target_uid: str | None = None,
                             job_name_map: dict[str, dict] | None = None,
                             page_size: int = 100,
                             max_pages: int = 0) -> dict:
    """
    Fetch history/task list per jobId with multiple endpoint/param strategies.
    Returns aggregated pages + extracted record rows.
    """
    job_ids = [str(j) for j in job_ids if str(j).strip()]
    if not job_ids:
        return {"source": "history-api-by-jobs", "pages": [], "records": []}

    candidates = [
        "/appen/backend/task/list",
        "/appen/backend/tasks/list",
        "/appen/backend/task/record/list",
        "/appen/backend/task/records",
        "/appen/backend/record/list",
        "/appen/backend/tasks/history",
        "/appen/backend/task/history",
        "/appen/ui/backend/task/list",
        "/appen/ui/backend/tasks/list",
        "/appen/ui/backend/task/record/list",
        "/appen/ui/backend/record/list",
        "/api/label/appen/v1/task/list",
        "/api/label/appen/v1/task/history",
    ]
    base = base_url.rstrip("/")
    out_pages: list[dict] = []
    out_records: list[dict] = []
    uid = str(target_uid or "").strip()

    param_templates = [
        ("GET", {"jobId": "{job}", "pageIndex": "{page}", "pageSize": "{size}"}),
        ("GET", {"job_id": "{job}", "pageIndex": "{page}", "pageSize": "{size}"}),
        ("GET", {"jobId": "{job}", "page": "{page}", "pageSize": "{size}"}),
        ("GET", {"jobId": "{job}", "pageNum": "{page1}", "pageSize": "{size}"}),
        ("GET", {"jobId": "{job}", "current": "{page1}", "pageSize": "{size}"}),
        ("GET", {"jobId": "{job}", "offset": "{offset}", "limit": "{size}"}),
        ("POST", {"jobId": "{job}", "pageIndex": "{page}", "pageSize": "{size}"}),
        ("POST", {"jobId": "{job}", "page": "{page}", "pageSize": "{size}"}),
        ("POST", {"jobId": "{job}", "pageNum": "{page1}", "pageSize": "{size}"}),
        ("POST", {"jobId": "{job}", "current": "{page1}", "pageSize": "{size}"}),
    ]
    if uid:
        param_templates.extend([
            ("GET", {"jobId": "{job}", "userId": "{uid}", "pageIndex": "{page}", "pageSize": "{size}"}),
            ("POST", {"jobId": "{job}", "userId": "{uid}", "pageIndex": "{page}", "pageSize": "{size}"}),
        ])

    def _build_params(tpl: dict, page_index: int, job_id: str) -> dict:
        params = {}
        for k, v in tpl.items():
            if v == "{page}":
                params[k] = page_index
            elif v == "{page1}":
                params[k] = page_index + 1
            elif v == "{size}":
                params[k] = page_size
            elif v == "{offset}":
                params[k] = page_index * page_size
            elif v == "{job}":
                params[k] = job_id
            elif v == "{uid}":
                params[k] = uid
            else:
                params[k] = v
        return params

    for job_id in job_ids:
        job_ctx = (job_name_map or {}).get(job_id) or {}
        for ep in candidates:
            url = ep if ep.startswith("http://") or ep.startswith("https://") else f"{base}{ep}"
            working = None
            for method, tpl in param_templates:
                params = _build_params(tpl, 0, job_id)
                try:
                    if method == "POST":
                        r = session.post(url, json=params, timeout=15)
                    else:
                        r = session.get(url, params=params, timeout=15)
                except Exception:
                    continue
                if not r.ok:
                    continue
                try:
                    payload = r.json()
                except Exception:
                    continue
                rows = _extract_history_records_from_payload(payload, job_ctx)
                if rows:
                    working = (method, tpl)
                    out_pages.append({
                        "endpoint": url,
                        "pageIndex": 0,
                        "method": method,
                        "params": params,
                        "payload": payload,
                        "job_id": job_id,
                    })
                    out_records.extend(rows)
                    break
            if not working:
                continue

            method, tpl = working
            page_index = 1
            empty_streak = 0
            while True:
                params = _build_params(tpl, page_index, job_id)
                try:
                    if method == "POST":
                        r = session.post(url, json=params, timeout=15)
                    else:
                        r = session.get(url, params=params, timeout=15)
                except Exception:
                    break
                if not r.ok:
                    break
                try:
                    payload = r.json()
                except Exception:
                    break
                out_pages.append({
                    "endpoint": url,
                    "pageIndex": page_index,
                    "method": method,
                    "params": params,
                    "payload": payload,
                    "job_id": job_id,
                })
                rows = _extract_history_records_from_payload(payload, job_ctx)
                if rows:
                    out_records.extend(rows)
                    empty_streak = 0
                else:
                    empty_streak += 1
                page_index += 1
                if max_pages and page_index >= max_pages:
                    break
                if empty_streak >= 2:
                    break

            # Found a working endpoint for this job; move to next job.
            break

    # Dedup output records
    dedup = {}
    for row in out_records:
        key = (row.get("record_id") or "", row.get("job_name") or "")
        if key in dedup:
            cur = dedup[key]
            if cur.get("labeling_items") is None and row.get("labeling_items") is not None:
                dedup[key] = row
        else:
            dedup[key] = row

    return {
        "source": "history-api-by-jobs",
        "fetched_at": datetime.now().isoformat(),
        "pages": out_pages,
        "records": list(dedup.values()),
    }


def fetch_known_keys(session: requests.Session, keys: list[str],
                     workers: int = 8) -> list[dict]:
    """Fetch trực tiếp nội dung các statistics.review keys đã biết."""
    deduped = list(dict.fromkeys(k.strip() for k in keys if k and k.strip()))
    if not deduped:
        return []

    print(f"\n📌 Fetch known keys: {len(deduped)}")
    return fetch_selected_files(
        session,
        [{"key": k} for k in deduped],
        workers=workers,
        progress_step=20,
    )


def select_files_for_user(keys: list[str], target_uid: str,
                          target_job_ids: set[str] | None = None,
                          target_task_ids: set[str] | None = None) -> dict:
    """Parse key index and select only files needed to analyze one user/job-id set."""
    parsed = [parse_stats_key(k) for k in keys]
    parsed = [p for p in parsed if p]

    type_dist = defaultdict(int)
    for p in parsed:
        type_dist[p["type"]] += 1

    target_task_ids = {str(t) for t in (target_task_ids or set()) if str(t).strip()}
    if target_job_ids or target_task_ids:
        user_labeling = [
            p for p in parsed
            if p["type"] in ("LABELING", "REWORK")
            and p["user_id"] == target_uid
            and (
                (target_job_ids and p["job_id"] in target_job_ids)
                or (target_task_ids and p["task_id"] in target_task_ids)
            )
        ]
    else:
        user_labeling = [
            p for p in parsed
            if p["user_id"] == target_uid and p["type"] in ("LABELING", "REWORK")
        ]
    user_task_ids = {(p["hash"], p["task_id"]) for p in user_labeling}
    qa_files = [
        p for p in parsed
        if p["type"] in REVIEW_TYPES and (p["hash"], p["task_id"]) in user_task_ids
    ]

    fetch_map = {}
    for f in user_labeling + qa_files:
        fetch_map[f["key"]] = f

    return {
        "parsed": parsed,
        "type_dist": dict(type_dist),
        "user_labeling": user_labeling,
        "qa_files": qa_files,
        "to_fetch": list(fetch_map.values()),
        "hashes": sorted({p["hash"] for p in user_labeling}),
        "mode": "job_id" if target_job_ids else ("task_id" if target_task_ids else "user_id"),
    }


def fetch_selected_files(session: requests.Session,
                         to_fetch: list[dict],
                         workers: int = 8,
                         progress_step: int = 50) -> list[dict]:
    if not to_fetch:
        print("   ✅ Fetched 0/0 files")
        return []

    # Dedupe theo key trước khi fetch.
    ordered_keys = list(dict.fromkeys(f["key"] for f in to_fetch if f.get("key")))
    total = len(ordered_keys)
    workers = max(1, min(int(workers or 1), 64, total))

    base_headers = dict(session.headers)
    base_cookies = session.cookies.get_dict()
    fetched_by_key: dict[str, dict] = {}
    failed_keys: list[str] = []

    if workers == 1:
        for i, key in enumerate(ordered_keys, 1):
            content = fetch_file_content(session, key)
            if content is not None:
                fetched_by_key[key] = content
            else:
                failed_keys.append(key)
            if i % max(1, progress_step) == 0 or i == total:
                print(f"      {i}/{total} checked, {len(fetched_by_key)} fetched")
    else:
        print(f"      ⚡ Parallel fetch with {workers} workers")
        with ThreadPoolExecutor(max_workers=workers) as ex:
            fut_map = {
                ex.submit(
                    _fetch_file_content_with_copied_auth,
                    key,
                    base_headers,
                    base_cookies,
                ): key
                for key in ordered_keys
            }
            done = 0
            for fut in as_completed(fut_map):
                key = fut_map[fut]
                done += 1
                try:
                    content = fut.result()
                except Exception:
                    content = None
                if content is not None:
                    fetched_by_key[key] = content
                else:
                    failed_keys.append(key)

                if done % max(1, progress_step) == 0 or done == total:
                    print(f"      {done}/{total} checked, {len(fetched_by_key)} fetched")

        # Some gateways throttle or challenge concurrent fetches.
        # Retry failed keys sequentially using the original session.
        if failed_keys:
            retry_keys = list(dict.fromkeys(failed_keys))
            print(f"      ↪ Retry sequential for {len(retry_keys)} failed keys...")
            failed_keys = []
            for i, key in enumerate(retry_keys, 1):
                content = fetch_file_content(session, key)
                if content is not None:
                    fetched_by_key[key] = content
                else:
                    failed_keys.append(key)
                if i % max(1, progress_step) == 0 or i == len(retry_keys):
                    print(
                        f"      retry {i}/{len(retry_keys)}"
                        f", recovered {len(fetched_by_key)}/{total}"
                    )

    files_with_content = [
        {"key": key, "content": fetched_by_key[key]}
        for key in ordered_keys
        if key in fetched_by_key
    ]
    if failed_keys:
        print(f"   ⚠️ Failed: {len(failed_keys)} files")
    print(f"   ✅ Fetched {len(files_with_content)}/{total} files")
    return files_with_content


# ─── PARSE FILENAME ─────────────────────────────────────────────
def parse_stats_key(key: str) -> dict | None:
    slash = key.find("/")
    if slash < 0:
        return None
    hash_id = key[:slash]
    fname = key[slash + 1:]
    m = STATS_REVIEW_RE.match(fname) or STATS_LEGACY_RE.match(fname)
    if not m:
        return None
    ts, job_id, task_id, date_str, rnd, ftype, user_id = m.groups()
    return {
        "key": key, "hash": hash_id,
        "timestamp": int(ts), "job_id": job_id,
        "task_id": task_id, "date": date_str,
        "round": int(rnd), "type": ftype, "user_id": user_id,
    }


# ─── PARSE ISSUES ───────────────────────────────────────────────
def parse_issues(stats: dict) -> dict:
    """
    Xử lý 3 format issues:
      1. {"error": N}              → N annotation objects lỗi
      2. {"error": {"count": N}}   → N annotation objects lỗi
      3. {"error": {"CUBOID": N, "RECT": M}}  → N+M annotation objects lỗi
    """
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


# ─── ANALYZE ────────────────────────────────────────────────────
def analyze(files_with_content: list[dict], target_uid: str,
            target_job_ids: set[str] | None = None,
            target_task_ids: set[str] | None = None,
            total_data_hint: int | None = None,
            total_records_hint: int | None = None,
            task_comment_map: dict[str, dict] | None = None) -> dict:
    """Phân tích tất cả files, trả về report."""
    print(f"\n🔬 Phân tích cho userId={target_uid}...")
    print(f"   Tổng files: {len(files_with_content)}")

    all_parsed = []
    file_contents = {}
    task_comment_map = task_comment_map or {}
    target_job_ids = {str(j) for j in (target_job_ids or set()) if str(j).strip()}
    target_task_ids = {str(t) for t in (target_task_ids or set()) if str(t).strip()}
    for item in files_with_content:
        key = item.get("key", "")
        p = parse_stats_key(key)
        if p:
            all_parsed.append(p)
            file_contents[key] = item.get("content", {})

    # Phân loại
    type_dist = defaultdict(int)
    for p in all_parsed:
        type_dist[p["type"]] += 1
    print(f"   Types: {dict(type_dist)}")

    if target_job_ids or target_task_ids:
        labeling_files = [
            f for f in all_parsed
            if f["type"] in ("LABELING", "REWORK")
            and f["user_id"] == target_uid
            and (
                (target_job_ids and f["job_id"] in target_job_ids)
                or (target_task_ids and f["task_id"] in target_task_ids)
            )
        ]
        print(f"   Bài match history/job/task filter: {len(labeling_files)}")
    else:
        labeling_files = [f for f in all_parsed
                          if f["user_id"] == target_uid
                          and f["type"] in ("LABELING", "REWORK")]
        print(f"   Bài của userId {target_uid}: {len(labeling_files)}")

    if not labeling_files:
        if target_job_ids or target_task_ids:
            print("   ⚠️  Không thấy file LABELING/REWORK nào match history/job/task filter.")
        else:
            print("   ⚠️  Không tìm thấy bài nào! Kiểm tra userId và hash.")
            candidate_uids = sorted({
                f["user_id"] for f in all_parsed if f["type"] in ("LABELING", "REWORK")
            })
            if candidate_uids:
                print(f"   Gợi ý user_ids có trong data hiện tại: {candidate_uids[:10]}")
        return {}

    # Index
    user_task_ids = set()
    for f in labeling_files:
        user_task_ids.add((f["hash"], f["task_id"]))
    print(f"   Unique tasks: {len(user_task_ids)}")

    task_files = defaultdict(list)
    for f in all_parsed:
        k = (f["hash"], f["task_id"])
        if k in user_task_ids:
            task_files[k].append(f)

    # Tổng hợp từng task
    records = []
    for (hash_id, task_id), task_f_list in task_files.items():
        if target_job_ids or target_task_ids:
            lab = next((f for f in task_f_list
                        if f["type"] in ("LABELING", "REWORK")
                        and f["user_id"] == target_uid
                        and (
                            (target_job_ids and f["job_id"] in target_job_ids)
                            or (target_task_ids and f["task_id"] in target_task_ids)
                        )), None)
        else:
            lab = next((f for f in task_f_list if f["user_id"] == target_uid
                        and f["type"] in ("LABELING", "REWORK")), None)
        if not lab:
            continue

        qa_rounds = sorted(
            [f for f in task_f_list if f["type"] in REVIEW_TYPES],
            key=lambda x: x["round"]
        )

        rec = {
            "hash": hash_id,
            "job_id": lab["job_id"],
            "task_id": task_id,
            "qa_review_count": len(qa_rounds),
            "rounds": [],
            "errors": {},
            "error_comments": {},
            "frame_errors": {},
            "qa_comments": [],
            "history_comments": [],
            "history_issue_comments": [],
            "total_loi": 0,
            "total_severity": 0,
            "status": "UNKNOWN",
        }

        for qf in qa_rounds:
            errs = parse_issues(file_contents.get(qf["key"], {}))
            rec["rounds"].append({
                "round": qf["round"], "type": qf["type"],
                "qa_user": qf["user_id"], "date": qf["date"],
                **errs,
            })
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
            elif rec["status"] == "UNKNOWN":
                rec["status"] = "PASSED"

        # Tổng lỗi
        unique_errs = set(
            e["name"] for rnd in rec["rounds"]
            for e in rnd.get("annotation_errors", [])
        )
        rec["total_loi"] = len(unique_errs)
        rec["total_severity"] = sum(rec["errors"].values())
        for ename, vals in list(rec["error_comments"].items()):
            rec["error_comments"][ename] = _dedup_texts(vals, max_items=10)
        rec["qa_comments"] = _dedup_texts(rec["qa_comments"], max_items=20)

        hist = task_comment_map.get(str(task_id)) if task_comment_map else None
        if isinstance(hist, dict):
            rec["history_comments"] = _dedup_texts(hist.get("qa_comments") or [], max_items=20)
            hist_issue = []
            for item in hist.get("issue_comments") or []:
                if not isinstance(item, dict):
                    continue
                issue_type = _normalize_comment_text(item.get("issue_type")) or "unknown"
                comment = _normalize_comment_text(item.get("comment"))
                hist_issue.append({"issue_type": issue_type, "comment": comment})
            rec["history_issue_comments"] = hist_issue[:50]
            if rec["history_comments"]:
                rec["qa_comments"] = _dedup_texts(
                    rec["qa_comments"] + rec["history_comments"],
                    max_items=25,
                )

            if rec["history_issue_comments"]:
                seen_history_error_names = set(rec["errors"].keys())
                for item in rec["history_issue_comments"]:
                    ename = item.get("issue_type") or "unknown"
                    cmt = item.get("comment") or ""
                    if ename not in seen_history_error_names:
                        rec["errors"][ename] = rec["errors"].get(ename, 0) + 1
                        seen_history_error_names.add(ename)
                    if not cmt:
                        continue
                    rec["error_comments"].setdefault(ename, [])
                    rec["error_comments"][ename].append(cmt)
                for ename, vals in list(rec["error_comments"].items()):
                    rec["error_comments"][ename] = _dedup_texts(vals, max_items=10)
                if rec["status"] != "REJECTED":
                    rec["status"] = "REJECTED"

            rec["total_loi"] = len(rec["errors"])
            rec["total_severity"] = sum(rec["errors"].values())

        icon = "❌" if rec["status"] == "REJECTED" else ("✅" if rec["qa_review_count"] > 0 else "⬜")
        print(f"   {icon} task={task_id} job={rec['job_id']}"
              f" | QA {rec['qa_review_count']}x"
              f" | {rec['total_loi']} loại lỗi | {rec['total_severity']} occurrences")
        if rec["errors"]:
            top_issue_names = list(rec["errors"].keys())[:3]
            print(f"      issues: {', '.join(top_issue_names)}")
        if rec["qa_comments"]:
            print(f"      qa_comment: {rec['qa_comments'][0][:140]}")
        elif rec["history_comments"]:
            print(f"      history_comment: {rec['history_comments'][0][:140]}")
        records.append(rec)

    # Aggregate
    with_error = [r for r in records if r["total_loi"] > 0]
    total_data = max(
        int(total_data_hint or 0),
        len({str(r.get("job_id", "")) for r in records if r.get("job_id")}),
    )
    total_records = max(int(total_records_hint or 0), len(records))
    total_rounds = sum(len(r.get("rounds", [])) for r in records)
    rounds_with_comment = sum(
        1 for r in records for rnd in r.get("rounds", [])
        if (rnd.get("qa_comment") or (rnd.get("qa_comments") or []))
    )
    records_with_history_data = sum(
        1 for r in records
        if r.get("history_comments") or r.get("history_issue_comments")
    )
    accuracy = (total_records - len(with_error)) / total_records * 100 if total_records else 0

    err_freq: dict = defaultdict(lambda: {"records": 0, "total_severity": 0, "comments": []})
    for r in records:
        seen_comment_only = set()
        for name, sev in r["errors"].items():
            err_freq[name]["records"] += 1
            err_freq[name]["total_severity"] += sev
            comments = (r.get("error_comments") or {}).get(name, [])
            if comments:
                err_freq[name]["comments"].extend(comments)
        for name, comments in (r.get("error_comments") or {}).items():
            if name in r["errors"]:
                continue
            if name in seen_comment_only:
                continue
            seen_comment_only.add(name)
            err_freq[name]["records"] += 1
            if comments:
                err_freq[name]["comments"].extend(comments)

        # Include issue types from history/detail source
        # even when comment text is empty.
        seen_hist_issue_types = set()
        for item in (r.get("history_issue_comments") or []):
            if not isinstance(item, dict):
                continue
            name = _normalize_comment_text(item.get("issue_type")) or "unknown"
            if name in seen_hist_issue_types:
                continue
            seen_hist_issue_types.add(name)
            if name not in r["errors"] and name not in (r.get("error_comments") or {}):
                err_freq[name]["records"] += 1
            cmt = _normalize_comment_text(item.get("comment"))
            if cmt:
                err_freq[name]["comments"].append(cmt)

    top_errors = sorted(
        [{
            "name": n,
            "records": v["records"],
            "total_severity": v["total_severity"],
            "comments": _dedup_texts(v.get("comments") or [], max_items=5),
        } for n, v in err_freq.items()],
        key=lambda x: -x["records"]
    )

    print(f"   QA rounds: {total_rounds} | rounds có comment: {rounds_with_comment}")
    if task_comment_map:
        print(f"   Task comment map: {len(task_comment_map)} tasks | records match history data: {records_with_history_data}")
    if total_rounds > 0 and rounds_with_comment == 0:
        print("   ℹ️ Raw data hiện tại không có trường comment QA (hoặc comment rỗng).")

    return {
        "generated_at": datetime.now().isoformat(),
        "target_user_id": target_uid,
        "target_job_ids": sorted(target_job_ids) if target_job_ids else [],
        "target_task_ids": sorted(target_task_ids) if target_task_ids else [],
        "summary": {
            "total_data": total_data,
            "total_records": total_records,
            "records_with_error": len(with_error),
            "records_passed": total_records - len(with_error),
            "accuracy_pct": round(accuracy, 1),
            "avg_qa_returns": round(
                sum(r["qa_review_count"] for r in records) / len(records), 2
            ) if records else 0,
        },
        "top_errors": top_errors[:15],
        "issue_records": with_error,
        "records": with_error,
    }


# ─── PRINT REPORT ───────────────────────────────────────────────
def print_report(report: dict):
    s = report["summary"]
    uid = report["target_user_id"]
    print("\n" + "═" * 64)
    print(f"  📊 QA PERFORMANCE REPORT — userId={uid}")
    print("═" * 64)
    if "total_data" in s:
        print(f"  Tổng data LD đã làm      : {s['total_data']}")
    print(f"  Tổng records đã làm      : {s['total_records']}")
    print(f"  Records bị lỗi           : {s['records_with_error']}")
    print(f"  Records pass             : {s['records_passed']}")
    print(f"  🎯 Tỷ lệ chính xác       : {s['accuracy_pct']}%")
    print(f"  TB số lần QA trả/bài     : {s['avg_qa_returns']}")
    print("\n  💡 LỖI HAY MẮC NHẤT:")
    print("  " + "-" * 60)
    for i, e in enumerate(report["top_errors"][:10], 1):
        pct = round(e["records"] / s["total_records"] * 100) if s["total_records"] else 0
        print(f"  {i:2}. [{e['records']}/{s['total_records']} bài = {pct}%]"
              f"  {e['total_severity']}R  {e['name']}")
        comments = e.get("comments") or []
        if comments:
            print(f"      ↳ QA: {comments[0][:160]}")
    print("═" * 64)


# ─── SEND TO BACKEND ────────────────────────────────────────────
def send_to_backend(files_with_content: list[dict], username: str,
                    user_id: str, hashes: list[str],
                    backend_url: str, batch_size: int = 300,
                    job_ids: list[str] | None = None,
                    task_ids: list[str] | None = None,
                    total_data_hint: int | None = None,
                    total_records_hint: int | None = None,
                    task_comment_map: dict[str, dict] | None = None) -> dict | None:
    """Gửi dữ liệu đã fetch về LD backend để merge vào report."""
    total_new = 0
    last_result = None
    batches = [files_with_content[i:i+batch_size]
               for i in range(0, len(files_with_content), batch_size)]
    if not batches:
        batches = [[]]

    print(f"\n📤 Gửi {len(batches)} batch về backend {backend_url}...")
    for bi, batch in enumerate(batches):
        payload = {
            "username": username,
            "target_uid": user_id,
            "hashes": hashes,
            "job_ids": job_ids or [],
            "task_ids": task_ids or [],
            "total_data_hint": total_data_hint,
            "total_records_hint": total_records_hint,
            "task_comments": task_comment_map or {},
            "files": batch,
        }
        try:
            r = requests.post(
                f"{backend_url}/api/qa/ingest",
                json=payload,
                timeout=60,
            )
        except requests.RequestException as e:
            print(f"   Batch {bi+1}/{len(batches)}: ❌ request error: {type(e).__name__}: {e}")
            time.sleep(0.1)
            continue

        response_text = r.text or ""
        if not response_text.strip():
            print(f"   Batch {bi+1}/{len(batches)}: ❌ HTTP {r.status_code} empty response")
            time.sleep(0.1)
            continue

        try:
            result = json.loads(response_text)
        except Exception as e:
            snippet = response_text.strip().replace("\n", " ")[:240]
            ctype = r.headers.get("content-type", "")
            print(
                f"   Batch {bi+1}/{len(batches)}: ❌ "
                f"HTTP {r.status_code} non-JSON response"
                f" ({ctype}; parse={type(e).__name__}: {e}): {snippet}"
            )
            time.sleep(0.1)
            continue

        if not isinstance(result, dict):
            print(
                f"   Batch {bi+1}/{len(batches)}: ❌ "
                f"HTTP {r.status_code} JSON nhưng không phải object: {type(result).__name__}"
            )
            time.sleep(0.1)
            continue

        route_ver = result.get("route_version")
        route_file = result.get("route_file")
        if route_ver:
            print(f"   ↪ backend route_version={route_ver}")
        if route_file:
            print(f"   ↪ backend route_file={route_file}")

        if result.get("ok") is not False:
            new_cnt = result.get("new_records", 0)
            total_new += new_cnt
            last_result = result
            print(f"   Batch {bi+1}/{len(batches)}: ✅ {new_cnt} records mới")
        else:
            err = result.get("error")
            err_type = result.get("error_type")
            if err_type:
                print(f"   Batch {bi+1}/{len(batches)}: ❌ {err_type}: {err}")
            else:
                print(f"   Batch {bi+1}/{len(batches)}: ❌ {err}")
            tb = result.get("traceback")
            if isinstance(tb, str) and tb.strip():
                tb_snippet = " | ".join(line.strip() for line in tb.strip().splitlines()[-4:])
                print(f"      traceback: {tb_snippet[:500]}")
        time.sleep(0.1)

    print(f"   → Tổng records mới: {total_new}")
    if last_result and last_result.get("summary"):
        s = last_result["summary"]
        print(f"   → Backend summary: {s.get('total_records')} records"
              f" | accuracy {s.get('accuracy_pct')}%")
    return last_result


# ─── MAIN ───────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="QA Python Scanner — Chạy thẳng từ server"
    )
    parser.add_argument("--user-id", default=None,
                        help=f"UserId cần phân tích; nếu bỏ trống sẽ đọc từ data/user/<username>.json hoặc dùng {DEFAULT_USER_ID}")
    parser.add_argument("--hash", action="append", dest="hashes")
    parser.add_argument("--base-url", default=BASE_URL,
                        help=f"Platform base URL (default: {BASE_URL})")
    parser.add_argument("--host-header", default=None,
                        help="Optional Host header override (useful when --base-url is an IP)")
    parser.add_argument("--cookie", help="Cookie string từ browser")
    parser.add_argument("--cookie-file", help="File chứa cookie")
    parser.add_argument("--username", default="jr-nguyenthanhtuan-ty",
                        help="Username để login nếu không có cookie")
    parser.add_argument("--password", default="Biaozhu123")
    parser.add_argument("--from-file", help="Dùng raw JSON đã có")
    parser.add_argument("--discover-by-user", action="store_true",
                        help="Quét global statistics.review để tự tìm hash/task theo user_id")
    parser.add_argument("--discover-start-marker", default="",
                        help="Marker bắt đầu cho discover global (ví dụ: 9e786.../)")
    parser.add_argument("--full-discover-by-user", action="store_true",
                        help="Crawl tuần tự toàn bucket theo user_id, có checkpoint để resume")
    parser.add_argument("--resume-file",
                        help="Checkpoint cho --full-discover-by-user")
    parser.add_argument("--checkpoint-every", type=int, default=10,
                        help="Số page giữa mỗi lần lưu checkpoint (default: 10)")
    parser.add_argument("--job-id", action="append", default=[],
                        help="JobId từ history (Labeling). Có thể truyền nhiều lần.")
    parser.add_argument("--job-ids-file",
                        help="File chứa danh sách job_id hoặc JSON history có field jobId/job_id")
    parser.add_argument("--fetch-worker-jobs", action="store_true",
                        help="Tự gọi API worker-jobs để lấy jobId (không cần export history)")
    parser.add_argument("--worker-jobs-output",
                        help="Lưu JSON worker-jobs (default: ld_jobs_<username>.json)")
    parser.add_argument("--worker-job-page-size", type=int, default=DEFAULT_WORKER_PAGE_SIZE,
                        help=f"Page size cho worker-jobs (default: {DEFAULT_WORKER_PAGE_SIZE})")
    parser.add_argument("--worker-job-max-pages", type=int, default=0,
                        help="Giới hạn số page worker-jobs (0 = không giới hạn)")
    parser.add_argument("--worker-job-status", action="append", default=[],
                        help="jobStatusList filter (có thể dùng nhiều lần)")
    parser.add_argument("--worker-status", action="append", default=[],
                        help="statusList filter (có thể dùng nhiều lần)")
    parser.add_argument("--worker-job-name", default="",
                        help="Filter jobName cho worker-jobs")
    parser.add_argument("--worker-sort-by", default=DEFAULT_WORKER_SORT_BY,
                        help=f"Sort field worker-jobs (default: {DEFAULT_WORKER_SORT_BY})")
    parser.add_argument("--ld-history-file",
                        help="File JSON/HAR lịch sử user; tự lọc BEVLE-ZS...Labeling Job và lấy job/record ids")
    parser.add_argument("--ld-history-first", action="store_true",
                        help="Dùng LD history làm nguồn chủ đạo: lấy job_id/record_id rồi tìm statistics keys theo job_id (ít bỏ sót)")
    parser.add_argument("--ld-title-regex", default=DEFAULT_LD_TITLE_RE,
                        help=f"Regex nhận diện data LD trong history (default: {DEFAULT_LD_TITLE_RE})")
    parser.add_argument("--ld-job-type", default="Labeling",
                        help="Job Type cần lấy trong history LD (default: Labeling)")
    parser.add_argument("--ld-jobs-output",
                        help="Lưu danh sách LD jobs/records đã trích từ history ra file JSON")
    parser.add_argument("--history-file",
                        help="File JSON/HAR history/detail để map comment QA theo task_id")
    parser.add_argument("--fetch-history-api", action="store_true",
                        help="Tự gọi API history của platform để tải toàn bộ pages của history và lưu ra file (thử nhiều endpoint)")
    parser.add_argument("--history-output",
                        help="File để lưu history thu thập từ API (mặc định: history_<username>.json)")
    parser.add_argument("--history-page-size", type=int, default=100,
                        help="Số item mỗi trang khi fetch history API (nếu endpoint hỗ trợ)")
    parser.add_argument("--history-max-pages", type=int, default=0,
                        help="Giới hạn số trang khi fetch history API (0 = không giới hạn)")
    parser.add_argument("--key-index-file",
                        help="File chứa S3 list/key index; lọc theo user_id hoặc job_id trước khi fetch")
    parser.add_argument("--known-key", action="append", default=[],
                        help="Fetch thẳng một statistics.review key đã biết; có thể truyền nhiều lần")
    parser.add_argument("--known-keys-file",
                        help="File chứa known keys hoặc JSON response có Contents[].Key")
    parser.add_argument("--output", "-o", help="Lưu report ra file")
    parser.add_argument("--save-raw", action="store_true")
    parser.add_argument("--backend", default=DEFAULT_BACKEND,
                        help=f"LD backend URL (default: {DEFAULT_BACKEND})")
    parser.add_argument("--no-backend", action="store_true",
                        help="Không gửi về backend, chỉ in report")
    parser.add_argument("--max-pages", type=int, default=500,
                        help="Giới hạn số page listing (0 = không giới hạn)")
    parser.add_argument("--fetch-workers", type=int, default=8,
                        help="Số worker fetch content song song (default: 8)")
    parser.add_argument("--auto-review-detail", action="store_true",
                        help="Tự gọi API review detail theo task_id/job_id để lấy issue/comment")
    parser.add_argument("--review-workers", type=int, default=6,
                        help="Số worker gọi API review detail (default: 6)")
    parser.add_argument("--review-timeout", type=float, default=10.0,
                        help="Timeout (giây) cho mỗi request review detail (default: 10)")
    parser.add_argument("--review-endpoint", action="append", default=[],
                        help="Custom review endpoint path để thử thêm (ví dụ: /appen/backend/reviews)")
    parser.add_argument("--review-debug", action="store_true",
                        help="In debug thông tin endpoint/status cho một vài task đầu")
    args = parser.parse_args()

    print(f"🧩 Scanner version: {SCRIPT_VERSION} | file: {Path(__file__).resolve()}")

    # Allow DNS workaround: call by IP, keep logical host in Host header.
    base_url = args.base_url.strip().rstrip("/")
    if not base_url.startswith("http://") and not base_url.startswith("https://"):
        base_url = "http://" + base_url
    globals()["BASE_URL"] = base_url
    globals()["S3_BASE"] = f"{base_url}/appen/pointcloud/contributor_proxy/v1/lidar"
    globals()["LOGIN_URL"] = f"{base_url}/appen/ui/api/account/login"

    target_uid = args.user_id or load_user_id_from_account(args.username) or DEFAULT_USER_ID
    if not args.user_id:
        print(f"👤 userId resolved: {target_uid}")
    hashes = args.hashes or [DEFAULT_HASH]
    target_job_ids = set(str(j).strip() for j in (args.job_id or []) if str(j).strip())
    target_task_ids: set[str] = set()
    if args.job_ids_file:
        target_job_ids.update(load_job_ids(args.job_ids_file))

    session_holder: dict[str, requests.Session | None] = {"session": None}

    def get_or_create_session() -> requests.Session:
        sess = session_holder.get("session")
        if sess is not None:
            return sess

        cookie_str = args.cookie
        if args.cookie_file:
            cookie_str = Path(args.cookie_file).read_text().strip()

        parsed_cookie = ""
        auth_header = ""
        if cookie_str:
            parsed_cookie, auth_header = _parse_cookie_and_auth(cookie_str)
        else:
            parsed_cookie, auth_header = "", ""

        sess = make_session(parsed_cookie, host_header=args.host_header, auth_header=auth_header)

        # Thử login nếu không có cookie
        if not parsed_cookie and not auth_header:
            print(f"⚠️  Không có cookie, thử login với {args.username}...")
            ok = try_login(sess, args.username, args.password)
            if not ok:
                print("\n💡 Cách lấy cookie:")
                print("   1. Mở platform → F12 → Network")
                print("   2. Click bất kỳ request → Headers → copy 'Cookie' header value")
                print(f"   3. Chạy lại: python3 {Path(__file__).name} --cookie \"<paste>\"")
                sys.exit(1)
        session_holder["session"] = sess
        return sess

    job_name_map: dict[str, dict] = {}
    if args.fetch_worker_jobs:
        session = get_or_create_session()
        worker_payload = fetch_worker_jobs(
            session=session,
            base_url=base_url,
            page_size=args.worker_job_page_size,
            max_pages=args.worker_job_max_pages,
            job_status_list=args.worker_job_status or None,
            status_list=args.worker_status or None,
            job_name=args.worker_job_name,
            sort_by=args.worker_sort_by,
        )
        worker_job_ids = _collect_job_ids_from_worker_jobs(worker_payload.get("results") or [])
        job_name_map = _collect_job_map_from_worker_jobs(worker_payload.get("results") or [])
        if worker_job_ids:
            print(f"📌 Worker jobs fetched: {len(worker_job_ids)} job ids")
            target_job_ids.update(worker_job_ids)
        out_path = Path(args.worker_jobs_output or f"ld_jobs_{args.username}.json")
        try:
            out_path.write_text(
                json.dumps(worker_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"   Worker jobs lưu: {out_path}")
        except Exception as e:
            print(f"⚠️  Không lưu được worker-jobs output: {e}")

    ld_history: dict = {"jobs": [], "records": [], "job_ids": [], "record_ids": []}
    if args.ld_history_file:
        ld_path = Path(args.ld_history_file)
        if not ld_path.exists():
            print(f"❌ --ld-history-file không tồn tại: {ld_path}")
            sys.exit(1)
        ld_history = extract_ld_jobs_from_history(
            str(ld_path),
            title_pattern=args.ld_title_regex,
            job_type=args.ld_job_type,
            target_uid=target_uid,
        )
        target_job_ids.update(ld_history.get("job_ids") or [])
        target_task_ids.update(ld_history.get("record_ids") or [])
        print(
            f"🧾 LD history: {len(ld_history.get('jobs') or [])} data/jobs"
            f" | {len(ld_history.get('record_ids') or [])} records"
        )
        if ld_history.get("labeling_items_total"):
            print(
                f"   Labeling Items total: {ld_history.get('labeling_items_total')}"
                f" (jobs with items: {ld_history.get('jobs_with_labeling_items')})"
            )
        if args.ld_jobs_output:
            Path(args.ld_jobs_output).write_text(
                json.dumps(ld_history, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"   LD history index lưu: {args.ld_jobs_output}")
    # Optionally fetch history via API (paginated)
    if args.fetch_history_api:
        session = get_or_create_session()
        hist_out = args.history_output or f"history_{args.username}.json"
        hist_payload = fetch_history_pages(session, base_url, page_size=args.history_page_size, max_pages=args.history_max_pages)
        if not hist_payload.get("pages") and target_job_ids:
            print("⚠️  History API không ra dữ liệu; thử fetch theo job_id...")
            job_hist = fetch_history_by_job_ids(
                session,
                base_url=base_url,
                job_ids=sorted(target_job_ids),
                target_uid=target_uid,
                job_name_map=job_name_map,
                page_size=args.history_page_size,
                max_pages=args.history_max_pages,
            )
            if job_hist.get("pages"):
                hist_payload = job_hist
        try:
            Path(hist_out).write_text(json.dumps(hist_payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"   Fetched history saved: {hist_out}")
            # Point ld_history_file to this file for extraction
            args.ld_history_file = hist_out
            if hist_payload.get("records"):
                print(f"   Extracted history records: {len(hist_payload.get('records') or [])}")
            # Try extract from aggregated pages if possible
            try:
                # Compose a synthetic docs list from page payloads
                docs = []
                for p in hist_payload.get('pages', []):
                    payload = p.get('payload')
                    if payload is None:
                        continue
                    if isinstance(payload, dict) and payload.get('data'):
                        docs.append(payload.get('data'))
                    else:
                        docs.append(payload)
                if hist_payload.get('records'):
                    docs.append({"records": hist_payload.get('records')})
                temp_path = Path(hist_out + '.flat.json')
                temp_path.write_text(json.dumps(docs, ensure_ascii=False, indent=2), encoding='utf-8')
                args.ld_history_file = str(temp_path)
                ld_history = extract_ld_jobs_from_history(str(temp_path), title_pattern=args.ld_title_regex, job_type=args.ld_job_type, target_uid=target_uid)
                target_job_ids.update(ld_history.get('job_ids') or [])
                target_task_ids.update(ld_history.get('record_ids') or [])
                print(f"   Extracted from fetched history: {len(ld_history.get('jobs') or [])} jobs | {len(ld_history.get('record_ids') or [])} records")
                if ld_history.get("labeling_items_total"):
                    print(
                        f"   Labeling Items total: {ld_history.get('labeling_items_total')}"
                        f" (jobs with items: {ld_history.get('jobs_with_labeling_items')})"
                    )
            except Exception:
                pass
        except Exception as e:
            print(f"⚠️  Không lưu hoặc xử lý history fetched: {e}")
    if target_job_ids:
        print(f"🎯 Mode theo job_id: {len(target_job_ids)} job ids")
    if target_task_ids:
        print(f"🎯 Mode theo record/task_id: {len(target_task_ids)} records")

    task_comment_map: dict[str, dict] = {}
    if args.history_file:
        history_path = Path(args.history_file)
        if not history_path.exists():
            print(f"❌ --history-file không tồn tại: {history_path}")
            print("   Hãy export HAR/JSON từ Network rồi chạy lại với path đúng.")
            sys.exit(1)
        try:
            task_comment_map = load_task_comments_from_history(
                str(history_path),
                target_job_ids=target_job_ids or None,
                target_uid=target_uid,
            )
            qa_cnt = sum(len(v.get("qa_comments") or []) for v in task_comment_map.values())
            issue_cnt = sum(len(v.get("issue_comments") or []) for v in task_comment_map.values())
            print(
                f"🗂️ History comment map: {len(task_comment_map)} tasks"
                f" | qa_comments={qa_cnt}"
                f" | issue_comments={issue_cnt}"
            )
        except Exception as e:
            print(f"❌ Không parse được history-file: {e}")
            sys.exit(1)

    # ── Mode: từ file đã có ─────────────────────────────────────
    if args.from_file:
        print(f"📂 Đọc từ file: {args.from_file}")
        with open(args.from_file, encoding="utf-8") as f:
            raw = json.load(f)
        files_with_content = raw if isinstance(raw, list) else raw.get("files", [])
        if args.auto_review_detail:
            session = get_or_create_session()
            target_tasks = _task_metas_from_ld_history(ld_history) or _collect_target_tasks_from_files(
                files_with_content,
                target_uid=target_uid,
                target_job_ids=target_job_ids or None,
                target_task_ids=target_task_ids or None,
            )
            live_map = fetch_task_comments_live(
                session,
                target_tasks,
                target_uid=target_uid,
                endpoint_paths=args.review_endpoint or None,
                workers=args.review_workers,
                timeout_sec=args.review_timeout,
                debug=args.review_debug,
            )
            task_comment_map = _merge_task_comment_maps(task_comment_map, live_map)
            qa_cnt = sum(len(v.get("qa_comments") or []) for v in task_comment_map.values())
            issue_cnt = sum(len(v.get("issue_comments") or []) for v in task_comment_map.values())
            print(
                f"🧠 Combined comment map: {len(task_comment_map)} tasks"
                f" | qa_comments={qa_cnt}"
                f" | issue_comments={issue_cnt}"
            )
        derived_hashes = sorted({
            p["hash"]
            for p in (parse_stats_key(item.get("key", "")) for item in files_with_content)
            if p
        })

        if not args.no_backend:
            send_to_backend(
                files_with_content,
                username=args.username,
                user_id=target_uid,
                hashes=derived_hashes or hashes,
                backend_url=args.backend,
                job_ids=sorted(target_job_ids) if target_job_ids else [],
                task_ids=sorted(target_task_ids) if target_task_ids else [],
                total_data_hint=len(ld_history.get("jobs") or []) or None,
                total_records_hint=_history_records_hint(ld_history),
                task_comment_map=task_comment_map,
            )

        report = analyze(files_with_content, target_uid,
                         target_job_ids=target_job_ids or None,
                         target_task_ids=target_task_ids or None,
                         total_data_hint=len(ld_history.get("jobs") or []) or None,
                         total_records_hint=_history_records_hint(ld_history),
                         task_comment_map=task_comment_map)
        if report:
            print_report(report)
            out = args.output or f"qa_report_{target_uid}_{int(time.time())}.json"
            Path(out).write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"\n📥 Report đã lưu: {out}")
        return

    # ── Setup session ───────────────────────────────────────────
    session = get_or_create_session()

    # If user requests history-first flow, use ld_history job_ids to find statistics keys
    if args.ld_history_first and ld_history.get("job_ids"):
        print("\n🔎 LD-history-first mode: tìm statistics keys theo job_ids từ history...")
        job_ids_from_history = set(ld_history.get("job_ids") or [])
        # List global statistics keys (may be large but we'll filter by job_id)
        global_keys = list_global_statistics_keys(session, max_pages=args.max_pages)
        print(f"   Tổng global statistics keys: {len(global_keys)}")
        selected = select_files_for_user(
            global_keys, target_uid,
            target_job_ids=job_ids_from_history,
            target_task_ids=None,
        )
        print(f"   Matched LABELING+REWORK files (job_id): {len(selected['user_labeling'])}")
        print(f"   Related review files (QA/REWORK): {len(selected['qa_files'])}")
        if selected["to_fetch"]:
            print(f"\n   📥 Fetching content của {len(selected['to_fetch'])} files (history-first)...")
            fetched_files = fetch_selected_files(
                session,
                selected["to_fetch"],
                workers=args.fetch_workers,
            )
            all_files_with_content.extend(fetched_files)
            discover_succeeded = len(fetched_files) > 0
        else:
            print("   Không tìm thấy files tương ứng với job_ids trong history.")

    # ── Scan từng hash ──────────────────────────────────────────
    all_files_with_content = []
    known_keys = list(args.known_key or [])
    discover_succeeded = False
    if args.known_keys_file:
        known_keys.extend(load_known_keys(args.known_keys_file))

    if known_keys:
        all_files_with_content.extend(
            fetch_known_keys(session, known_keys, workers=args.fetch_workers)
        )

    if args.key_index_file and not known_keys:
        print(f"\n🗂️ Đọc key index: {args.key_index_file}")
        index_keys = load_known_keys(args.key_index_file)
        selected = select_files_for_user(
            index_keys, target_uid,
            target_job_ids=target_job_ids or None,
            target_task_ids=target_task_ids or None,
        )
        print(f"   Keys trong index: {len(index_keys)}")
        print(f"   Parsed statistics keys: {len(selected['parsed'])}")
        print(f"   Types: {selected['type_dist']}")
        if selected["mode"] == "job_id":
            print(f"   Matched LABELING+REWORK files (job_id): {len(selected['user_labeling'])}")
        else:
            print(f"   User {target_uid} LABELING+REWORK files: {len(selected['user_labeling'])}")
        print(f"   Discovered hashes: {len(selected['hashes'])}")
        print(f"   Related review files (QA/REWORK): {len(selected['qa_files'])}")
        if selected["hashes"]:
            hashes = selected["hashes"]
            print(f"   Hash sample: {', '.join(hashes[:10])}")
        print(f"\n   📥 Fetching content của {len(selected['to_fetch'])} files...")
        all_files_with_content.extend(
            fetch_selected_files(
                session,
                selected["to_fetch"],
                workers=args.fetch_workers,
            )
        )
        discover_succeeded = len(all_files_with_content) > 0

    if args.full_discover_by_user and not known_keys and not discover_succeeded:
        print(f"\n🧭 Full discover crawl theo userId={target_uid} ...")
        resume_file = (
            args.resume_file
            or f"qa_discover_{target_uid}_checkpoint.json"
        )
        selected = crawl_global_for_user(
            session=session,
            target_uid=target_uid,
            max_pages=args.max_pages,
            resume_file=resume_file,
            save_every=args.checkpoint_every,
            target_job_ids=target_job_ids or None,
            target_task_ids=target_task_ids or None,
        )
        print(f"   Checkpoint: {selected['checkpoint']}")
        print(f"   Tổng index pages đã quét: {selected['pages_scanned']}")
        print(f"   Crawl index done: {selected['done']}")
        print(f"   Types: {selected['type_dist']}")
        if selected["mode"] == "job_id":
            print(f"   Matched LABELING+REWORK files (job_id): {len(selected['user_labeling'])}")
        else:
            print(f"   User {target_uid} LABELING+REWORK files: {len(selected['user_labeling'])}")
        print(f"   Discovered hashes: {len(selected['hashes'])}")
        print(f"   Related review files (QA/REWORK): {len(selected['qa_files'])}")
        if selected["hashes"]:
            hashes = selected["hashes"]
            print(f"   Hash sample: {', '.join(hashes[:10])}")

        if selected["to_fetch"]:
            print(f"\n   📥 Fetching content chỉ cho {len(selected['to_fetch'])} user/QA files...")
            fetched_files = fetch_selected_files(
                session,
                selected["to_fetch"],
                workers=args.fetch_workers,
            )
            all_files_with_content.extend(fetched_files)
            discover_succeeded = len(fetched_files) > 0
        else:
            print("   Chưa tìm thấy task của user trong phần index đã quét.")
            print("   Chạy lại cùng lệnh để resume từ checkpoint.")

    if args.discover_by_user and not known_keys and not discover_succeeded:
        print(f"\n🧭 Discover mode theo userId={target_uid} ...")
        discover_marker = args.discover_start_marker.strip()
        if not discover_marker and hashes:
            discover_marker = f"{hashes[0]}/"
        if discover_marker:
            print(f"   Start marker: {discover_marker}")
        global_keys = list_global_statistics_keys(
            session,
            max_pages=args.max_pages,
            start_marker=discover_marker,
        )
        selected = select_files_for_user(
            global_keys, target_uid,
            target_job_ids=target_job_ids or None,
            target_task_ids=target_task_ids or None,
        )
        print(f"   Parsed statistics keys: {len(selected['parsed'])}")
        if selected["mode"] == "job_id":
            print(f"   Matched LABELING+REWORK files (job_id): {len(selected['user_labeling'])}")
        else:
            print(f"   User {target_uid} LABELING+REWORK files: {len(selected['user_labeling'])}")
        print(f"   Discovered hashes: {len(selected['hashes'])}")
        if selected["hashes"]:
            sample = ", ".join(selected["hashes"][:10])
            print(f"   Hash sample: {sample}")
            hashes = selected["hashes"]
        print(f"   Related review files (QA/REWORK): {len(selected['qa_files'])}")

        print(f"\n   📥 Fetching content của {len(selected['to_fetch'])} files...")
        fetched_files = fetch_selected_files(
            session,
            selected["to_fetch"],
            workers=args.fetch_workers,
        )
        all_files_with_content.extend(fetched_files)
        discover_succeeded = len(fetched_files) > 0

    for hi, hash_id in enumerate(hashes):
        if known_keys or discover_succeeded:
            print(f"\n📂 Hash {hi+1}/{len(hashes)}: {hash_id}")
            if known_keys:
                print("   Bỏ qua list bucket vì đã dùng known-key mode.")
            else:
                print("   Bỏ qua list bucket vì data đã có từ key-index/discover mode.")
            continue
        if args.full_discover_by_user and not discover_succeeded:
            print("\n⚠️ Full discover chưa tìm được data trong page budget hiện tại.")
            print("   Bỏ qua scan theo --hash vì endpoint list đang ignore prefix/marker.")
            break
        if args.discover_by_user and not discover_succeeded and hi == 0:
            print("\n⚠️ Discover-by-user không tìm được data; fallback sang scan theo --hash ...")

        print(f"\n📂 Hash {hi+1}/{len(hashes)}: {hash_id}")

        # Bước 1: List TẤT CẢ statistics files
        print("   Listing all statistics.review.* files...")
        keys = list_all_files(session, hash_id,
                              sub_prefix="statistics.review.",
                              max_pages=args.max_pages)
        if not keys:
            print("   ⚠️ Không thấy statistics.review.* — fallback scan hash/* để tìm *.statistics.json")
            all_hash_keys = list_all_files(session, hash_id,
                                           sub_prefix="",
                                           max_pages=args.max_pages)
            keys = [k for k in all_hash_keys if parse_stats_key(k)]
        print(f"   → {len(keys)} files found")

        # Parse filenames
        parsed = [parse_stats_key(k) for k in keys]
        parsed = [p for p in parsed if p]

        # Phân bố type
        type_dist = defaultdict(int)
        for p in parsed:
            type_dist[p["type"]] += 1
        print(f"   Types: {dict(type_dist)}")

        # Lọc bài của user
        user_labeling = [p for p in parsed
                         if p["user_id"] == target_uid
                         and p["type"] in ("LABELING", "REWORK")]
        print(f"   User {target_uid} LABELING+REWORK: {len(user_labeling)}")

        user_task_ids = set(p["task_id"] for p in user_labeling)
        print(f"   Unique taskIds: {len(user_task_ids)}")

        # Lọc review files của cùng task
        qa_files = [p for p in parsed
                    if p["type"] in REVIEW_TYPES
                    and p["task_id"] in user_task_ids]
        print(f"   Review files (QA/REWORK): {len(qa_files)}")

        # Tất cả files cần fetch (dedup theo key)
        selected = select_files_for_user(
            keys, target_uid,
            target_job_ids=target_job_ids or None,
            target_task_ids=target_task_ids or None,
        )
        to_fetch = selected["to_fetch"]
        print(f"\n   📥 Fetching content của {len(to_fetch)} files...")
        all_files_with_content.extend(
            fetch_selected_files(session, to_fetch, workers=args.fetch_workers)
        )

    print(f"\n📊 Tổng files với content: {len(all_files_with_content)}")

    if args.auto_review_detail and (all_files_with_content or ld_history.get("records")):
        target_tasks = _task_metas_from_ld_history(ld_history) or _collect_target_tasks_from_files(
            all_files_with_content,
            target_uid=target_uid,
            target_job_ids=target_job_ids or None,
            target_task_ids=target_task_ids or None,
        )
        live_map = fetch_task_comments_live(
            session,
            target_tasks,
            target_uid=target_uid,
            endpoint_paths=args.review_endpoint or None,
            workers=args.review_workers,
            timeout_sec=args.review_timeout,
            debug=args.review_debug,
        )
        task_comment_map = _merge_task_comment_maps(task_comment_map, live_map)
        qa_cnt = sum(len(v.get("qa_comments") or []) for v in task_comment_map.values())
        issue_cnt = sum(len(v.get("issue_comments") or []) for v in task_comment_map.values())
        print(
            f"🧠 Combined comment map: {len(task_comment_map)} tasks"
            f" | qa_comments={qa_cnt}"
            f" | issue_comments={issue_cnt}"
        )

    # ── Lưu raw data ────────────────────────────────────────────
    if args.save_raw:
        raw_path = f"qa_raw_{target_uid}_{int(time.time())}.json"
        Path(raw_path).write_text(
            json.dumps(all_files_with_content, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"   Raw data lưu: {raw_path}")

    # ── Gửi về backend ──────────────────────────────────────────
    if not args.no_backend:
        send_to_backend(all_files_with_content,
                        username=args.username,
                        user_id=target_uid,
                        hashes=hashes,
                        backend_url=args.backend,
                        job_ids=sorted(target_job_ids) if target_job_ids else [],
                        task_ids=sorted(target_task_ids) if target_task_ids else [],
                        total_data_hint=len(ld_history.get("jobs") or []) or None,
                        total_records_hint=_history_records_hint(ld_history),
                        task_comment_map=task_comment_map)

    # ── Phân tích local ─────────────────────────────────────────
    report = analyze(all_files_with_content, target_uid,
                     target_job_ids=target_job_ids or None,
                     target_task_ids=target_task_ids or None,
                     total_data_hint=len(ld_history.get("jobs") or []) or None,
                     total_records_hint=_history_records_hint(ld_history),
                     task_comment_map=task_comment_map)
    if not report:
        print("❌ Không có data để phân tích")
        sys.exit(1)

    print_report(report)

    out = args.output or f"qa_report_{target_uid}_{int(time.time())}.json"
    Path(out).write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n📥 Report đã lưu: {out}")


if __name__ == "__main__":
    main()
