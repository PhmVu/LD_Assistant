from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from services.drawing_engine import build_lane_marking_instructions
from services.ld_ai.domain_guard import check_text_domain
from services.ld_ai.intent_parser import parse_intent
from services.ld_ai.knowledge_retriever import retrieve_references
from services.ld_ai.vision_stub import summarize_image
from services.ld_ai.ld_brain import LDBrain


@dataclass
class LDChatResponse:
    answer: str
    references: List[Dict[str, Any]]
    drawing: Dict[str, Any] | None
    moderation: Dict[str, Any] | None


class LDAIOrchestrator:
    def __init__(self) -> None:
        self.name = "LD"
        self.version = "2.0"
        self.brain = LDBrain()

    def chat(
        self,
        message: str,
        history: Optional[List[Dict[str, Any]]] = None,
        image_bytes: Optional[bytes] = None,
    ) -> LDChatResponse:
        guard = check_text_domain(message, has_image=bool(image_bytes))
        if not guard.allowed:
            default_drawing = build_lane_marking_instructions("default", "auto")
            return LDChatResponse(
                answer=(
                    "Mình chỉ hỗ trợ các câu hỏi liên quan đến vạch kẻ đường, lề đường, "
                    "mũi tên chỉ hướng hoặc biển báo trên mặt đường."
                ),
                references=[],
                drawing={"base": default_drawing, "variants": []},
                moderation={
                    "allowed": False,
                    "reason": guard.reason,
                    "matched": guard.matched,
                },
            )

        intent = parse_intent(message)

        # ── Vision: phân tích ảnh nếu có, extract drawing_kind ─────────────
        vision_drawing_kind: Optional[str] = None
        vision_note: Optional[str] = None
        if image_bytes:
            vision = summarize_image(image_bytes, chat_mode=True)
            if vision:
                vision_note = vision.summary
                vision_drawing_kind = vision.drawing_kind
                # Cập nhật intent với vision_kind để brain dùng
                if vision_drawing_kind and vision_drawing_kind not in {"solid", "default"}:
                    intent.vision_drawing_kind = vision_drawing_kind

        # ── Drawing: ưu tiên vision_kind nếu có ảnh ─────────────────────────
        effective_kind = (
            vision_drawing_kind
            if vision_drawing_kind and vision_drawing_kind not in {"solid", "default", ""}
            else intent.drawing_kind
        )
        drawing = build_lane_marking_instructions(effective_kind, intent.color_hint)

        references = retrieve_references(message)
        brain_result = self.brain.answer(
            message=message,
            intent=intent,
            references=references,
            vision_note=vision_note,
            history=history,
            base_drawing=drawing,
            image_bytes=image_bytes,
            vision_drawing_kind=vision_drawing_kind,
        )

        return LDChatResponse(
            answer=brain_result.answer,
            references=references,
            drawing={
                "base": drawing,
                "variants": brain_result.drawing_candidates or [],
            },
            moderation={
                "allowed": True,
                "reason": guard.reason,
                "matched": guard.matched,
                "intent": {
                    "marking_type": intent.marking_type,
                    "request_type": intent.request_type,
                    "color_hint": intent.color_hint,
                    "wants_long_explanation": intent.wants_long_explanation,
                    "drawing_kind": effective_kind,
                    "case_analysis": brain_result.case_analysis,
                },
                "brain": {
                    "used_llm": brain_result.used_llm,
                    "error": brain_result.error,
                    "tool_call": brain_result.tool_call,
                    "polish_status": brain_result.polish_status,
                    "length_ratio": brain_result.length_ratio,
                    "validation_warnings": brain_result.validation_warnings or [],
                },
                "vision": {
                    "summary": vision_note,
                    "drawing_kind": vision_drawing_kind,
                } if image_bytes else None,
            },
        )


orchestrator = LDAIOrchestrator()
