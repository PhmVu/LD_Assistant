from __future__ import annotations

import math
import re
import unicodedata
from typing import Any


def _resolve_color(kind: str, color_hint: str | None) -> str:
    hint = (color_hint or "auto").strip().lower()
    if hint.startswith("#"):
        return hint
    if hint in {"yellow", "vàng", "vang"}:
        return "#facc15"
    if hint in {"white", "trắng", "trang"}:
        return "#f8fafc"
    if kind == "edge":
        return "#facc15"
    return "#f8fafc"


def _normalize_text(text: str) -> str:
    value = (text or "").lower().replace("đ", "d")
    value = unicodedata.normalize("NFD", value)
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", value).strip()


def _straight_lane(y: float = 60, x_start: float = 10, x_end: float = 150, n: int = 10) -> list[list[float]]:
    pts = []
    for i in range(n):
        t = i / (n - 1)
        x = x_start + t * (x_end - x_start)
        pts.append([round(x, 1), y, 0.0, float(i)])
    return pts


def _curved_lane(y_start=30, y_end=90, x_start=10, x_end=150, curve=20, n=14) -> list[list[float]]:
    pts = []
    for i in range(n):
        t = i / (n - 1)
        x = x_start + t * (x_end - x_start)
        y = y_start + t * (y_end - y_start) + curve * math.sin(math.pi * t)
        pts.append([round(x, 1), round(y, 1), 0.0, float(i)])
    return pts


def _fishbone_pts(cx: float = 80, top_y: float = 15, bot_y: float = 105, n_ribs: int = 5) -> list[list[list[float]]]:
    """Trả về list các rib (xương cá) — mỗi rib là 2 điểm."""
    ribs = []
    for i in range(n_ribs):
        t = i / (n_ribs - 1)
        y = top_y + t * (bot_y - top_y)
        # Rib trái và phải, góc 45°
        ribs.append([[cx, y, 0, float(i * 4)], [cx - 22, y + 18, 0, float(i * 4 + 1)]])
        ribs.append([[cx, y, 0, float(i * 4 + 2)], [cx + 22, y + 18, 0, float(i * 4 + 3)]])
    return ribs


# ── Road config helpers ───────────────────────────────────────────────────────
def _road_config(lanes: int, direction: str, marking_note: str) -> dict:
    return {"lanes": lanes, "direction": direction, "marking_note": marking_note}


def _cam_config(
    vp_x_ratio: float = 0.50,   # 0.0–1.0 of canvas W; 0.5 = centered
    vp_y_ratio: float = 0.07,   # 0.0–1.0 of canvas H; horizon level
    fov: float = 0.72,           # field-of-view depth factor (0.5–1.0)
    scale_top: float = 0.60,    # road width taper at top = W * scale_top
) -> dict:
    """Camera config shipped with each drawing so frontend uses correct vanishing point."""
    return {
        "vp_x_ratio": vp_x_ratio,
        "vp_y_ratio": vp_y_ratio,
        "fov": fov,
        "scale_top": scale_top,
    }


# ── Main builder ─────────────────────────────────────────────────────────────
def build_lane_marking_instructions(kind: str, color_hint: str | None = None) -> dict[str, Any]:
    """
    Trả về drawing instruction dict cho LaneCanvas frontend component.
    Hỗ trợ: dashed, solid, edge, arrow, crosswalk, stop_line,
             double_yellow, yellow_solid_dash, fishbone, stop_bar_double,
             missing_lane, wrong_color, wrong_type, wrong_arrow, offset
    """
    kind = (kind or "default").strip().lower().replace("-", "_")
    if kind == "yellow_solid":
        kind = "solid"
        color_hint = "yellow"
    elif kind == "yellow_dashed":
        kind = "dashed"
        color_hint = "yellow"
    elif kind == "white_solid":
        kind = "solid"
        color_hint = "white"
    elif kind == "white_dashed":
        kind = "dashed"
        color_hint = "white"

    color = _resolve_color(kind, color_hint)

    # ── Vạch trắng đứt (phân làn, cho phép vượt) ─────────────────────────────
    if kind == "dashed":
        return {
            "style": {"color": color, "width": 0.15, "dash": [3, 6], "opacity": 0.95},
            "polyline4d": _straight_lane(y=60),
            "layers": [
                {"style": {"color": "#facc15", "width": 0.28, "dash": [], "opacity": 0.65}, "polyline4d": _straight_lane(y=18)},
                {"style": {"color": color, "width": 0.16, "dash": [3, 6], "opacity": 0.95}, "polyline4d": _straight_lane(y=60)},
                {"style": {"color": "#facc15", "width": 0.28, "dash": [], "opacity": 0.65}, "polyline4d": _straight_lane(y=102)},
            ],
            "note": "Vạch nét đứt: segment 3m, gap 6m. Phân làn — cho phép vượt.",
            "scene": "dashed",
            "road_config": _road_config(3, "one-way", "white dashed center"),
            "camera_config": _cam_config(vp_x_ratio=0.50, vp_y_ratio=0.06, fov=0.70, scale_top=0.58),
        }

    # ── Vạch liền (mép đường / cấm lấn làn) ──────────────────────────────────
    if kind == "solid":
        return {
            "style": {"color": color, "width": 0.18, "dash": [], "opacity": 1.0},
            "polyline4d": _straight_lane(y=60),
            "layers": [
                {"style": {"color": "#facc15", "width": 0.30, "dash": [], "opacity": 0.70}, "polyline4d": _straight_lane(y=18)},
                {"style": {"color": color, "width": 0.20, "dash": [], "opacity": 1.0}, "polyline4d": _straight_lane(y=60)},
                {"style": {"color": "#facc15", "width": 0.30, "dash": [], "opacity": 0.70}, "polyline4d": _straight_lane(y=102)},
            ],
            "note": "Vạch liền: không đứt đoạn, bám sát tim làn, độ dày đồng đều.",
            "scene": "solid",
            "road_config": _road_config(2, "two-way", "white solid edge"),
            "camera_config": _cam_config(vp_x_ratio=0.50, vp_y_ratio=0.06, fov=0.72, scale_top=0.60),
        }

    # ── Lề đường / edge line (vàng) ───────────────────────────────────────────
    if kind == "edge":
        return {
            "style": {"color": color, "width": 0.22, "dash": [], "opacity": 0.95},
            "polyline4d": _curved_lane(y_start=100, y_end=115, x_start=5, x_end=155, curve=5),
            "layers": [
                {"style": {"color": "#f8fafc", "width": 0.15, "dash": [3, 6], "opacity": 0.7}, "polyline4d": _straight_lane(y=55)},
                {"style": {"color": color, "width": 0.26, "dash": [], "opacity": 1.0}, "polyline4d": _curved_lane(y_start=100, y_end=115, x_start=5, x_end=155, curve=5)},
            ],
            "note": "Lề đường: màu vàng, bám mép ngoài, không vào lòng đường.",
            "scene": "edge",
            "road_config": _road_config(2, "one-way", "yellow solid edge line"),
            "camera_config": _cam_config(vp_x_ratio=0.48, vp_y_ratio=0.08, fov=0.68, scale_top=0.62),
        }

    # ── Mũi tên chỉ hướng ────────────────────────────────────────────────────
    if kind == "arrow":
        return {
            "style": {"color": color, "width": 0.20, "dash": [], "opacity": 0.95},
            "polyline4d": [[80,110,0,0],[80,40,0,1],[80,40,0,2],[60,60,0,3],[80,40,0,4],[100,60,0,5]],
            "layers": [
                {"style": {"color": "#facc15", "width": 0.28, "dash": [], "opacity": 0.60}, "polyline4d": _straight_lane(y=15)},
                {"style": {"color": color, "width": 0.22, "dash": [], "opacity": 0.95},
                 "polyline4d": [[80,110,0,0],[80,40,0,1],[80,40,0,2],[60,60,0,3],[80,40,0,4],[100,60,0,5]]},
                {"style": {"color": "#facc15", "width": 0.28, "dash": [], "opacity": 0.60}, "polyline4d": _straight_lane(y=105)},
            ],
            "note": "Mũi tên: đặt theo tim làn, hướng đúng chiều di chuyển, màu trắng.",
            "scene": "arrow",
            "road_config": _road_config(2, "one-way", "white arrow direction marking"),
            "camera_config": _cam_config(vp_x_ratio=0.50, vp_y_ratio=0.05, fov=0.78, scale_top=0.55),
        }

    # ── Zebra / vạch qua đường ───────────────────────────────────────────────
    if kind == "crosswalk":
        stripes = []
        for i in range(6):
            y = 18 + i * 16
            stripes.extend([[18, y, 0, float(i*2)], [142, y, 0, float(i*2+1)]])
        return {
            "style": {"color": color, "width": 0.36, "dash": [], "opacity": 0.9},
            "polyline4d": stripes,
            "layers": [{"style": {"color": color, "width": 0.38, "dash": [], "opacity": 0.9}, "polyline4d": stripes}],
            "note": "Vạch zebra: 6 dải song song cách đều 16cm, màu trắng sáng.",
            "scene": "crosswalk",
            "road_config": _road_config(1, "two-way", "white zebra crosswalk"),
            "camera_config": _cam_config(vp_x_ratio=0.50, vp_y_ratio=0.10, fov=0.65, scale_top=0.70),
        }

    # ── Vạch dừng (stop line) ────────────────────────────────────────────────
    if kind == "stop_line":
        return {
            "style": {"color": color, "width": 0.26, "dash": [], "opacity": 0.95},
            "polyline4d": [[15,70,0,0],[145,70,0,1]],
            "layers": [
                {"style": {"color": "#f8fafc", "width": 0.15, "dash": [3,6], "opacity": 0.7}, "polyline4d": _straight_lane(y=38)},
                {"style": {"color": color, "width": 0.30, "dash": [], "opacity": 1.0}, "polyline4d": [[15,70,0,0],[145,70,0,1]]},
                {"style": {"color": "#f8fafc", "width": 0.15, "dash": [3,6], "opacity": 0.7}, "polyline4d": _straight_lane(y=95)},
            ],
            "note": "Vạch dừng: ngang liền, vuông góc chiều di chuyển, trước vạch đèn.",
            "scene": "stop_line",
            "road_config": _road_config(2, "one-way", "white solid stop line"),
            "camera_config": _cam_config(vp_x_ratio=0.50, vp_y_ratio=0.08, fov=0.68, scale_top=0.62),
        }

    # ── Vạch đôi vàng (double yellow — phân 2 chiều cấm vượt) ───────────────
    if kind == "double_yellow":
        return {
            "style": {"color": "#facc15", "width": 0.18, "dash": [], "opacity": 1.0},
            "polyline4d": _straight_lane(y=57),
            "layers": [
                {"style": {"color": "#f8fafc", "width": 0.28, "dash": [], "opacity": 0.65}, "polyline4d": _straight_lane(y=15)},
                # Vạch vàng 1 (trái)
                {"style": {"color": "#facc15", "width": 0.20, "dash": [], "opacity": 1.0}, "polyline4d": _straight_lane(y=54)},
                # Vạch vàng 2 (phải) — song song cách 6px
                {"style": {"color": "#facc15", "width": 0.20, "dash": [], "opacity": 1.0}, "polyline4d": _straight_lane(y=66)},
                {"style": {"color": "#f8fafc", "width": 0.28, "dash": [], "opacity": 0.65}, "polyline4d": _straight_lane(y=105)},
            ],
            "note": "Vạch đôi vàng liền: phân đường 2 chiều, cấm vượt hoàn toàn. QCVN 41:2019 điều 3.2.",
            "scene": "double_yellow",
            "road_config": _road_config(4, "two-way", "double yellow center line no-overtake"),
            "camera_config": _cam_config(vp_x_ratio=0.50, vp_y_ratio=0.06, fov=0.74, scale_top=0.56),
        }

    # ── Vàng liền + đứt song song (1 bên cấm, 1 bên được vượt) ─────────────
    if kind == "yellow_solid_dash":
        return {
            "style": {"color": "#facc15", "width": 0.18, "dash": [], "opacity": 1.0},
            "polyline4d": _straight_lane(y=54),
            "layers": [
                {"style": {"color": "#f8fafc", "width": 0.26, "dash": [], "opacity": 0.60}, "polyline4d": _straight_lane(y=15)},
                # Vàng liền (cấm vượt phía này)
                {"style": {"color": "#facc15", "width": 0.20, "dash": [], "opacity": 1.0}, "polyline4d": _straight_lane(y=54)},
                # Vàng đứt (được vượt phía kia)
                {"style": {"color": "#facc15", "width": 0.18, "dash": [4, 5], "opacity": 0.90}, "polyline4d": _straight_lane(y=66)},
                {"style": {"color": "#f8fafc", "width": 0.26, "dash": [], "opacity": 0.60}, "polyline4d": _straight_lane(y=105)},
            ],
            "note": "Vàng liền+đứt song song: 1 chiều cấm vượt (cạnh vàng liền), chiều kia được vượt (cạnh đứt).",
            "scene": "yellow_solid_dash",
            "road_config": _road_config(4, "two-way", "yellow solid+dash one-side overtake"),
            "camera_config": _cam_config(vp_x_ratio=0.50, vp_y_ratio=0.06, fov=0.74, scale_top=0.56),
        }

    # ── Xương cá / Fishbone (vùng nhập làn cao tốc) ──────────────────────────
    if kind == "fishbone":
        ribs = _fishbone_pts(cx=80, n_ribs=5)
        # Flatten: mỗi rib là 1 segment, dùng break points (index = sentinel)
        layers = [
            {"style": {"color": "#f8fafc", "width": 0.28, "dash": [], "opacity": 0.60}, "polyline4d": _straight_lane(y=12)},
            {"style": {"color": "#f8fafc", "width": 0.28, "dash": [], "opacity": 0.60}, "polyline4d": _straight_lane(y=108)},
        ]
        # Thêm từng rib như layer riêng
        for rib in ribs:
            layers.append({
                "style": {"color": "#facc15", "width": 0.18, "dash": [], "opacity": 0.90},
                "polyline4d": rib,
            })
        # Spine (xương sống giữa)
        layers.append({
            "style": {"color": "#facc15", "width": 0.22, "dash": [], "opacity": 0.95},
            "polyline4d": _straight_lane(y=60, x_start=80, x_end=80, n=2),
        })
        return {
            "style": {"color": "#facc15", "width": 0.18, "dash": [], "opacity": 0.90},
            "polyline4d": _straight_lane(y=60),
            "layers": layers,
            "note": "Xương cá (fishbone): mũi tên ngắn góc 45° chỉ vào làn, vùng nhập làn cao tốc.",
            "scene": "fishbone",
            "road_config": _road_config(3, "one-way", "fishbone merge zone marking"),
            "camera_config": _cam_config(vp_x_ratio=0.50, vp_y_ratio=0.05, fov=0.76, scale_top=0.54),
        }

    # ── Vạch dừng đôi (trước đèn tín hiệu quan trọng) ───────────────────────
    if kind == "stop_bar_double":
        return {
            "style": {"color": color, "width": 0.26, "dash": [], "opacity": 0.95},
            "polyline4d": [[15,65,0,0],[145,65,0,1]],
            "layers": [
                {"style": {"color": "#f8fafc", "width": 0.14, "dash": [3,6], "opacity": 0.65}, "polyline4d": _straight_lane(y=35)},
                # Vạch dừng 1
                {"style": {"color": color, "width": 0.28, "dash": [], "opacity": 1.0}, "polyline4d": [[15,60,0,0],[145,60,0,1]]},
                # Vạch dừng 2 (cách 8px)
                {"style": {"color": color, "width": 0.22, "dash": [], "opacity": 0.80}, "polyline4d": [[15,72,0,0],[145,72,0,1]]},
                {"style": {"color": "#f8fafc", "width": 0.14, "dash": [3,6], "opacity": 0.65}, "polyline4d": _straight_lane(y=95)},
            ],
            "note": "Vạch dừng đôi: 2 vạch ngang song song trước đèn tín hiệu quan trọng.",
            "scene": "stop_bar_double",
            "road_config": _road_config(2, "one-way", "double stop bar at traffic light"),
            "camera_config": _cam_config(vp_x_ratio=0.50, vp_y_ratio=0.08, fov=0.68, scale_top=0.62),
        }

    # ── QA ERRORS ────────────────────────────────────────────────────────────

    if kind == "missing_lane":
        return {
            "style": {"color": "#ef4444", "width": 0.08, "dash": [2, 4], "opacity": 0.4},
            "polyline4d": _straight_lane(y=60),
            "layers": [
                {"style": {"color": "#facc15", "width": 0.28, "dash": [], "opacity": 0.70}, "polyline4d": _straight_lane(y=18)},
                # Ghost đỏ — vị trí vạch bị thiếu
                {"style": {"color": "#ef4444", "width": 0.14, "dash": [2, 5], "opacity": 0.38}, "polyline4d": _straight_lane(y=60)},
                {"style": {"color": "#facc15", "width": 0.28, "dash": [], "opacity": 0.70}, "polyline4d": _straight_lane(y=102)},
            ],
            "note": "❌ LỖI: Thiếu vạch làn giữa — cần bổ sung vạch đứt trắng tại đây.",
            "scene": "error_missing_lane",
            "error": True,
            "road_config": _road_config(3, "one-way", "missing white dashed center"),
            "camera_config": _cam_config(vp_x_ratio=0.50, vp_y_ratio=0.06, fov=0.70, scale_top=0.58),
        }

    if kind == "wrong_color":
        return {
            "style": {"color": "#f8fafc", "width": 0.18, "dash": [], "opacity": 0.9},
            "polyline4d": _straight_lane(y=60),
            "layers": [
                {"style": {"color": "#facc15", "width": 0.28, "dash": [], "opacity": 0.70}, "polyline4d": _straight_lane(y=18)},
                # Sai: vạch trắng ở vị trí cần vàng
                {"style": {"color": "#f8fafc", "width": 0.24, "dash": [], "opacity": 0.95}, "polyline4d": _straight_lane(y=60)},
                {"style": {"color": "#facc15", "width": 0.28, "dash": [], "opacity": 0.70}, "polyline4d": _straight_lane(y=102)},
            ],
            "note": "❌ LỖI: Sai màu vạch — vạch trung tâm 2 chiều phải màu vàng (#facc15), không phải trắng.",
            "scene": "error_wrong_color",
            "error": True,
            "road_config": _road_config(2, "two-way", "wrong: white instead of yellow center"),
            "camera_config": _cam_config(vp_x_ratio=0.50, vp_y_ratio=0.06, fov=0.72, scale_top=0.60),
        }

    if kind == "wrong_type":
        return {
            "style": {"color": "#f8fafc", "width": 0.22, "dash": [], "opacity": 0.9},
            "polyline4d": _straight_lane(y=60),
            "layers": [
                {"style": {"color": "#facc15", "width": 0.28, "dash": [], "opacity": 0.70}, "polyline4d": _straight_lane(y=18)},
                # Sai: vạch liền thay vạch đứt
                {"style": {"color": "#f8fafc", "width": 0.24, "dash": [], "opacity": 0.95}, "polyline4d": _straight_lane(y=60)},
                {"style": {"color": "#facc15", "width": 0.28, "dash": [], "opacity": 0.70}, "polyline4d": _straight_lane(y=102)},
            ],
            "note": "❌ LỖI: Sai loại vạch — dùng vạch liền thay vạch đứt cho phân làn cho phép vượt.",
            "scene": "error_wrong_type",
            "error": True,
            "road_config": _road_config(2, "one-way", "wrong: solid instead of dashed"),
            "camera_config": _cam_config(vp_x_ratio=0.50, vp_y_ratio=0.06, fov=0.72, scale_top=0.60),
        }

    if kind == "wrong_arrow":
        # Mũi tên ngược chiều (hướng xuống thay vì lên)
        return {
            "style": {"color": "#f8fafc", "width": 0.20, "dash": [], "opacity": 0.9},
            "polyline4d": [[80,30,0,0],[80,100,0,1],[80,100,0,2],[60,80,0,3],[80,100,0,4],[100,80,0,5]],
            "layers": [
                {"style": {"color": "#facc15", "width": 0.28, "dash": [], "opacity": 0.60}, "polyline4d": _straight_lane(y=15)},
                # Mũi tên ngược
                {"style": {"color": "#ef4444", "width": 0.22, "dash": [], "opacity": 0.90},
                 "polyline4d": [[80,30,0,0],[80,100,0,1],[80,100,0,2],[60,80,0,3],[80,100,0,4],[100,80,0,5]]},
                {"style": {"color": "#facc15", "width": 0.28, "dash": [], "opacity": 0.60}, "polyline4d": _straight_lane(y=105)},
            ],
            "note": "❌ LỖI: Mũi tên sai hướng — hướng mũi tên phải trùng chiều di chuyển của làn xe.",
            "scene": "error_wrong_arrow",
            "error": True,
            "road_config": _road_config(2, "one-way", "wrong arrow direction"),
            "camera_config": _cam_config(vp_x_ratio=0.50, vp_y_ratio=0.05, fov=0.78, scale_top=0.55),
        }

    if kind == "offset":
        shifted = [[p[0], p[1] + 20, p[2], p[3]] for p in _straight_lane(y=60)]
        return {
            "style": {"color": "#f8fafc", "width": 0.18, "dash": [3, 6], "opacity": 0.8},
            "polyline4d": shifted,
            "layers": [
                {"style": {"color": "#facc15", "width": 0.28, "dash": [], "opacity": 0.70}, "polyline4d": _straight_lane(y=18)},
                # Vị trí đúng (ghost xanh mờ)
                {"style": {"color": "#22c55e", "width": 0.12, "dash": [2, 5], "opacity": 0.32}, "polyline4d": _straight_lane(y=60)},
                # Vị trí sai (lệch 20px)
                {"style": {"color": "#f8fafc", "width": 0.20, "dash": [3, 6], "opacity": 0.90}, "polyline4d": shifted},
                {"style": {"color": "#facc15", "width": 0.28, "dash": [], "opacity": 0.70}, "polyline4d": _straight_lane(y=102)},
            ],
            "note": "❌ LỖI: Vạch lệch vị trí — cần căn về đúng tim làn (xanh mờ = vị trí đúng).",
            "scene": "error_offset",
            "error": True,
            "road_config": _road_config(2, "one-way", "offset from correct position"),
            "camera_config": _cam_config(vp_x_ratio=0.52, vp_y_ratio=0.06, fov=0.70, scale_top=0.60),
        }

    # ── Default ───────────────────────────────────────────────────────────────
    return {
        "style": {"color": color, "width": 0.14, "dash": [], "opacity": 0.85},
        "polyline4d": _straight_lane(y=60),
        "layers": [
            {"style": {"color": "#facc15", "width": 0.26, "dash": [], "opacity": 0.65}, "polyline4d": _straight_lane(y=20)},
            {"style": {"color": color, "width": 0.16, "dash": [], "opacity": 0.85}, "polyline4d": _straight_lane(y=60)},
            {"style": {"color": "#facc15", "width": 0.26, "dash": [], "opacity": 0.65}, "polyline4d": _straight_lane(y=100)},
        ],
        "note": "Minh họa vạch kẻ đường — nêu rõ loại vạch để có hướng dẫn chi tiết.",
        "scene": "road_top_view",
        "road_config": _road_config(2, "one-way", "generic road marking"),
        "camera_config": _cam_config(vp_x_ratio=0.50, vp_y_ratio=0.07, fov=0.72, scale_top=0.60),
    }


# ── Issue name → drawing kind mapping ────────────────────────────────────────
ERROR_ISSUE_MAP: dict[str, str] = {
    "missing lane": "missing_lane",
    "missing lane line": "missing_lane",
    "lane line missing": "missing_lane",
    "missing turn arrow": "wrong_arrow",
    "missing arrow": "wrong_arrow",
    "wrong line color": "wrong_color",
    "wrong color": "wrong_color",
    "sai màu": "wrong_color",
    "wrong solid": "wrong_type",
    "wrong dashed": "wrong_type",
    "wrong solid/dashed": "wrong_type",
    "sai loại vạch": "wrong_type",
    "missing road edge": "edge",
    "road edge": "edge",
    "curve polyline offset": "offset",
    "polyline offset": "offset",
    "offset": "offset",
    "wrong direction": "wrong_arrow",
    "wrong direction arrow": "wrong_arrow",
    "minor offset": "offset",
    "double yellow": "double_yellow",
    "double centerline": "double_yellow",
    "yellow solid dash": "yellow_solid_dash",
    "fishbone": "fishbone",
    "xương cá": "fishbone",
    "stop bar": "stop_bar_double",
    "stop line double": "stop_bar_double",
}

NORMALIZED_ISSUE_MAP: dict[str, str] = {
    "missing lane": "missing_lane",
    "missing lane line": "missing_lane",
    "lane line missing": "missing_lane",
    "thieu vach": "missing_lane",
    "bo sot vach": "missing_lane",
    "wrong line color": "wrong_color",
    "wrong color": "wrong_color",
    "sai mau": "wrong_color",
    "nham mau": "wrong_color",
    "wrong solid": "wrong_type",
    "wrong dashed": "wrong_type",
    "wrong solid dashed": "wrong_type",
    "wrong type": "wrong_type",
    "sai loai": "wrong_type",
    "sai kieu": "wrong_type",
    "road edge": "edge",
    "missing road edge": "edge",
    "le duong": "edge",
    "mep duong": "edge",
    "offset": "offset",
    "curve polyline offset": "offset",
    "polyline offset": "offset",
    "minor offset": "offset",
    "lech": "offset",
    "sai vi tri": "offset",
    "wrong arrow": "wrong_arrow",
    "wrong direction": "wrong_arrow",
    "wrong direction arrow": "wrong_arrow",
    "missing turn arrow": "wrong_arrow",
    "missing arrow": "wrong_arrow",
    "mui ten sai": "wrong_arrow",
    "sai huong": "wrong_arrow",
    "double yellow": "double_yellow",
    "double centerline": "double_yellow",
    "doi vang": "double_yellow",
    "yellow solid dash": "yellow_solid_dash",
    "vang lien dut": "yellow_solid_dash",
    "fishbone": "fishbone",
    "xuong ca": "fishbone",
    "stop bar": "stop_bar_double",
    "stop line double": "stop_bar_double",
    "vach dung doi": "stop_bar_double",
    "crosswalk": "crosswalk",
    "zebra": "crosswalk",
    "vach qua duong": "crosswalk",
}


def issue_name_to_kind(issue_name: str) -> str:
    """Map a QA issue name string to a drawing kind."""
    lower = _normalize_text(issue_name)
    for key, kind in NORMALIZED_ISSUE_MAP.items():
        if key in lower:
            return kind
    for key, kind in ERROR_ISSUE_MAP.items():
        if _normalize_text(key) in lower:
            return kind
    return "solid"


# ── Refine drawing kind from full LLM answer text ────────────────────────────
# Priority ordered: most specific patterns first
_REFINE_PATTERNS: list[tuple[list[str], str]] = [
    # Errors — specific first
    (["thiếu vạch", "missing lane", "thiếu làn", "bỏ sót vạch", "không có vạch"], "missing_lane"),
    (["sai màu", "wrong color", "wrong colour", "nhầm màu", "màu sai", "white.*instead.*yellow", "trắng.*thay.*vàng"], "wrong_color"),
    (["sai loại", "wrong type", "wrong solid", "wrong dashed", "liền.*thay.*đứt", "đứt.*thay.*liền"], "wrong_type"),
    (["mũi tên sai", "sai hướng", "wrong arrow", "wrong direction", "ngược chiều", "hướng ngược"], "wrong_arrow"),
    (["lệch vị trí", "lệch tim", "offset", "vạch lệch", "lệch làn", "sai tâm"], "offset"),
    # Road types
    (["xương cá", "fishbone", "merge zone", "vùng nhập làn"], "fishbone"),
    (["đôi vàng", "double yellow", "vạch đôi vàng", "hai vạch vàng"], "double_yellow"),
    (["vàng liền.*đứt", "vàng đứt.*liền", "yellow solid.*dash", "solid.*dash.*vàng"], "yellow_solid_dash"),
    (["vạch dừng đôi", "double stop", "stop bar double"], "stop_bar_double"),
    (["zebra", "crosswalk", "vạch qua đường", "vạch người đi bộ"], "crosswalk"),
    (["vạch dừng", "stop line", "vạch trước đèn"], "stop_line"),
    (["mũi tên", "arrow", "chỉ hướng"], "arrow"),
    (["lề đường", "mép đường", "edge line", "biên đường"], "edge"),
    (["vàng đứt", "yellow dashed", "vạch vàng đứt"], "yellow_dashed"),
    (["vàng liền", "yellow solid", "vạch vàng liền"], "yellow_solid"),
    (["vạch đứt", "nét đứt", "dashed", "phân làn đứt"], "dashed"),
    (["vạch liền", "nét liền", "solid line"], "solid"),
]


_REFINE_NORMALIZED_PATTERNS: list[tuple[list[str], str]] = [
    (["thieu vach", "missing lane", "bo sot vach", "khong co vach"], "missing_lane"),
    (["sai mau", "wrong color", "nham mau", "mau sai"], "wrong_color"),
    (["sai loai", "wrong type", "wrong solid", "wrong dashed"], "wrong_type"),
    (["mui ten sai", "sai huong", "wrong arrow", "wrong direction", "nguoc chieu"], "wrong_arrow"),
    (["lech vi tri", "lech tim", "offset", "vach lech", "sai tam"], "offset"),
    (["xuong ca", "fishbone", "merge zone", "vung nhap lan"], "fishbone"),
    (["doi vang", "double yellow", "hai vach vang"], "double_yellow"),
    (["vang lien dut", "vang dut lien", "yellow solid dash"], "yellow_solid_dash"),
    (["vach dung doi", "double stop", "stop bar double"], "stop_bar_double"),
    (["zebra", "crosswalk", "vach qua duong", "nguoi di bo"], "crosswalk"),
    (["vach dung", "stop line", "vach truoc den"], "stop_line"),
    (["mui ten", "arrow", "chi huong"], "arrow"),
    (["le duong", "mep duong", "edge line", "bien duong"], "edge"),
    (["vang dut", "yellow dashed"], "yellow_dashed"),
    (["vang lien", "yellow solid"], "yellow_solid"),
    (["vach dut", "net dut", "dashed"], "dashed"),
    (["vach lien", "net lien", "solid line"], "solid"),
]


def refine_drawing_from_text(text: str, current_kind: str = "solid") -> str:
    """
    Scan the full LLM answer text (Vietnamese + English) to detect the most
    relevant drawing kind. Returns `current_kind` if no stronger signal found.

    Priority: errors > specific marking types > generic.
    """
    if not text:
        return current_kind
    normalized = _normalize_text(text)
    for patterns, kind in _REFINE_NORMALIZED_PATTERNS:
        for pat in patterns:
            if pat in normalized:
                return kind
    lower = text.lower()
    for patterns, kind in _REFINE_PATTERNS:
        for pat in patterns:
            if re.search(pat, lower):
                return kind
    return current_kind
