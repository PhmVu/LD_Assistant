from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime


@dataclass
class MemoryRecord:
    id: str
    key: str
    content: Dict[str, Any]
    metrics: Dict[str, float]
    timestamp: str


class SynapticVortex:
    """Simple sharded memory store keyed by `key` (e.g., regime or marking_type).

    Stores JSON file under data/ld_memory/vortex.json with structure { key: [records...] }
    """

    def __init__(self, root: Path | None = None):
        if root is None:
            root = Path(__file__).resolve().parents[3] / "data" / "ld_memory"
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "vortex.json"
        self._store: Dict[str, List[Dict[str, Any]]] = {}
        self._load()

    def _load(self) -> None:
        try:
            if self.path.exists():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._store = raw
        except Exception:
            self._store = {}

    def _save(self) -> None:
        try:
            self.path.write_text(json.dumps(self._store, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def add(self, key: str, content: Dict[str, Any], metrics: Optional[Dict[str, float]] = None) -> MemoryRecord:
        metrics = metrics or {}
        rec = MemoryRecord(
            id=f"rec_{int(datetime.utcnow().timestamp() * 1000)}",
            key=key,
            content=content,
            metrics={k: float(v) for k, v in (metrics or {}).items()},
            timestamp=datetime.utcnow().isoformat(),
        )
        bucket = self._store.get(key, [])
        bucket.insert(0, asdict(rec))
        # cap bucket size to 200
        self._store[key] = bucket[:200]
        self._save()
        return rec

    def recall(self, key: str, top_n: int = 5) -> List[MemoryRecord]:
        lst = self._store.get(key, [])
        out: List[MemoryRecord] = []
        for item in lst[:top_n]:
            out.append(MemoryRecord(**item))
        return out

    def list_keys(self) -> List[str]:
        return list(self._store.keys())


# singleton
_GLOBAL_VORTEX: Optional[SynapticVortex] = None


def get_global_vortex() -> SynapticVortex:
    global _GLOBAL_VORTEX
    if _GLOBAL_VORTEX is None:
        _GLOBAL_VORTEX = SynapticVortex()
    return _GLOBAL_VORTEX
