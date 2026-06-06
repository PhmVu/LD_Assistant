from __future__ import annotations

from pathlib import Path
from typing import List

from fastapi import APIRouter, File, HTTPException, UploadFile

from core.config import settings
from services.docs_index import DocsIndex
from services.docs_ingestor import ingest_directory, ingest_file


router = APIRouter(prefix="/api/ld/docs", tags=["ld-docs"])


docs_index = DocsIndex()


@router.get("")
async def list_docs():
    return {"success": True, "data": docs_index.list_docs()}


@router.get("/{doc_name}/content")
async def get_doc_content(doc_name: str):
    """Return the full extracted text content of a document."""
    # First, try to locate via the index (text_path field)
    all_docs = docs_index.list_docs()
    text_path: Path | None = None
    for doc in all_docs:
        if doc.get("name") == doc_name:
            raw = doc.get("text_path")
            if raw:
                p = Path(raw)
                if p.exists():
                    text_path = p
            break

    # Fallback: derive path from stem
    if text_path is None:
        stem = Path(doc_name).stem
        candidate = settings.DOCS_DIR / f"{stem}.txt"
        if candidate.exists():
            text_path = candidate

    if text_path is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy nội dung tài liệu.")

    try:
        content = text_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi đọc file: {exc}") from exc

    return {"success": True, "content": content}


@router.post("/upload")
async def upload_docs(files: List[UploadFile] = File(...)):
    settings.DOCS_DIR.mkdir(parents=True, exist_ok=True)

    stored = []
    for f in files:
        content = await f.read()
        filename = f.filename or "document"
        safe_name = filename.replace("/", "_").replace("\\", "_")
        dest = settings.DOCS_DIR / safe_name
        dest.write_bytes(content)

        doc_meta = ingest_file(dest, content_type=f.content_type)
        stored.append(doc_meta)

    return {"success": True, "data": stored}


@router.post("/ingest-local")
async def ingest_local_docs():
    """Ingest documents from LD/docs directory into storage index."""
    results = ingest_directory(settings.DOCS_SOURCE_DIR)
    return {"success": True, "data": results}
