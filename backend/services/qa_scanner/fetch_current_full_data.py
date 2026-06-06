#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

from scanner_paths import default_cookie_file, runtime_dir

try:
    import requests
except ImportError:
    print("Need requests: pip install requests")
    sys.exit(1)


DEFAULT_BASE_URL = "http://10.79.0.80"
DEFAULT_HOST_HEADER = "global-autolabeling-service.evad.xiaomi.srv"
DEFAULT_PAGE_SIZE = 200


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


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


def _load_jobs(jobs_file: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    path = Path(jobs_file)
    if not path.exists():
        raise FileNotFoundError(f"jobs-file does not exist: {path}")
    payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    if isinstance(payload, dict):
        jobs = payload.get("results") or payload.get("jobs") or []
    elif isinstance(payload, list):
        jobs = payload
        payload = {"source": "list"}
    else:
        raise ValueError("jobs-file must contain a JSON object or array")
    jobs = [job for job in jobs if isinstance(job, dict) and job.get("jobId") is not None]
    return payload, jobs


def _infer_user_id(jobs: list[dict[str, Any]]) -> str:
    counts = Counter(str(job.get("workerId")) for job in jobs if job.get("workerId") is not None)
    if not counts:
        return ""
    return counts.most_common(1)[0][0]


def _preview_body(text: str, limit: int = 600) -> str:
    return (text or "").replace("\r", " ").replace("\n", " ")[:limit]


def _get_json(
    sess: requests.Session,
    base_url: str,
    path: str,
    params: dict[str, Any] | None = None,
    timeout: int = 20,
) -> dict[str, Any]:
    url = f"{base_url}{path}"
    started = time.time()
    try:
        resp = sess.get(url, params=params or {}, timeout=timeout)
    except Exception as exc:
        return {
            "ok": False,
            "path": path,
            "params": params or {},
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_sec": round(time.time() - started, 3),
        }

    item: dict[str, Any] = {
        "ok": resp.status_code == 200,
        "path": path,
        "params": params or {},
        "status_code": resp.status_code,
        "elapsed_sec": round(time.time() - started, 3),
    }
    try:
        item["json"] = resp.json()
    except Exception:
        item["body_preview"] = _preview_body(resp.text)
    return item


def _extract_page(payload: Any) -> tuple[list[Any], int | None, int | None]:
    if not isinstance(payload, dict):
        return [], None, None
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("results", "records", "rows", "items", "list"):
            value = data.get(key)
            if isinstance(value, list):
                total_pages = data.get("totalPages")
                total_elements = data.get("totalElements") or data.get("total")
                return value, _to_int(total_pages), _to_int(total_elements)
    if isinstance(data, list):
        return data, None, len(data)
    for key in ("results", "records", "rows", "items", "list"):
        value = payload.get(key)
        if isinstance(value, list):
            return value, _to_int(payload.get("totalPages")), _to_int(payload.get("totalElements"))
    return [], None, None


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _fetch_paged(
    sess: requests.Session,
    base_url: str,
    path: str,
    base_params: dict[str, Any],
    page_size: int,
    max_pages: int,
    timeout: int,
) -> dict[str, Any]:
    pages: list[dict[str, Any]] = []
    records: list[Any] = []
    page_index = 0
    total_pages: int | None = None
    total_elements: int | None = None

    while True:
        params = dict(base_params)
        params.update({"pageIndex": page_index, "pageSize": page_size})
        result = _get_json(sess, base_url, path, params=params, timeout=timeout)
        pages.append({k: v for k, v in result.items() if k != "json"})

        if not result.get("ok"):
            return {
                "ok": False,
                "path": path,
                "params": base_params,
                "pages": pages,
                "records": records,
                "error": result.get("error") or result.get("body_preview") or result.get("json"),
            }

        page_records, page_total_pages, page_total_elements = _extract_page(result.get("json"))
        if page_total_pages is not None:
            total_pages = page_total_pages
        if page_total_elements is not None:
            total_elements = page_total_elements
        records.extend(page_records)

        print(
            f"  page {page_index + 1}/{total_pages or '?'}: "
            f"{len(page_records)} records"
        )

        page_index += 1
        if total_pages is not None and page_index >= total_pages:
            break
        if max_pages and page_index >= max_pages:
            break
        if not page_records:
            break

    return {
        "ok": True,
        "path": path,
        "params": base_params,
        "totalPages": total_pages,
        "totalElements": total_elements,
        "pages": pages,
        "records": records,
    }


def _ok_json(result: dict[str, Any]) -> Any:
    if not result.get("ok"):
        return result
    return result.get("json")


def _make_session(cookie: str, host_header: str) -> requests.Session:
    sess = requests.Session()
    sess.headers.update(
        {
            "Accept": "application/json",
            "Cookie": cookie,
            "User-Agent": "ld-current-full-data/1.0",
        }
    )
    if host_header:
        sess.headers["Host"] = host_header
    return sess


def _fetch_job_result(
    *,
    job: dict[str, Any],
    cookie: str,
    host_header: str,
    base_url: str,
    page_size: int,
    max_pages_per_job: int,
    timeout: int,
    records_only: bool,
) -> dict[str, Any]:
    sess = _make_session(cookie, host_header)
    job_id = job.get("jobId")
    task_details = _fetch_paged(
        sess,
        base_url,
        "/appen/backend/job/summary/workload/task-detail/list",
        {"jobId": job_id},
        page_size=page_size,
        max_pages=max_pages_per_job,
        timeout=timeout,
    )

    if records_only:
        return {
            "job": job,
            "task_details": task_details,
            "label_summary": {},
            "qa_summary": {},
            "worker_job": {},
            "lifecycle": {},
            "errors": [] if task_details.get("ok") else [{"section": "task_details", "detail": task_details}],
        }

    label_summary = _get_json(
        sess,
        base_url,
        "/appen/backend/job/summary/workload/label/get",
        params={"jobId": job_id},
        timeout=timeout,
    )
    qa_summary = _get_json(
        sess,
        base_url,
        "/appen/backend/job/summary/workload/qa/get",
        params={"jobId": job_id},
        timeout=timeout,
    )
    worker_job = _get_json(
        sess,
        base_url,
        "/appen/backend/job/worker/job",
        params={"jobId": job_id},
        timeout=timeout,
    )
    lifecycle = _get_json(
        sess,
        base_url,
        "/appen/backend/job/worker/lifecycle",
        params={"jobId": job_id},
        timeout=timeout,
    )

    errors = []
    for name, result in (
        ("task_details", task_details),
        ("label_summary", label_summary),
        ("qa_summary", qa_summary),
        ("worker_job", worker_job),
        ("lifecycle", lifecycle),
    ):
        if not result.get("ok"):
            errors.append({"section": name, "detail": result})

    return {
        "job": job,
        "task_details": task_details,
        "label_summary": _ok_json(label_summary),
        "qa_summary": _ok_json(qa_summary),
        "worker_job": _ok_json(worker_job),
        "lifecycle": _ok_json(lifecycle),
        "errors": errors,
    }


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_report(
    jobs: list[dict[str, Any]],
    job_results: list[dict[str, Any]],
    user_id: str,
    fetched_at: str,
) -> dict[str, Any]:
    flattened_records: list[dict[str, Any]] = []
    record_status_counts: Counter[str] = Counter()
    job_status_counts: Counter[str] = Counter()
    jobs_with_records = 0
    jobs_with_errors = 0
    available_tasks = 0
    need_rework_tasks = 0
    accuracies: list[float] = []

    for job in jobs:
        job_status_counts[str(job.get("jobStatus") or job.get("status") or "UNKNOWN")] += 1
        available_tasks += int(job.get("availableTasks") or 0)
        need_rework_tasks += int(job.get("needReworkTasks") or 0)
        accuracy = _number(job.get("accuracy"))
        if accuracy is not None:
            accuracies.append(accuracy)

    for item in job_results:
        job = item.get("job") or {}
        details = item.get("task_details") or {}
        records = details.get("records") or []
        if records:
            jobs_with_records += 1
        if item.get("errors"):
            jobs_with_errors += 1

        context = {
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
            "assignTime": job.get("assignTime"),
            "confirmTime": job.get("confirmTime"),
        }
        for record in records:
            if isinstance(record, dict):
                row = dict(context)
                row.update(record)
                status = str(record.get("status") or "UNKNOWN")
            else:
                row = dict(context)
                row["record"] = record
                status = "UNKNOWN"
            record_status_counts[status] += 1
            flattened_records.append(row)

    accuracy_summary: dict[str, Any] = {}
    if accuracies:
        accuracy_summary = {
            "count": len(accuracies),
            "min": min(accuracies),
            "max": max(accuracies),
            "avg": round(statistics.mean(accuracies), 4),
        }

    return {
        "source": "current-session-full-data",
        "fetched_at": fetched_at,
        "user_id": user_id,
        "summary": {
            "total_jobs": len(jobs),
            "jobs_with_records": jobs_with_records,
            "jobs_with_errors": jobs_with_errors,
            "total_records": len(flattened_records),
            "available_tasks_from_worker_jobs": available_tasks,
            "need_rework_tasks_from_worker_jobs": need_rework_tasks,
            "job_status_counts": dict(job_status_counts),
            "record_status_counts": dict(record_status_counts),
            "accuracy": accuracy_summary,
        },
        "records": flattened_records,
        "jobs": [
            {
                "jobId": job.get("jobId"),
                "jobDisplayId": job.get("jobDisplayId"),
                "jobName": job.get("jobName"),
                "jobType": job.get("jobType"),
                "jobStatus": job.get("jobStatus"),
                "status": job.get("status"),
                "workerId": job.get("workerId"),
                "projectId": job.get("projectId"),
                "projectName": job.get("projectName"),
                "availableTasks": job.get("availableTasks"),
                "needReworkTasks": job.get("needReworkTasks"),
                "accuracy": job.get("accuracy"),
                "assignTime": job.get("assignTime"),
                "confirmTime": job.get("confirmTime"),
            }
            for job in jobs
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch full current-session data from LD worker frontend endpoints."
    )
    parser.add_argument("--cookie-file", default=str(default_cookie_file()))
    parser.add_argument("--jobs-file", required=True)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--host-header", default=DEFAULT_HOST_HEADER)
    parser.add_argument("--user-id", default="")
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--max-pages-per-job", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--workers", type=int, default=1, help="Concurrent jobs to fetch (default: 1)")
    parser.add_argument(
        "--records-only",
        action="store_true",
        help="Only fetch task-detail records; skip per-job summaries/lifecycle.",
    )
    parser.add_argument("--output", default="")
    parser.add_argument("--report-output", default="")
    args = parser.parse_args()

    base_url = _normalize_base_url(args.base_url)
    cookie = _load_cookie(args.cookie_file)
    jobs_payload, jobs = _load_jobs(args.jobs_file)
    if not jobs:
        print("No jobs found in jobs-file")
        return 1

    user_id = args.user_id.strip() or _infer_user_id(jobs)
    stamp = _now_stamp()
    output_path = Path(args.output) if args.output else runtime_dir() / f"full_data_{user_id or 'current'}_{stamp}.json"
    report_path = Path(args.report_output) if args.report_output else runtime_dir() / f"report_{user_id or 'current'}_{stamp}.json"

    sess = requests.Session()
    sess.headers.update(
        {
            "Accept": "application/json",
            "Cookie": cookie,
            "User-Agent": "ld-current-full-data/1.0",
        }
    )
    if args.host_header:
        sess.headers["Host"] = args.host_header

    fetched_at = datetime.now().isoformat()
    job_results: list[dict[str, Any]] = []

    print(f"Worker/user id: {user_id or '(unknown)'}")
    print(f"Jobs to fetch: {len(jobs)}")
    workers = max(1, int(args.workers or 1))
    if workers == 1:
        for idx, job in enumerate(jobs, start=1):
            job_id = job.get("jobId")
            print(f"[{idx}/{len(jobs)}] jobId={job_id} {job.get('jobDisplayId') or ''}")
            job_results.append(
                _fetch_job_result(
                    job=job,
                    cookie=cookie,
                    host_header=args.host_header,
                    base_url=base_url,
                    page_size=args.page_size,
                    max_pages_per_job=args.max_pages_per_job,
                    timeout=args.timeout,
                    records_only=args.records_only,
                )
            )
    else:
        print(f"Concurrent job workers: {workers}")
        indexed_results: list[tuple[int, dict[str, Any]]] = []
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    _fetch_job_result,
                    job=job,
                    cookie=cookie,
                    host_header=args.host_header,
                    base_url=base_url,
                    page_size=args.page_size,
                    max_pages_per_job=args.max_pages_per_job,
                    timeout=args.timeout,
                    records_only=args.records_only,
                ): (idx, job)
                for idx, job in enumerate(jobs, start=1)
            }
            for done, future in enumerate(as_completed(futures), start=1):
                idx, job = futures[future]
                job_id = job.get("jobId")
                try:
                    result = future.result()
                except Exception as exc:
                    result = {
                        "job": job,
                        "task_details": {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                        "label_summary": {},
                        "qa_summary": {},
                        "worker_job": {},
                        "lifecycle": {},
                        "errors": [{"section": "job_fetch", "detail": f"{type(exc).__name__}: {exc}"}],
                    }
                indexed_results.append((idx, result))
                print(f"[{done}/{len(jobs)}] done jobId={job_id} {job.get('jobDisplayId') or ''}")
        job_results = [result for _idx, result in sorted(indexed_results, key=lambda item: item[0])]

    full_data = {
        "source": "current-session-full-data",
        "fetched_at": fetched_at,
        "base_url": base_url,
        "host_header": args.host_header,
        "user_id": user_id,
        "jobs_file": str(args.jobs_file),
        "jobs_payload": jobs_payload,
        "job_results": job_results,
    }
    report = _build_report(jobs, job_results, user_id, fetched_at)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(full_data, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = report["summary"]
    print(f"Saved full data -> {output_path}")
    print(f"Saved report    -> {report_path}")
    print(
        "Summary: "
        f"{summary['total_jobs']} jobs, "
        f"{summary['jobs_with_records']} jobs with records, "
        f"{summary['total_records']} records"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
