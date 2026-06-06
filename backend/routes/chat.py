from __future__ import annotations

import asyncio
import json as json_module
from typing import Any, Dict, List, Optional
from dataclasses import asdict

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from services.ld_orchestrator import orchestrator
from services.ld_ai.council_bridge import apply_feedback as council_apply_feedback
from services.drawing_engine import build_lane_marking_instructions, issue_name_to_kind, refine_drawing_from_text
from services.ld_ai.intent_parser import parse_intent
from services.ld_ai.knowledge_retriever import retrieve_references


router = APIRouter(prefix="/api/ld", tags=["ld-ai"])


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    history: Optional[List[Dict[str, Any]]] = None


@router.post("/chat")
async def ld_chat(
    message: str = Form(...),
    history: Optional[str] = Form(None),
    image: Optional[UploadFile] = File(None),
):
    if not message.strip():
        raise HTTPException(status_code=400, detail="message is required")

    history_payload: Optional[List[Dict[str, Any]]] = None
    if history:
        try:
            history_payload = json_module.loads(history)
        except Exception:
            raise HTTPException(status_code=400, detail="history must be valid JSON")

    image_bytes = await image.read() if image else None

    response = orchestrator.chat(
        message=message,
        history=history_payload,
        image_bytes=image_bytes,
    )

    return {
        "success": True,
        "data": {
            "answer": response.answer,
            "references": response.references,
            "drawing": response.drawing,
            "moderation": response.moderation,
        },
    }


@router.get("/drawing")
async def ld_drawing(
    issue: str = Query("", description="Issue name or marking kind"),
    kind: str = Query("", description="Explicit drawing kind override"),
    color: str = Query("auto", description="Color hint: auto | white | yellow | #hex"),
):
    """Return a drawing instruction for a given issue or marking kind.
    Used by the user-detail panel to render per-error illustrations."""
    resolved_kind = kind.strip() or (issue_name_to_kind(issue) if issue.strip() else "solid")
    drawing = build_lane_marking_instructions(resolved_kind, color or "auto")
    return {"success": True, "drawing": drawing, "resolved_kind": resolved_kind}


@router.post("/chat/stream")
async def ld_chat_stream(
    message: str = Form(...),
    history: Optional[str] = Form(None),
    image: Optional[UploadFile] = File(None),
):
    """
    Streaming chat SSE. Text is buffered through LD AI validation before chunks
    are emitted, so SiliconFlow cannot leak an unvalidated contradiction.
    """
    if not message.strip():
        raise HTTPException(status_code=400, detail="message is required")

    history_payload: Optional[List[Dict[str, Any]]] = None
    if history:
        try:
            history_payload = json_module.loads(history)
        except Exception:
            pass

    image_bytes = await image.read() if image else None

    # Parse intent từ message
    intent = parse_intent(message)

    # Vision: xử lý ảnh nếu có — extract drawing_kind
    vision_drawing_kind: str | None = None
    vision_note: str | None = None
    if image_bytes:
        try:
            from services.ld_ai.vision_stub import summarize_image as _vision
            vr = _vision(image_bytes, chat_mode=True)
            if vr:
                vision_note = vr.summary
                vision_drawing_kind = vr.drawing_kind
        except Exception:
            pass

    # Resolve drawing kind: ưu tiên vision > intent
    effective_kind = (
        vision_drawing_kind
        if vision_drawing_kind and vision_drawing_kind not in {"solid", "default", ""}
        else (intent.drawing_kind or intent.marking_type)
    )
    drawing = build_lane_marking_instructions(effective_kind, intent.color_hint)

    def _text_chunks(text: str, size: int = 180):
        text = text or ""
        for start in range(0, len(text), size):
            yield text[start : start + size]

    async def generate():
        brain_instance = _get_brain()
        references = retrieve_references(message)
        
        # Start the blocking brain answer calculation in a separate thread
        # to keep the async event loop responsive and allow periodic heartbeats.
        task = asyncio.create_task(
            asyncio.to_thread(
                brain_instance.answer,
                message=message,
                intent=intent,
                references=references,
                vision_note=vision_note,
                history=history_payload,
                base_drawing=drawing,
                image_bytes=image_bytes,
                vision_drawing_kind=vision_drawing_kind,
            )
        )

        # Periodically yield heartbeats to prevent Vite dev server / HMR proxy timeouts
        while not task.done():
            yield ": heartbeat\n\n"
            await asyncio.sleep(2.0)

        brain_result = await task

        acc_text = brain_result.answer
        for chunk in _text_chunks(acc_text):
            yield f"data: {json_module.dumps({'type': 'token', 'text': chunk}, ensure_ascii=False)}\n\n"

        yield f"data: {json_module.dumps({'type': 'meta', 'used_llm': brain_result.used_llm, 'error': brain_result.error, 'polish_status': brain_result.polish_status, 'length_ratio': brain_result.length_ratio, 'validation_warnings': brain_result.validation_warnings or [], 'case_analysis': brain_result.case_analysis}, ensure_ascii=False)}\n\n"

        # ── Step 2: Refine drawing from validated answer text ───────────────
        refined_kind = refine_drawing_from_text(acc_text, effective_kind or "solid")
        if refined_kind != (effective_kind or "solid"):
            refined_drawing = build_lane_marking_instructions(refined_kind, intent.color_hint)
            yield f"data: {json_module.dumps({'type': 'drawing_refined', 'drawing': refined_drawing}, ensure_ascii=False)}\n\n"
        else:
            yield f"data: {json_module.dumps({'type': 'drawing', 'drawing': drawing}, ensure_ascii=False)}\n\n"
        yield 'data: {"type":"done"}\n\n'

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Lazy singleton brain for stream endpoint ─────────────────────────────────
_brain_singleton = None

def _get_brain():
    global _brain_singleton
    if _brain_singleton is None:
        from services.ld_ai.ld_brain import LDBrain
        _brain_singleton = LDBrain()
    return _brain_singleton


class FeedbackPayload(BaseModel):
    key: str
    summary: dict
    metrics: Optional[Dict[str, float]] = None
    user_id: Optional[str] = None


class VariantSelectionPayload(BaseModel):
    user_id: str
    key: str
    variant: dict
    metrics: Optional[Dict[str, float]] = None
    note: Optional[str] = None


def _store_user_history(vortex, user_id: Optional[str], content: Dict[str, Any], metrics: Optional[Dict[str, float]] = None):
    if not user_id:
        return None
    history_key = f"QA_USER::{user_id}"
    return vortex.add(history_key, content, metrics=metrics or {})


@router.post("/feedback")
async def ld_feedback(payload: FeedbackPayload):
    """Accept feedback / QA results and store into LD memory for future RAG."""
    try:
        from services.ld_ai.synaptic_vortex import get_global_vortex

        vortex = get_global_vortex()
        rec = vortex.add(payload.key, payload.summary, metrics=payload.metrics or {})
        history_rec = _store_user_history(
            vortex,
            payload.user_id,
            {"type": "feedback", "key": payload.key, "summary": payload.summary},
            metrics=payload.metrics or {},
        )
        # apply council bridge rules (update reasoning memory, produce hints)
        hints = council_apply_feedback(payload.key, payload.summary or {}, payload.metrics or {})
        return {
            "success": True,
            "record_id": rec.id,
            "hints": hints,
            "user_record_id": history_rec.id if history_rec else None,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/variant-selection")
async def ld_variant_selection(payload: VariantSelectionPayload):
    """Persist chosen variant for a user so the QA dashboard can show history."""
    try:
        from services.ld_ai.synaptic_vortex import get_global_vortex

        vortex = get_global_vortex()
        history_rec = _store_user_history(
            vortex,
            payload.user_id,
            {
                "type": "variant_selection",
                "key": payload.key,
                "variant": payload.variant,
                "note": payload.note,
            },
            metrics=payload.metrics or {},
        )
        return {"success": True, "record_id": history_rec.id if history_rec else None}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/qa-history/{user_id}")
async def ld_qa_history(user_id: str, limit: int = Query(50, ge=1, le=200)):
    """Return QA history for a given user."""
    try:
        from services.ld_ai.synaptic_vortex import get_global_vortex

        vortex = get_global_vortex()
        key = f"QA_USER::{user_id}"
        records = vortex.recall(key, top_n=limit)
        return {"success": True, "data": [asdict(r) for r in records]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
