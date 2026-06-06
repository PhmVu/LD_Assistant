from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from services.ld_ai.intent_parser import IntentResult


@dataclass
class PolishValidation:
    accepted: bool
    status: str
    length_ratio: float
    warnings: list[str]


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text or "")
    without_marks = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", without_marks.lower()).strip()


def length_limits(intent: IntentResult) -> tuple[float, float]:
    return (0.9, 1.5 if intent.wants_long_explanation else 1.3)


def length_contract_text(core_answer: str, intent: IntentResult) -> str:
    min_ratio, max_ratio = length_limits(intent)
    core_len = max(len(core_answer or ""), 1)
    return (
        f"Do dai cau tra loi polish phai nam trong khoang "
        f"{int(core_len * min_ratio)}-{int(core_len * max_ratio)} ky tu "
        f"(core hien co {core_len} ky tu)."
    )


def _has_fishbone_line_split_contradiction(candidate_norm: str) -> bool:
    has_old_new = (
        ("line cu" in candidate_norm or "duong mau do" in candidate_norm)
        and ("line moi" in candidate_norm or "duong mau xanh" in candidate_norm)
    )
    if not has_old_new:
        return False
    bad_phrases = (
        "can phan biet",
        "phai phan biet",
        "bat buoc phan biet",
        "nen phan biet",
        "phan biet ro",
        "can duoc phan biet",
        "tach rieng",
        "gan rieng",
    )
    return any(phrase in candidate_norm for phrase in bad_phrases)


def _missing_fishbone_protected_fact(candidate_norm: str, protected_norm: str) -> bool:
    if "khong phan biet" not in protected_norm and "ve binh thuong" not in protected_norm:
        return False
    return not (
        "khong phan biet" in candidate_norm
        or "khong can phan biet" in candidate_norm
        or "ve binh thuong" in candidate_norm
    )


def _has_centerline_break_contradiction(candidate_norm: str, protected_norm: str) -> bool:
    protected_requires_break = (
        "phai break" in protected_norm
        or "phai tach" in protected_norm
        or "phai cat" in protected_norm
        or "khong duoc keo mot centerline" in protected_norm
    )
    if not protected_requires_break:
        return False

    bad_phrases = (
        "khong can break",
        "khong can cat",
        "khong can tach",
        "khong phai break",
        "khong phai cat",
        "khong phai tach",
        "giu nguyen centerline xuyen suot",
        "keo mot centerline xuyen suot",
    )
    has_bad_phrase = any(phrase in candidate_norm for phrase in bad_phrases)
    if not has_bad_phrase:
        return False

    conditional_ok = (
        "neu khong co thay doi" in candidate_norm
        or "neu cung thuoc tinh" in candidate_norm
        or "neu khong doi" in candidate_norm
    )
    return not conditional_ok


def validate_polish(
    candidate: str,
    *,
    core_answer: str,
    protected_facts: list[str],
    intent: IntentResult,
) -> PolishValidation:
    warnings: list[str] = []
    candidate = (candidate or "").strip()
    core_answer = core_answer or ""
    core_len = max(len(core_answer), 1)
    length_ratio = len(candidate) / core_len if candidate else 0.0

    if not candidate:
        warnings.append("empty_llm_response")

    min_ratio, max_ratio = length_limits(intent)
    if candidate and length_ratio < min_ratio:
        warnings.append(f"too_short:{length_ratio:.2f}< {min_ratio:.2f}")
    if candidate and length_ratio > max_ratio:
        warnings.append(f"too_long:{length_ratio:.2f}> {max_ratio:.2f}")

    candidate_norm = normalize_text(candidate)
    protected_norm = normalize_text(" ".join(protected_facts))

    if intent.marking_type == "fishbone":
        if _has_fishbone_line_split_contradiction(candidate_norm):
            warnings.append("fishbone_old_new_line_contradiction")
        if _missing_fishbone_protected_fact(candidate_norm, protected_norm):
            warnings.append("missing_fishbone_project_fact")

    if _has_centerline_break_contradiction(candidate_norm, protected_norm):
        warnings.append("case_analysis_break_decision_contradiction")

    accepted = not warnings
    return PolishValidation(
        accepted=accepted,
        status="accepted" if accepted else "rejected",
        length_ratio=round(length_ratio, 3),
        warnings=warnings,
    )
