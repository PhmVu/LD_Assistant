from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from services.drawing_engine import build_lane_marking_instructions


router = APIRouter(prefix="/api/ld", tags=["ld-drawing"])


class DrawRequest(BaseModel):
    kind: str = Field(
        "dashed",
        description="dashed | solid | edge | arrow | crosswalk | stop_line | default",
    )
    color_hint: str = Field(
        "auto",
        description="auto | white | yellow | #hex",
    )


@router.post("/draw")
async def draw_lane(req: DrawRequest):
    payload = build_lane_marking_instructions(req.kind, req.color_hint)
    return {"success": True, "data": payload}
