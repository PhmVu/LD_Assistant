from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field

from core.config import settings
from services.ld_ai.llm_client import LLMClient

# ── Known drawing kinds that AI can return ────────────────────────────────────
VALID_DRAWING_KINDS = {
    "dashed", "solid", "edge", "arrow", "crosswalk", "stop_line",
    "double_yellow", "yellow_solid_dash", "fishbone", "stop_bar_double",
    "missing_lane", "wrong_color", "wrong_type", "wrong_arrow", "offset",
}

# ── Vision prompt: chuyên sâu về vạch kẻ đường ───────────────────────────────
_VISION_PROMPT = """\
Bạn là chuyên gia phân tích vạch kẻ đường cho hệ thống annotation xe tự lái.
Nhìn vào ảnh và phân tích theo đúng thứ tự sau:

1. **Loại vạch**: xác định rõ loại (chọn một hoặc nhiều):
   - vạch trắng đứt (white dashed) — phân làn, cho phép vượt
   - vạch trắng liền (white solid) — mép đường, cấm lấn làn
   - vạch vàng liền (yellow solid) — tim đường 2 chiều, cấm vượt
   - vạch vàng đứt (yellow dashed) — tim đường 2 chiều, được vượt
   - vạch đôi vàng (double yellow) — phân đường 2 chiều không vượt
   - vàng liền + đứt (yellow solid+dash) — 1 chiều cấm, 1 chiều được vượt
   - mũi tên chỉ hướng (arrow) — chỉ hướng đi trong làn
   - xương cá / fishbone — vùng nhập làn trên cao tốc
   - zebra / vạch qua đường — vùng dành cho người đi bộ
   - vạch dừng (stop line) — ngang đường trước đèn tín hiệu

2. **Màu sắc chính**: trắng (#f8fafc) hay vàng (#facc15) hay khác

3. **Lỗi annotation** (nếu có): thiếu vạch, sai màu, sai loại, lệch offset, sai hướng

4. **Drawing kind** (bắt buộc, chọn 1 trong danh sách):
   dashed | solid | edge | arrow | crosswalk | stop_line |
   double_yellow | yellow_solid_dash | fishbone | stop_bar_double |
   missing_lane | wrong_color | wrong_type | wrong_arrow | offset

Trả lời bằng tiếng Việt, ngắn gọn rõ ràng. Dòng cuối bắt buộc:
drawing_kind: <kind>
"""

# ── Image analysis prompt dùng khi user gửi ảnh trong chat ───────────────────
_CHAT_IMAGE_PROMPT = """\
Bạn là chuyên gia annotation vạch kẻ đường. Người dùng gửi ảnh này để hỏi về vạch kẻ đường.

Phân tích ảnh và mô tả:
- Loại vạch đang thấy (trắng đứt/liền, vàng đứt/liền, đôi vàng, xương cá, mũi tên, zebra...)
- Màu sắc và đặc điểm kỹ thuật (độ dày ước tính, khoảng cách segment nếu là vạch đứt)
- Nếu thấy lỗi annotation: mô tả lỗi cụ thể (thiếu vạch ở đâu, sai màu như thế nào...)
- Tiêu chuẩn áp dụng được (QCVN 41:2019 hay guideline annotation nội bộ)

Cuối cùng ghi: drawing_kind: <loại phù hợp nhất>
"""


@dataclass
class VisionResult:
    summary: str
    category: str
    confidence: float
    drawing_kind: str = field(default="solid")   # AI tự phân loại từ ảnh


def _extract_drawing_kind(text: str) -> str:
    """Trích drawing_kind từ dòng cuối reply của AI."""
    if not text:
        return "solid"
    # Tìm pattern "drawing_kind: <value>"
    match = re.search(r"drawing_kind\s*:\s*(\w+)", text, re.IGNORECASE)
    if match:
        kind = match.group(1).strip().lower()
        if kind in VALID_DRAWING_KINDS:
            return kind
    # Fallback: infer từ keywords trong text
    t = text.lower()
    if "double_yellow" in t or "đôi vàng" in t:
        return "double_yellow"
    if "fishbone" in t or "xương cá" in t:
        return "fishbone"
    if "arrow" in t or "mũi tên" in t:
        return "arrow"
    if "crosswalk" in t or "zebra" in t:
        return "crosswalk"
    if "stop_line" in t or "vạch dừng" in t:
        return "stop_line"
    if "missing" in t or "thiếu vạch" in t:
        return "missing_lane"
    if "wrong_color" in t or "sai màu" in t:
        return "wrong_color"
    if "offset" in t or "lệch" in t:
        return "offset"
    if "dashed" in t or "đứt" in t:
        return "dashed"
    if "edge" in t or "lề" in t:
        return "edge"
    return "solid"


def _build_vision_message(b64: str, prompt: str, provider: str) -> dict:
    """Tạo message structure đúng format cho từng provider."""
    if provider == "ollama":
        return {"role": "user", "content": prompt, "images": [b64]}
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ],
    }


def summarize_image(image_bytes: bytes | None, *, chat_mode: bool = False) -> VisionResult | None:
    """
    Phân tích ảnh vạch kẻ đường.

    Args:
        image_bytes: raw image bytes
        chat_mode: True khi user gửi ảnh trong chat (dùng prompt ngắn hơn)

    Returns:
        VisionResult với summary, category, confidence, drawing_kind
    """
    if not image_bytes:
        return None

    size_kb = len(image_bytes) / 1024.0

    if settings.LD_LLM_ENABLED:
        try:
            client = LLMClient()
            if not client.supports_vision(settings.LD_VISION_MODEL):
                raise RuntimeError("Vision model not supported")

            b64 = base64.b64encode(image_bytes).decode("utf-8")
            prompt = _CHAT_IMAGE_PROMPT if chat_mode else _VISION_PROMPT
            message = _build_vision_message(b64, prompt, client.provider)

            summary_text = client.chat(
                model=settings.LD_VISION_MODEL,
                messages=[message],
                temperature=0.2,
                max_tokens=500,   # Đủ dài để AI giải thích chi tiết
            )

            if summary_text and summary_text.strip():
                kind = _extract_drawing_kind(summary_text)
                return VisionResult(
                    summary=summary_text.strip(),
                    category="road-marking",
                    confidence=0.82,
                    drawing_kind=kind,
                )
        except Exception:
            pass

    # Fallback khi LLM disabled hoặc lỗi
    fallback_summary = (
        f"Đã nhận ảnh {size_kb:.1f} KB. "
        "LD AI phân tích ảnh vạch kẻ đường, lề đường, mũi tên chỉ hướng và biển báo mặt đường. "
        "Vui lòng mô tả thêm câu hỏi để AI hỗ trợ chính xác hơn."
    )
    return VisionResult(
        summary=fallback_summary,
        category="unknown",
        confidence=0.2,
        drawing_kind="solid",
    )


def analyze_doc_image(image_bytes: bytes) -> VisionResult | None:
    """
    Phân tích ảnh từ tài liệu tri thức (bảng vạch kẻ đường, QCVN diagram...).
    Dùng prompt chuyên sâu hơn để extract thông tin kỹ thuật.
    """
    return summarize_image(image_bytes, chat_mode=False)
