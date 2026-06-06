#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import csv
import html
import json
import re
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from scanner_paths import default_cookie_file, runtime_dir

try:
    import requests
except ImportError:
    print("Need requests: pip install requests")
    sys.exit(1)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


DEFAULT_BASE_URL = "http://10.79.0.80"
DEFAULT_HOST_HEADER = "global-autolabeling-service.evad.xiaomi.srv"
DEFAULT_LIMIT_MODES = {"QA_RW", "QA_RO"}

ANNOTATION_KEY_RE = re.compile(
    r"^(?P<hash>[a-f0-9]{32})/"
    r"R\.(?P<timestamp>\d+)\.lidar\.seg\."
    r"(?P<qa_job_id>\d+)\.(?P<qa_task_id>.+?)\."
    r"(?P<record_id>\d+)\.(?P<mode>QA_RW|QA_RO|AUDIT|REWORK|LABELING)\."
    r"(?P<worker_id>\d+)(?:\.[^.]+)?\.(?P<kind>review|result)\.json$",
    re.IGNORECASE,
)


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_cookie(cookie_file: str) -> str:
    path = Path(cookie_file)
    if not path.exists():
        raise FileNotFoundError(f"cookie-file does not exist: {path}")
    raw = path.read_text(encoding="utf-8", errors="ignore").strip()
    cookie = raw
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except Exception:
            data = None
        if isinstance(data, dict):
            cookie = str(data.get("scanner_cookie") or data.get("cookie") or "").strip()
    if not cookie:
        raise ValueError(f"cookie-file is empty: {path}")
    return cookie


def _normalize_base_url(base_url: str) -> str:
    base_url = (base_url or "").strip().rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        base_url = "http://" + base_url
    return base_url


def _session(cookie: str, host_header: str) -> requests.Session:
    sess = requests.Session()
    sess.headers.update(
        {
            "Cookie": cookie,
            "Host": host_header,
            "Origin": f"http://{host_header}",
            "Referer": f"http://{host_header}/appen/mashup/ssr/qa-report",
            "User-Agent": "Mozilla/5.0",
            "Accept": "*/*",
        }
    )
    return sess


def _clone_session(sess: requests.Session) -> requests.Session:
    cloned = requests.Session()
    cloned.headers.update(sess.headers)
    return cloned


def _local_url(url: str, base_url: str, host_header: str) -> str:
    if not isinstance(url, str):
        return url
    for prefix in (f"http://{host_header}", f"https://{host_header}"):
        if url.startswith(prefix):
            return base_url + url[len(prefix):]
    return url


def _get_json(
    sess: requests.Session,
    url: str,
    base_url: str,
    host_header: str,
    params: dict[str, Any] | None = None,
    timeout: int = 25,
) -> tuple[bool, Any, str]:
    try:
        resp = sess.get(
            _local_url(url, base_url, host_header),
            params=params or {},
            timeout=timeout,
        )
    except Exception as exc:
        return False, None, f"{type(exc).__name__}: {exc}"
    try:
        data = resp.json()
    except Exception:
        return False, None, f"HTTP {resp.status_code}: {(resp.text or '')[:300]}"
    if resp.status_code != 200:
        return False, data, f"HTTP {resp.status_code}"
    return True, data, ""


def _post_json(
    sess: requests.Session,
    url: str,
    base_url: str,
    host_header: str,
    params: dict[str, Any],
    body: dict[str, Any],
    timeout: int = 25,
) -> tuple[bool, Any, str]:
    try:
        resp = sess.post(
            _local_url(url, base_url, host_header),
            params=params,
            json=body,
            timeout=timeout,
        )
    except Exception as exc:
        return False, None, f"{type(exc).__name__}: {exc}"
    try:
        data = resp.json()
    except Exception:
        return False, None, f"HTTP {resp.status_code}: {(resp.text or '')[:300]}"
    if resp.status_code != 200:
        return False, data, f"HTTP {resp.status_code}"
    if isinstance(data, dict) and data.get("code") == 0:
        return True, data, ""
    return False, data, str(data.get("message") if isinstance(data, dict) else data)


def _data(section: Any) -> dict[str, Any]:
    if isinstance(section, dict) and isinstance(section.get("data"), dict):
        return section["data"]
    return {}


def _num(value: Any, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    return int(_num(value, default))


def _job_context(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "jobId": job.get("jobId"),
        "jobDisplayId": job.get("jobDisplayId"),
        "jobName": job.get("jobName"),
        "jobType": job.get("jobType"),
        "jobStatus": job.get("jobStatus"),
        "workerId": job.get("workerId"),
        "projectId": job.get("projectId"),
        "projectName": job.get("projectName"),
        "availableTasks": job.get("availableTasks"),
        "needReworkTasks": job.get("needReworkTasks"),
        "accuracy": job.get("accuracy"),
    }


def _candidate_records(full_data: dict[str, Any], scan_all: bool) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in full_data.get("job_results") or []:
        job = item.get("job") or {}
        context = _job_context(job)
        label = _data(item.get("label_summary"))
        qa = _data(item.get("qa_summary"))
        qa_reject_count = _int(qa.get("qaRejectCount"))
        need_rework = _int(job.get("needReworkTasks"))
        rework_by_other = _int(label.get("reworkCountByOther"))
        records = (item.get("task_details") or {}).get("records") or []
        for record in records:
            if not isinstance(record, dict):
                continue
            origin_doc = record.get("originDocId")
            final_doc = record.get("finalDocId")
            has_final = final_doc not in (None, "", 0, "0")
            changed = has_final and origin_doc not in (None, "") and str(origin_doc) != str(final_doc)
            candidate = scan_all or (has_final and changed and (qa_reject_count > 0 or need_rework > 0 or rework_by_other > 0))
            if not candidate:
                continue
            out.append(
                {
                    **context,
                    "recordId": record.get("recordId"),
                    "originDocId": origin_doc,
                    "finalDocId": final_doc,
                    "recordStatus": record.get("status"),
                    "labelingTime": record.get("labelingTime"),
                    "lastModifiedTime": record.get("lastModifiedTime"),
                    "qaRejectCountForJob": qa_reject_count,
                    "qaPassCountForJob": _int(qa.get("qaPassCount")),
                    "qaCountForJob": _int(qa.get("qaCount")),
                    "reworkCountByOtherForJob": rework_by_other,
                }
            )
    return out


def _qa_report_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            for key in ("items", "records", "list", "results"):
                value = data.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
    return []


def _decode_url_text(value: str) -> str:
    value = html.unescape(value or "").strip()
    if not value:
        return ""
    try:
        return json.loads(f'"{value}"')
    except Exception:
        return value.replace("\\u002F", "/").replace("\\/", "/")


def _decode_template(template: str) -> str:
    if not isinstance(template, str) or not template:
        return ""
    try:
        return base64.b64decode(template).decode("utf-8", errors="replace")
    except Exception:
        return template


def _extract_base_url(template: str) -> str:
    text = _decode_template(template)
    patterns = (
        r"base_url\s*:\s*'([^']+)'",
        r'"base_url"\s*:\s*"([^"]+)"',
        r"baseUrl\s*:\s*'([^']+)'",
        r'"baseUrl"\s*:\s*"([^"]+)"',
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return _decode_url_text(match.group(1))
    return ""


def _file_name_from_url(url: str) -> str:
    if not isinstance(url, str):
        return ""
    parsed = urlsplit(url)
    qs = parse_qs(parsed.query)
    value = (qs.get("fileName") or [""])[0]
    return _decode_url_text(value)


def _parse_annotation_url(annotation_url: str) -> dict[str, Any] | None:
    key = _file_name_from_url(annotation_url)
    match = ANNOTATION_KEY_RE.match(key)
    if not match:
        return None
    info = match.groupdict()
    info["key"] = key
    info["qaReviewId"] = f"{info['qa_job_id']}.{info['qa_task_id']}.{info['record_id']}.review"
    return info


def _choose_qa_annotations(items: list[dict[str, Any]], modes: set[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items:
        annotation = item.get("annotation")
        if not isinstance(annotation, str):
            continue
        parsed = _parse_annotation_url(annotation)
        if not parsed:
            continue
        if parsed.get("mode") not in modes:
            continue
        base_url = _extract_base_url(item.get("template") or "")
        if not base_url:
            continue
        out.append({"item": item, "annotation": annotation, "parsed": parsed, "baseUrl": base_url})
    return out


def _strip_points(node: Any, include_points: bool) -> Any:
    if include_points:
        return node
    if isinstance(node, list):
        return [_strip_points(item, include_points) for item in node]
    if isinstance(node, dict):
        return {k: _strip_points(v, include_points) for k, v in node.items() if k not in {"points", "pointsInfo"}}
    return node


def _fetch_statistics(
    sess: requests.Session,
    url: str,
    base_url: str,
    host_header: str,
    timeout: int,
) -> dict[str, Any] | None:
    if not url:
        return None
    ok, data, _error = _get_json(sess, url, base_url, host_header, timeout=timeout)
    if ok and isinstance(data, dict):
        return data
    return None


def _issue_names_from_statistics(stats: dict[str, Any] | None) -> list[str]:
    if not isinstance(stats, dict):
        return []
    issues = stats.get("issues")
    if not isinstance(issues, dict):
        return []
    return [str(name) for name, value in issues.items() if value not in (None, 0, {}, [])]


def _flatten_review_items(record: dict[str, Any], review_data: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    reviews = review_data.get("reviews")
    if not isinstance(reviews, list):
        return rows
    for frame in reviews:
        if not isinstance(frame, dict):
            continue
        frame_id = frame.get("frameId")
        for item in frame.get("items") or []:
            if not isinstance(item, dict):
                continue
            issue_types = item.get("type")
            if isinstance(issue_types, list):
                issue_text = " | ".join(str(v) for v in issue_types)
            elif issue_types is None:
                issue_text = ""
            else:
                issue_text = str(issue_types)
            rows.append(
                {
                    **record,
                    "qaJobId": review_data.get("qaJobId"),
                    "qaTaskId": review_data.get("qaTaskId"),
                    "qaWorkerId": review_data.get("qaWorkerId"),
                    "qaMode": review_data.get("qaMode"),
                    "qaReviewId": review_data.get("qaReviewId"),
                    "qaAnnotationKey": review_data.get("qaAnnotationKey"),
                    "reviewStatisticsUrl": review_data.get("review_statistics"),
                    "frameId": frame_id,
                    "reviewItemId": item.get("id"),
                    "reviewNumber": item.get("number"),
                    "qaType": item.get("qaType"),
                    "instanceType": item.get("instanceType"),
                    "issueTypes": issue_text,
                    "comment": item.get("comment") or "",
                    "pointCount": item.get("pointCount"),
                    "visible": item.get("visible"),
                }
            )
    return rows


def _fetch_reviews_for_one_record(
    *,
    sess: requests.Session,
    record: dict[str, Any],
    index: int,
    total: int,
    qa_report_url: str,
    reviews_url: str,
    base_url: str,
    host_header: str,
    modes: set[str],
    include_points: bool,
    fetch_statistics: bool,
    keep_record_details: bool,
    timeout: int,
) -> dict[str, Any]:
    prefix = f"[{index}/{total}] job={record.get('jobId')} record={record.get('recordId')}"
    params = {
        "jobId": record.get("jobId"),
        "projectId": record.get("projectId"),
        "originDocId": record.get("originDocId"),
        "finalDocId": record.get("finalDocId"),
        "recordId": record.get("recordId"),
    }
    ok, payload, error = _get_json(
        sess,
        qa_report_url,
        base_url,
        host_header,
        params=params,
        timeout=timeout,
    )
    if not ok:
        return {
            "index": index,
            "record": None,
            "issue_rows": [],
            "errors": [{**record, "stage": "qa-report", "error": error, "response": payload}],
            "log": f"{prefix}: qa-report failed",
        }

    annotations = _choose_qa_annotations(_qa_report_items(payload), modes)
    if not annotations:
        return {
            "index": index,
            "record": None,
            "issue_rows": [],
            "errors": [],
            "log": f"{prefix}: no QA annotation",
        }

    record_reviews: list[dict[str, Any]] = []
    issue_rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    review_count = 0
    item_count = 0
    for annotation in annotations:
        parsed = annotation["parsed"]
        qa_review_id = parsed["qaReviewId"]
        ok, review_payload, error = _post_json(
            sess,
            reviews_url,
            base_url,
            host_header,
            params={"auditId": qa_review_id},
            body={"baseUrl": annotation["baseUrl"]},
            timeout=timeout,
        )
        if not ok:
            errors.append(
                {
                    **record,
                    "stage": "reviews",
                    "qaReviewId": qa_review_id,
                    "qaAnnotationKey": parsed.get("key"),
                    "error": error,
                    "response": review_payload,
                }
            )
            continue

        data = review_payload.get("data") if isinstance(review_payload, dict) else None
        if not isinstance(data, dict):
            continue
        review_count += 1
        stats = None
        if fetch_statistics:
            stats = _fetch_statistics(
                sess,
                data.get("review_statistics") or "",
                base_url,
                host_header,
                timeout,
            )
        flatten_review_record = {
            "qaJobId": parsed.get("qa_job_id"),
            "qaTaskId": parsed.get("qa_task_id"),
            "qaWorkerId": parsed.get("worker_id"),
            "qaMode": parsed.get("mode"),
            "qaReviewId": qa_review_id,
            "qaAnnotationKey": parsed.get("key"),
            "review_statistics": data.get("review_statistics"),
            "statistics": stats,
            "statisticsIssueNames": _issue_names_from_statistics(stats),
            "reviews": data.get("reviews") or [],
        }
        issue_rows.extend(_flatten_review_items(record, flatten_review_record))
        item_count += sum(
            len((frame.get("items") or []))
            for frame in (flatten_review_record.get("reviews") or [])
            if isinstance(frame, dict)
        )
        if keep_record_details:
            record_reviews.append(
                {
                    **flatten_review_record,
                    "reviews": _strip_points(flatten_review_record["reviews"], include_points),
                }
            )

    if review_count:
        return {
            "index": index,
            "record": {**record, "qaReviews": record_reviews} if keep_record_details else None,
            "issue_rows": issue_rows,
            "errors": errors,
            "log": f"{prefix}: {item_count} QA issue items",
        }

    return {
        "index": index,
        "record": None,
        "issue_rows": issue_rows,
        "errors": errors,
        "log": f"{prefix}: no review details",
    }


def fetch_reviews_for_records(
    *,
    sess: requests.Session,
    records: list[dict[str, Any]],
    base_url: str,
    host_header: str,
    modes: set[str],
    include_points: bool,
    fetch_statistics: bool,
    timeout: int,
    sleep_sec: float,
    workers: int = 1,
    keep_record_details: bool = True,
) -> dict[str, Any]:
    qa_report_url = f"{base_url}/appen/mashup/api/qa-report"
    reviews_url = f"{base_url}/appen/pointcloud/contributor_proxy/v1/lidar/reviews"
    indexed_records: list[tuple[int, dict[str, Any]]] = []
    indexed_issue_rows: list[tuple[int, dict[str, Any]]] = []
    indexed_errors: list[tuple[int, dict[str, Any]]] = []
    issue_type_counts: Counter[str] = Counter()
    comment_counts: Counter[str] = Counter()

    total = len(records)
    workers = max(1, int(workers or 1))

    def consume(result: dict[str, Any]) -> None:
        idx = int(result.get("index") or 0)
        print(result.get("log") or f"[{idx}/{total}] done")
        record_result = result.get("record")
        if isinstance(record_result, dict):
            indexed_records.append((idx, record_result))
        for row in result.get("issue_rows") or []:
            indexed_issue_rows.append((idx, row))
            for issue in (row.get("issueTypes") or "").split(" | "):
                if issue:
                    issue_type_counts[issue] += 1
            comment = (row.get("comment") or "").strip()
            if comment:
                comment_counts[comment] += 1
        for error in result.get("errors") or []:
            indexed_errors.append((idx, error))

    if workers == 1:
        for idx, record in enumerate(records, 1):
            consume(
                _fetch_reviews_for_one_record(
                    sess=sess,
                    record=record,
                    index=idx,
                    total=total,
                    qa_report_url=qa_report_url,
                    reviews_url=reviews_url,
                    base_url=base_url,
                    host_header=host_header,
                    modes=modes,
                    include_points=include_points,
                    fetch_statistics=fetch_statistics,
                    keep_record_details=keep_record_details,
                    timeout=timeout,
                )
            )
            if sleep_sec > 0:
                time.sleep(sleep_sec)
    else:
        print(f"Concurrent record workers: {workers}")
        thread_state = threading.local()

        def worker_fetch(idx: int, record: dict[str, Any]) -> dict[str, Any]:
            if not hasattr(thread_state, "sess"):
                thread_state.sess = _clone_session(sess)
            return _fetch_reviews_for_one_record(
                sess=thread_state.sess,
                record=record,
                index=idx,
                total=total,
                qa_report_url=qa_report_url,
                reviews_url=reviews_url,
                base_url=base_url,
                host_header=host_header,
                modes=modes,
                include_points=include_points,
                fetch_statistics=fetch_statistics,
                keep_record_details=keep_record_details,
                timeout=timeout,
            )

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(worker_fetch, idx, record): idx
                for idx, record in enumerate(records, 1)
            }
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    consume(future.result())
                except Exception as exc:
                    indexed_errors.append(
                        (
                            idx,
                            {
                                **records[idx - 1],
                                "stage": "record_fetch",
                                "error": f"{type(exc).__name__}: {exc}",
                            },
                        )
                    )
                    print(f"[{idx}/{total}] record fetch failed: {type(exc).__name__}: {exc}")
                if sleep_sec > 0:
                    time.sleep(sleep_sec)

    output_records = [row for _idx, row in sorted(indexed_records, key=lambda item: item[0])]
    issue_rows = [row for _idx, row in sorted(indexed_issue_rows, key=lambda item: item[0])]
    errors = [row for _idx, row in sorted(indexed_errors, key=lambda item: item[0])]

    return {
        "records": output_records,
        "issue_rows": issue_rows,
        "errors": errors,
        "issue_type_counts": dict(issue_type_counts.most_common()),
        "comment_counts": dict(comment_counts.most_common()),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "jobId",
        "jobDisplayId",
        "jobName",
        "projectId",
        "recordId",
        "originDocId",
        "finalDocId",
        "recordStatus",
        "labelingTime",
        "lastModifiedTime",
        "qaRejectCountForJob",
        "needReworkTasks",
        "qaJobId",
        "qaTaskId",
        "qaWorkerId",
        "qaMode",
        "qaReviewId",
        "frameId",
        "reviewNumber",
        "reviewItemId",
        "qaType",
        "instanceType",
        "pointCount",
        "visible",
        "issueTypes",
        "comment",
        "reviewStatisticsUrl",
        "qaAnnotationKey",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch QA review issue details from current full-data records."
    )
    parser.add_argument("--full-data", required=True, help="full_data_*.json from fetch_current_full_data.py")
    parser.add_argument("--cookie-file", default=str(default_cookie_file()))
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--host-header", default=DEFAULT_HOST_HEADER)
    parser.add_argument("--output-prefix", default="", help="Default: data/scanner/qa_review_issues_<uid>_<stamp>")
    parser.add_argument("--all-records", action="store_true", help="Try every record with a finalDocId, not only likely QA candidates")
    parser.add_argument("--max-records", type=int, default=0)
    parser.add_argument("--modes", default="QA_RW,QA_RO", help="Comma-separated annotation modes to read")
    parser.add_argument("--include-points", action="store_true", help="Keep huge points arrays in JSON output")
    parser.add_argument(
        "--issue-rows-only",
        action="store_true",
        help="Store only flattened issue rows in JSON/CSV; omit per-record review detail blocks.",
    )
    parser.add_argument("--no-fetch-statistics", action="store_true", help="Skip fetching small statistics.review aggregate files")
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument("--sleep-sec", type=float, default=0.0)
    parser.add_argument("--workers", type=int, default=1, help="Concurrent records to fetch (default: 1)")
    args = parser.parse_args()

    base_url = _normalize_base_url(args.base_url)
    host_header = args.host_header.strip()
    full_data = _load_json(args.full_data)
    user_id = str(full_data.get("user_id") or full_data.get("userId") or "unknown")
    modes = {m.strip().upper() for m in args.modes.split(",") if m.strip()}
    modes = modes or DEFAULT_LIMIT_MODES
    records = _candidate_records(full_data, scan_all=args.all_records)
    if args.max_records > 0:
        records = records[: args.max_records]

    print(f"Candidate records: {len(records)}")
    cookie = _load_cookie(args.cookie_file)
    sess = _session(cookie, host_header)
    fetched = fetch_reviews_for_records(
        sess=sess,
        records=records,
        base_url=base_url,
        host_header=host_header,
        modes=modes,
        include_points=args.include_points,
        fetch_statistics=not args.no_fetch_statistics,
        timeout=args.timeout,
        sleep_sec=args.sleep_sec,
        workers=args.workers,
        keep_record_details=not args.issue_rows_only,
    )

    generated_at = datetime.now().isoformat()
    summary = {
        "candidate_records": len(records),
        "records_with_reviews": len(fetched["records"]),
        "issue_rows": len(fetched["issue_rows"]),
        "errors": len(fetched["errors"]),
        "issue_type_counts": fetched["issue_type_counts"],
        "comment_counts": fetched["comment_counts"],
    }
    report = {
        "source": "qa-review-issue-fetcher",
        "generated_at": generated_at,
        "user_id": user_id,
        "input_full_data": str(args.full_data),
        "modes": sorted(modes),
        "summary": summary,
        "records": fetched["records"],
        "issue_rows": fetched["issue_rows"],
        "errors": fetched["errors"],
        "notes": [
            "Review detail auditId is inferred from QA annotation filename as <qaJobId>.<qaTaskId>.<recordId>.review.",
            "Points arrays are omitted by default to keep output small; pass --include-points to retain them.",
        ],
    }

    prefix = args.output_prefix
    if not prefix:
        prefix = str(runtime_dir() / f"qa_review_issues_{user_id}_{_stamp()}")
    json_path = Path(prefix).with_suffix(".json")
    csv_path = Path(prefix).with_suffix(".csv")
    _write_json(json_path, report)
    _write_csv(csv_path, fetched["issue_rows"])

    print("\nDone")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"JSON: {json_path}")
    print(f"CSV:  {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
