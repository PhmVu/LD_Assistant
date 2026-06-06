from __future__ import annotations

from dataclasses import dataclass, field
import re


@dataclass
class IntentResult:
    marking_type: str
    request_type: str
    color_hint: str
    # resolved drawing kind (may differ from marking_type for error illustrations)
    drawing_kind: str = field(default="")
    # Có yêu cầu giải thích dài không (explain / lesson)
    wants_long_explanation: bool = field(default=False)
    # AI inferred drawing kind từ vision (nếu có ảnh)
    vision_drawing_kind: str = field(default="")

    def __post_init__(self) -> None:
        if not self.drawing_kind:
            self.drawing_kind = self.marking_type or "default"


# ── Từ khóa theo nhóm ────────────────────────────────────────────────────────
# Bao gồm cả từ có dấu và không dấu (user gõ tắt)
_DOUBLE_YELLOW = {
    "đôi vàng", "double yellow", "vạch đôi vàng", "đường đôi", "tim đường đôi",
    # không dấu
    "doi vang", "vach doi vang", "duong doi", "tim duong doi",
}
_YELLOW_SOLID_DASH = {
    "vàng liền đứt", "vàng đứt liền", "yellow solid dash", "solid dash", "liền và đứt",
    # không dấu
    "vang lien dut", "vang dut lien",
}
_FISHBONE = {
    "xương cá", "fishbone", "mũi tên ngắn", "vùng nhập làn", "merge zone", "merging",
    # không dấu
    "xuong ca", "mui ten ngan", "vung nhap lan",
}
_STOP_BAR_DOUBLE = {
    "vạch dừng đôi", "stop bar double", "double stop", "vạch dừng kép",
    # không dấu
    "vach dung doi", "vach dung kep",
}
_DASHED = {
    "nét đứt", "vạch đứt", "dashed", "đường đứt", "phân làn đứt",
    # không dấu
    "net dut", "vach dut", "duong dut", "phan lan dut",
}
_SOLID = {
    "vạch liền", "nét liền", "đường liền", "solid", "vạch không đứt",
    # không dấu
    "vach lien", "net lien", "duong lien",
}
_EDGE = {
    "lề đường", "lề", "edge", "curb", "mép đường", "biên đường",
    # không dấu
    "le duong", "mep duong", "bien duong",
}
_ARROW = {
    "mũi tên", "arrow", "chỉ hướng", "hướng đi",
    # không dấu
    "mui ten", "chi huong",
}
_CROSSWALK = {
    "zebra", "crosswalk", "vạch qua đường", "vạch người đi bộ", "pedestrian",
    # không dấu
    "vach qua duong", "nguoi di bo",
}
_STOP_LINE = {
    "stop line", "vạch dừng", "vạch đỏ", "vạch trước đèn", "stop bar",
    # không dấu
    "vach dung", "vach truoc den",
}
_MISSING_LANE = {
    "thiếu vạch", "missing lane", "missing line", "bỏ sót vạch", "không có vạch",
    # không dấu
    "thieu vach", "bo sot vach", "khong co vach",
}
_WRONG_COLOR = {
    "sai màu", "wrong color", "wrong colour", "nhầm màu", "màu sai",
    # không dấu
    "sai mau", "nham mau", "mau sai",
}
_WRONG_TYPE = {
    "sai loại", "wrong type", "nhầm vạch", "wrong solid", "wrong dashed", "sai kiểu",
    # không dấu
    "sai loai", "nham vach", "sai kieu",
}
_WRONG_ARROW = {
    "mũi tên sai", "sai hướng mũi tên", "wrong arrow", "arrow sai", "wrong direction arrow",
    # không dấu
    "mui ten sai", "sai huong mui ten",
}
_OFFSET = {
    "lệch", "offset", "sai vị trí", "lệch tim", "lệch làn", "vạch lệch", "sai tâm",
    # không dấu
    "lech", "sai vi tri", "lech tim", "lech lan", "vach lech", "sai tam",
}

_EXPLAIN_WORDS = {
    "giải thích", "explain", "hướng dẫn", "cách kẻ", "như thế nào",
    "tại sao", "why", "quy tắc", "tiêu chuẩn", "qcvn", "tcvn",
    "học", "hiểu", "nghĩa là gì", "là gì", "phân biệt", "khác nhau",
    # không dấu
    "giai thich", "huong dan", "cach ke", "nhu the nao",
    "tai sao", "quy tac", "tieu chuan", "hieu", "la gi", "phan biet", "khac nhau",
}
_DRAW_WORDS = {
    "vẽ", "draw", "minh hoạ", "minh họa", "illustration", "render", "show me",
    # không dấu
    "ve", "minh hoa",
}
_FIX_WORDS = {
    "sai", "lỗi", "fix", "sửa", "error", "wrong", "missing", "thiếu", "nhầm",
    # không dấu
    "loi", "sua", "thieu", "nham",
}


def _contains_any(text: str, keywords: set[str]) -> bool:
    return any(kw in text for kw in keywords)


def _extract_color(text: str) -> str:
    if _contains_any(text, {"vàng", "yellow"}):
        return "yellow"
    if _contains_any(text, {"trắng", "white"}):
        return "white"
    return "auto"


def parse_intent(message: str) -> IntentResult:
    text = message.lower()

    # ── Marking type — từ cụ thể → tổng quát ────────────────────────────────
    if _contains_any(text, _DOUBLE_YELLOW):
        marking_type = "double_yellow"
    elif _contains_any(text, _YELLOW_SOLID_DASH):
        marking_type = "yellow_solid_dash"
    elif _contains_any(text, _FISHBONE):
        marking_type = "fishbone"
    elif _contains_any(text, _STOP_BAR_DOUBLE):
        marking_type = "stop_bar_double"
    elif _contains_any(text, _MISSING_LANE):
        marking_type = "missing_lane"
    elif _contains_any(text, _WRONG_COLOR):
        marking_type = "wrong_color"
    elif _contains_any(text, _WRONG_TYPE):
        marking_type = "wrong_type"
    elif _contains_any(text, _WRONG_ARROW):
        marking_type = "wrong_arrow"
    elif _contains_any(text, _OFFSET):
        marking_type = "offset"
    elif _contains_any(text, _CROSSWALK):
        marking_type = "crosswalk"
    elif _contains_any(text, _STOP_LINE):
        marking_type = "stop_line"
    elif _contains_any(text, _EDGE):
        marking_type = "edge"
    elif _contains_any(text, _ARROW):
        # "hướng" + "làn" = arrow, tránh nhầm "hướng dẫn"
        if "mũi tên" in text or "arrow" in text or ("hướng" in text and "làn" in text):
            marking_type = "arrow"
        else:
            marking_type = "default"
    elif _contains_any(text, _DASHED):
        marking_type = "dashed"
    elif _contains_any(text, _SOLID):
        marking_type = "solid"
    else:
        marking_type = "default"

    # ── Request type ─────────────────────────────────────────────────────────
    if _contains_any(text, _FIX_WORDS):
        request_type = "fix"
    elif _contains_any(text, _DRAW_WORDS):
        request_type = "draw"
    elif _contains_any(text, _EXPLAIN_WORDS):
        request_type = "explain"
    else:
        request_type = "explain"

    # ── Wants long explanation ────────────────────────────────────────────────
    # Nếu user hỏi phân biệt, giải thích, quy tắc → yêu cầu trả lời dài hơn
    wants_long = _contains_any(text, {
        "giải thích", "explain", "phân biệt", "khác nhau", "quy tắc",
        "tiêu chuẩn", "qcvn", "tcvn", "tại sao", "why", "học", "hiểu",
    })

    # ── Color hint ───────────────────────────────────────────────────────────
    color_hint = _extract_color(text)

    # ── Resolve drawing kind ─────────────────────────────────────────────────
    drawing_kind = marking_type if marking_type not in {"default"} else "solid"

    return IntentResult(
        marking_type=marking_type,
        request_type=request_type,
        color_hint=color_hint,
        drawing_kind=drawing_kind,
        wants_long_explanation=wants_long,
    )
