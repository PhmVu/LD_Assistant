from __future__ import annotations

from dataclasses import dataclass
from typing import List


KEYWORDS = [
    # Tiếng Việt có dấu
    "vạch", "vạch kẻ", "nét đứt", "vạch liền", "lề đường", "làn", "đường",
    "mũi tên", "chỉ hướng", "zebra", "crosswalk", "stop line",
    "thiếu vạch", "sai màu", "sai loại", "lệch", "annotation",
    "ảnh", "hình", "minh họa", "minh hoạ", "vẽ",
    # Tiếng Việt không dấu / Latinh
    "lane", "road", "marking", "dashed", "solid", "edge", "curb", "median",
    "divider", "image", "arrow", "turn", "offset", "missing", "wrong",
    "vach", "net dut", "le duong", "mui ten", "xương cá", "fishbone",
    "double yellow", "doi vang", "annotation", "qa", "labeling", "label",
    "polyline", "lidar", "qcvn", "tcvn", "loi", "sai", "thieu", "ve",
    "phan lan", "tim duong", "mep duong",
    # Project-specific LD wording
    "centerline", "linecenter", "line cu", "line moi", "line cũ", "line mới",
    "cao toc", "cao tốc", "sua chua", "sửa chữa", "thi cong", "thi công",
]


@dataclass
class GuardResult:
    allowed: bool
    reason: str
    matched: List[str]


def check_text_domain(text: str, has_image: bool = False) -> GuardResult:
    lowered = text.lower()
    matched = [kw for kw in KEYWORDS if kw in lowered]
    if matched:
        return GuardResult(True, "road-marking domain", matched)
    if has_image and (not lowered.strip() or any(k in lowered for k in ("ảnh", "image", "hình", "photo"))):
        return GuardResult(True, "image provided", matched)
    return GuardResult(False, "outside road-marking scope", matched)
