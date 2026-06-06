from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from core.config import settings
from services.drawing_engine import build_lane_marking_instructions
from services.ld_ai.council_bridge import get_council_hints
from services.ld_ai.evolver import evolve_drawing_candidates
from services.ld_ai.intent_parser import IntentResult
from services.ld_ai.llm_client import LLMClient
from services.ld_ai.polish_guard import length_contract_text, validate_polish
from services.ld_ai.response_builder import build_response
from services.ld_ai.synaptic_vortex import get_global_vortex


@dataclass
class BrainResult:
    answer: str
    used_llm: bool
    error: Optional[str]
    drawing_candidates: Optional[List[Dict[str, Any]]]
    tool_call: Optional[Dict[str, Any]]
    polish_status: str = "fallback"
    length_ratio: Optional[float] = None
    validation_warnings: Optional[List[str]] = None
    case_analysis: Optional[Dict[str, Any]] = None


class LDBrain:
    def __init__(self) -> None:
        self.client = LLMClient()
        self.vortex = get_global_vortex()

    def _build_system_prompt(self, wants_long: bool = False) -> str:
        depth = (
            "Trả lời dài, giàu chi tiết, có ví dụ thực tế và giải thích rõ vì sao."
            if wants_long
            else "Trả lời rõ ràng, đủ ý, tự nhiên như chuyên gia hỗ trợ đồng nghiệp."
        )
        return (
            "Bạn là LD AI, chuyên gia annotation vạch kẻ đường, road edge, lane centerline, "
            "mũi tên, stop line, crosswalk, diversion area và lỗi QA cho dữ liệu tự lái. "
            "Luôn trả lời bằng tiếng Việt. Không dùng tiếng Trung. Không bịa nếu tài liệu không nói rõ. "
            "Bạn chỉ là lớp biên tập câu chữ cho câu trả lời lõi nội bộ: giữ nguyên kết luận, giữ nguyên quy tắc, "
            "không đảo nghĩa, không thêm luật mới, không biến ví dụ thành quy định bắt buộc. "
            "Nếu thấy câu trả lời lõi và tài liệu có vẻ khác kiến thức phổ thông, vẫn phải theo lõi nội bộ. "
            f"{depth} Khi giải thích lỗi, luôn nêu: lỗi là gì, vì sao sai, sửa thế nào."
        )

    def _compact_text(self, text: str, max_len: int) -> str:
        cleaned = (text or "").strip()
        if len(cleaned) <= max_len:
            return cleaned
        half = max_len // 2
        return cleaned[:half] + "\n...\n" + cleaned[-half:]

    def _truncate(self, text: str, max_len: int) -> str:
        text = (text or "").strip()
        return text[:max_len] + ("..." if len(text) > max_len else "")

    def _render_history(self, history: Optional[List[Dict[str, Any]]]) -> str:
        if not history:
            return ""
        lines: list[str] = []
        for turn in history[-5:]:
            role = turn.get("role")
            content = str(turn.get("content") or "").strip()
            if role not in {"user", "assistant"} or not content:
                continue
            label = "User" if role == "user" else "LD AI"
            lines.append(f"{label}: {self._truncate(content, 260)}")
        return "\n".join(lines)

    def _build_user_prompt(
        self,
        message: str,
        history_text: str,
        intent: IntentResult,
        references: List[Dict[str, Any]],
        vision_note: Optional[str],
        fallback: str,
        protected_facts: List[str],
        case_analysis: Optional[Dict[str, Any]],
        drawing_info: Optional[Dict[str, Any]],
    ) -> str:
        ref_snippets: list[str] = []
        for doc in references[:6]:
            name = doc.get("name") or "LD data"
            preview = doc.get("preview") or doc.get("summary") or doc.get("text") or ""
            preview = self._compact_text(str(preview), 650)
            if preview:
                ref_snippets.append(f"[{name}] {preview}")

        drawing_ctx = ""
        if drawing_info:
            drawing_ctx = (
                f"Drawing scene: {drawing_info.get('scene') or 'default'}; "
                f"note: {drawing_info.get('note') or ''}; "
                f"road_config: {drawing_info.get('road_config') or {}}"
            )

        parts = [
            f"Câu hỏi người dùng: {message}",
            (
                "Intent đã parse: "
                f"marking_type={intent.marking_type}; request_type={intent.request_type}; "
                f"drawing_kind={intent.drawing_kind}; color={intent.color_hint or 'auto'}"
            ),
            "Câu trả lời lõi nội bộ đã dựng sẵn, hãy giữ đúng ý và chỉ làm nó tự nhiên, sắc sảo hơn:\n"
            + fallback,
            "Các ý bắt buộc phải giữ nguyên, không được đảo nghĩa:\n"
            + "\n".join(f"- {fact}" for fact in protected_facts[:8]),
            length_contract_text(fallback, intent),
        ]
        if case_analysis:
            parts.append(
                "CaseAnalysis của core, model ngoài chỉ được polish theo quyết định này:\n"
                + str(case_analysis)
            )
        if history_text:
            parts.append("Lịch sử gần đây:\n" + history_text)
        if vision_note:
            parts.append("Ghi chú từ ảnh:\n" + vision_note)
        if ref_snippets:
            parts.append("Tri thức/tài liệu liên quan:\n" + "\n".join(ref_snippets))
        if drawing_ctx:
            parts.append("Minh họa đi kèm:\n" + drawing_ctx)
        parts.append(
            "Yêu cầu trả lời: chỉ polish từ câu trả lời lõi; trình bày thân thiện, mạch lạc hơn, "
            "dài hơn vừa phải trong giới hạn độ dài ở trên; không dùng markdown phức tạp; "
            "nếu cần liệt kê thì dùng số 1., 2., 3."
        )
        return "\n\n".join(parts)

    def _repair_polish(
        self,
        *,
        model: str,
        intent: IntentResult,
        fallback: str,
        protected_facts: List[str],
        case_analysis: Optional[Dict[str, Any]],
        invalid_reply: str,
        warnings: List[str],
    ) -> str:
        protected = "\n".join(f"- {fact}" for fact in protected_facts[:8])
        prompt = (
            "Bản polish trước bị loại vì: " + ", ".join(warnings) + "\n\n"
            "Hãy viết lại dựa 100% trên câu trả lời lõi. Không thêm quy tắc mới, không đảo nghĩa, "
            "không biến ví dụ thành luật bắt buộc.\n\n"
            f"{length_contract_text(fallback, intent)}\n\n"
            "Ý bắt buộc giữ nguyên:\n"
            f"{protected}\n\n"
            "CaseAnalysis core bắt buộc giữ nguyên:\n"
            f"{case_analysis or {}}\n\n"
            "Câu trả lời lõi:\n"
            f"{fallback}\n\n"
            "Bản polish bị loại để tham khảo lỗi, không được sao chép nếu nó mâu thuẫn:\n"
            f"{self._compact_text(invalid_reply, 1200)}"
        )
        return self.client.chat(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Bạn là editor kiểm lỗi cho LD AI. Nhiệm vụ duy nhất là viết lại câu trả lời lõi "
                        "bằng tiếng Việt tự nhiên hơn nhưng giữ nguyên toàn bộ facts được bảo vệ."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=min(settings.LD_TEMPERATURE, 0.2),
            max_tokens=1800 if intent.wants_long_explanation else settings.LD_MAX_TOKENS,
        ).strip()

    def _external_llm_available(self) -> bool:
        if not settings.LD_LLM_ENABLED:
            return False
        provider = settings.LD_LLM_PROVIDER.strip().lower()
        if provider == "siliconflow" and not settings.LD_LLM_API_KEY:
            return False
        return provider in {"siliconflow", "ollama"}

    def _resolve_drawing(
        self,
        intent: IntentResult,
        base_drawing: Optional[Dict[str, Any]],
        vision_drawing_kind: Optional[str] = None,
    ) -> Dict[str, Any]:
        if base_drawing and base_drawing.get("style"):
            return base_drawing
        if vision_drawing_kind and vision_drawing_kind not in {"solid", "default", ""}:
            kind = vision_drawing_kind
        else:
            kind = intent.drawing_kind or intent.marking_type or "default"
        return build_lane_marking_instructions(kind, intent.color_hint or "auto")

    def answer(
        self,
        *,
        message: str,
        intent: IntentResult,
        references: List[Dict[str, Any]],
        vision_note: Optional[str],
        history: Optional[List[Dict[str, Any]]] = None,
        base_drawing: Optional[Dict[str, Any]] = None,
        image_bytes: Optional[bytes] = None,
        vision_drawing_kind: Optional[str] = None,
    ) -> BrainResult:
        core_payload = build_response(message, intent, None)
        fallback = core_payload.answer
        protected_facts = core_payload.protected_facts
        case_analysis = core_payload.case_analysis.to_dict()
        drawing_tool_output = self._resolve_drawing(intent, base_drawing, vision_drawing_kind)
        tool_call_info: Dict[str, Any] = {
            "tool": "draw_lane_marking",
            "requested": True,
            "used_kind": drawing_tool_output.get("scene", intent.marking_type or "default"),
            "used_color_hint": intent.color_hint or "auto",
            "from_vision": bool(
                vision_drawing_kind and vision_drawing_kind not in {"solid", "default", ""}
            ),
        }

        try:
            hints = get_council_hints(intent.marking_type)
            if hints:
                references = references + [{"name": "CouncilHints", "preview": str(hints)[:400]}]
        except Exception:
            pass

        try:
            recall = self.vortex.recall(f"MARKING::{intent.marking_type or 'general'}", top_n=4)
            recall_text = "\n".join(str(r.content.get("summary", ""))[:260] for r in recall)
            if recall_text:
                references = references + [{"name": "LD Memory", "preview": recall_text}]
        except Exception:
            pass

        try:
            candidates = evolve_drawing_candidates(drawing_tool_output, n=3)
        except Exception:
            candidates = []

        if not self._external_llm_available():
            return BrainResult(
                answer=fallback,
                used_llm=False,
                error=None,
                drawing_candidates=candidates,
                tool_call=tool_call_info,
                polish_status="fallback",
                length_ratio=1.0,
                validation_warnings=[],
                case_analysis=case_analysis,
            )

        try:
            max_tokens = 1800 if intent.wants_long_explanation else settings.LD_MAX_TOKENS
            model = (
                settings.LD_VISION_MODEL
                if image_bytes and self.client.supports_vision(settings.LD_VISION_MODEL)
                else settings.LD_TEXT_MODEL
            )
            reply = self.client.chat(
                model=model,
                messages=[
                    {"role": "system", "content": self._build_system_prompt(intent.wants_long_explanation)},
                    {
                        "role": "user",
                        "content": self._build_user_prompt(
                            message=self._compact_text(message, 1400),
                            history_text=self._render_history(history),
                            intent=intent,
                            references=references,
                            vision_note=vision_note,
                            fallback=fallback,
                            protected_facts=protected_facts,
                            case_analysis=case_analysis,
                            drawing_info=drawing_tool_output,
                        ),
                    },
                ],
                temperature=settings.LD_TEMPERATURE,
                max_tokens=max_tokens,
                image_bytes=image_bytes,
            ).strip()
            if not reply:
                raise ValueError("empty LLM response")

            validation = validate_polish(
                reply,
                core_answer=fallback,
                protected_facts=protected_facts,
                intent=intent,
            )
            if validation.accepted:
                return BrainResult(
                    answer=reply,
                    used_llm=True,
                    error=None,
                    drawing_candidates=candidates,
                    tool_call=tool_call_info,
                    polish_status="accepted",
                    length_ratio=validation.length_ratio,
                    validation_warnings=[],
                    case_analysis=case_analysis,
                )

            try:
                repaired = self._repair_polish(
                    model=model,
                    intent=intent,
                    fallback=fallback,
                    protected_facts=protected_facts,
                    case_analysis=case_analysis,
                    invalid_reply=reply,
                    warnings=validation.warnings,
                )
                repaired_validation = validate_polish(
                    repaired,
                    core_answer=fallback,
                    protected_facts=protected_facts,
                    intent=intent,
                )
                if repaired_validation.accepted:
                    return BrainResult(
                        answer=repaired,
                        used_llm=True,
                        error=None,
                        drawing_candidates=candidates,
                        tool_call=tool_call_info,
                        polish_status="repaired",
                        length_ratio=repaired_validation.length_ratio,
                        validation_warnings=validation.warnings,
                        case_analysis=case_analysis,
                    )
                validation.warnings.extend(
                    f"repair_{warning}" for warning in repaired_validation.warnings
                )
            except Exception as repair_exc:
                validation.warnings.append(f"repair_error:{repair_exc}")

            return BrainResult(
                answer=fallback,
                used_llm=False,
                error="; ".join(validation.warnings),
                drawing_candidates=candidates,
                tool_call=tool_call_info,
                polish_status="rejected",
                length_ratio=validation.length_ratio,
                validation_warnings=validation.warnings,
                case_analysis=case_analysis,
            )
        except Exception as exc:
            return BrainResult(
                answer=fallback,
                used_llm=False,
                error=str(exc),
                drawing_candidates=candidates,
                tool_call=tool_call_info,
                polish_status="fallback",
                length_ratio=1.0,
                validation_warnings=[],
                case_analysis=case_analysis,
            )
