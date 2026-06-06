from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.config import settings


class DocsIndex:
    def __init__(self, index_path: Path | None = None) -> None:
        self.index_path = index_path or settings.INDEX_PATH
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.index_path.exists():
            self._write({"docs": []})

    def _read(self) -> dict[str, Any]:
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except Exception:
            return {"docs": []}

    def _write(self, payload: dict[str, Any]) -> None:
        self.index_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def add_doc(self, doc: dict[str, Any]) -> None:
        payload = self._read()
        docs = payload.get("docs", [])
        docs.append(doc)
        payload["docs"] = docs
        self._write(payload)

    def upsert_doc(self, doc: dict[str, Any]) -> None:
        payload = self._read()
        docs = payload.get("docs", [])
        name = doc.get("name")
        if isinstance(docs, list) and name:
            updated = False
            for i, item in enumerate(docs):
                if isinstance(item, dict) and item.get("name") == name:
                    docs[i] = doc
                    updated = True
                    break
            if not updated:
                docs.append(doc)
            payload["docs"] = docs
            self._write(payload)
            return
        self.add_doc(doc)

    def list_docs(self) -> list[dict[str, Any]]:
        payload = self._read()
        docs = payload.get("docs", [])
        if isinstance(docs, list):
            return docs
        return []
