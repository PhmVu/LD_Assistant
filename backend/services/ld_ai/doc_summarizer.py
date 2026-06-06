from __future__ import annotations

from typing import Optional, Any, Dict
import json

from core.config import settings
from services.ld_ai.llm_client import LLMClient
from services.ld_ai.vision_stub import analyze_doc_image


def _truncate(text: str, max_len: int = 1200) -> str:
    text = text.strip()
    return text[:max_len] + ("..." if len(text) > max_len else "")


def summarize_text(content: str) -> str:
    snippet = _truncate(content)
    if not settings.LD_LLM_ENABLED:
        return snippet

    client = LLMClient()
    prompt = (
        "Tóm tắt ngắn gọn nội dung tài liệu về vạch kẻ đường (3-5 gạch đầu dòng).\n\n"
        f"{snippet}"
    )
    try:
        return client.chat(
            model=settings.LD_TEXT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=300,
        )
    except Exception:
        return snippet


def summarize_image(image_b64: str) -> Optional[str]:
    """
    Phân tích ảnh từ tài liệu tri thức (bảng vạch kẻ đường, diagram QCVN...).
    Dùng vision_stub với prompt chuyên sâu để extract loại vạch chính xác.
    """
    if not settings.LD_LLM_ENABLED:
        return None
    try:
        import base64
        image_bytes = base64.b64decode(image_b64)
        result = analyze_doc_image(image_bytes)
        if result and result.summary:
            # Thêm drawing_kind vào summary để docs_ingestor dùng
            return f"{result.summary}\n[drawing_kind: {result.drawing_kind}]"
    except Exception:
        pass
    return None


def _safe_json(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].replace("```", "").strip()
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except Exception:
        return None
    return None


def extract_rd_pack(content: str) -> Optional[Dict[str, Any]]:
    snippet = _truncate(content, 1800)
    if not snippet:
        return None
    if not settings.LD_LLM_ENABLED:
        return None

    client = LLMClient()
    prompt = (
        "Bạn là chuyên gia RD (Research/Design) về vạch kẻ đường."
        "Trích xuất tri thức theo JSON thuần (không markdown)."
        "Trả về đúng một JSON với cấu trúc:\n"
        "{\n"
        "  \"title\": string,\n"
        "  \"scope\": string,\n"
        "  \"key_rules\": [string],\n"
        "  \"numeric_params\": [{\"name\": string, \"value\": string, \"unit\": string, \"context\": string}],\n"
        "  \"qa_checks\": [string],\n"
        "  \"edge_cases\": [string],\n"
        "  \"glossary\": [{\"term\": string, \"definition\": string}]\n"
        "}\n\n"
        f"Nội dung tài liệu:\n{snippet}"
    )
    try:
        reply = client.chat(
            model=settings.LD_TEXT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=600,
        )
        return _safe_json(reply)
    except Exception:
        return None
