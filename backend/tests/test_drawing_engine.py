from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.drawing_engine import build_lane_marking_instructions, issue_name_to_kind


def test_issue_name_to_kind_wrong_line_color():
    assert issue_name_to_kind("Wrong line color") == "wrong_color"
    assert issue_name_to_kind("Sai màu vạch") == "wrong_color"


def test_main_drawing_kinds_have_required_schema():
    kinds = [
        "dashed",
        "solid",
        "edge",
        "arrow",
        "crosswalk",
        "stop_line",
        "double_yellow",
        "yellow_solid_dash",
        "fishbone",
        "stop_bar_double",
        "missing_lane",
        "wrong_color",
        "wrong_type",
        "wrong_arrow",
        "offset",
        "yellow_solid",
        "yellow_dashed",
    ]

    for kind in kinds:
        drawing = build_lane_marking_instructions(kind, "auto")
        assert drawing.get("style"), kind
        assert drawing.get("layers"), kind
        assert drawing.get("scene"), kind
        assert drawing.get("road_config"), kind
