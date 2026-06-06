from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

from core.config import settings
from services.docs_index import DocsIndex


_TOKEN_RE = re.compile(r"[a-z0-9_]+", re.IGNORECASE)
_STOPWORDS = {
    "la", "gi", "va", "voi", "cho", "cua", "cac", "nhung", "trong", "khi",
    "thi", "the", "nao", "can", "phai", "duoc", "khong", "mot", "neu",
}


def normalize_text(text: str) -> str:
    value = (text or "").lower().replace("đ", "d")
    value = unicodedata.normalize("NFD", value)
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", value).strip()


def query_tokens(text: str) -> set[str]:
    return {
        token
        for token in _TOKEN_RE.findall(normalize_text(text))
        if len(token) > 1 and token not in _STOPWORDS
    }


def _score_doc(query: str, doc: Dict[str, Any]) -> int:
    q_tokens = query_tokens(query)
    haystack = normalize_text(
        " ".join(
            str(doc.get(key) or "")
            for key in ("name", "preview", "summary", "marking_type")
        )
        + " "
        + " ".join(doc.get("keywords") or [])
    )
    score = sum(3 for token in q_tokens if token in haystack)
    name = normalize_text(str(doc.get("name") or ""))
    if any(token in name for token in q_tokens):
        score += 4
    if "lane" in name or "mark" in name or "vach" in name:
        score += 1
    return score


def retrieve_references(query: str, limit: int = 6) -> List[Dict[str, Any]]:
    docs = DocsIndex().list_docs()
    ranked = sorted(docs, key=lambda d: _score_doc(query, d), reverse=True)
    return ranked[:limit]


def _chunk_text(text: str, target_size: int = 1100, overlap: int = 160) -> list[str]:
    clean = re.sub(r"\n{3,}", "\n\n", text or "").strip()
    if not clean:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", clean) if p.strip()]
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 2 <= target_size:
            current = f"{current}\n\n{para}".strip()
            continue
        if current:
            chunks.append(current)
        if len(para) <= target_size:
            current = para
        else:
            start = 0
            while start < len(para):
                chunks.append(para[start:start + target_size])
                start += max(1, target_size - overlap)
            current = ""
    if current:
        chunks.append(current)
    return chunks


def _doc_text_paths() -> list[Path]:
    if not settings.DOCS_DIR.exists():
        return []
    return sorted(p for p in settings.DOCS_DIR.glob("*.txt") if p.is_file())


@lru_cache(maxsize=1)
def _load_doc_chunks() -> tuple[dict[str, Any], ...]:
    chunks: list[dict[str, Any]] = []
    for path in _doc_text_paths():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for idx, chunk in enumerate(_chunk_text(text), start=1):
            chunks.append({
                "name": path.name,
                "source": str(path),
                "chunk_index": idx,
                "text": chunk,
                "normalized": normalize_text(chunk),
            })
    return tuple(chunks)


def clear_knowledge_cache() -> None:
    _load_doc_chunks.cache_clear()


def retrieve_doc_chunks(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    tokens = query_tokens(query)
    if not tokens:
        tokens = query_tokens("vach lane annotation")
    phrase = normalize_text(query)
    ranked: list[tuple[int, dict[str, Any]]] = []

    for chunk in _load_doc_chunks():
        text_norm = str(chunk.get("normalized") or "")
        name_norm = normalize_text(str(chunk.get("name") or ""))
        score = 0
        for token in tokens:
            if token in text_norm:
                score += 3
            if token in name_norm:
                score += 5
        if phrase and phrase in text_norm:
            score += 12
        if score <= 0:
            continue
        ranked.append((score, chunk))

    ranked.sort(key=lambda item: item[0], reverse=True)
    out: list[dict[str, Any]] = []
    for score, chunk in ranked[:limit]:
        item = {k: v for k, v in chunk.items() if k != "normalized"}
        item["score"] = score
        item["preview"] = str(item.get("text") or "")[:700]
        out.append(item)
    return out
