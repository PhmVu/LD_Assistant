from __future__ import annotations

import re
from typing import Dict, Any, List, Optional
from services.ld_ai.reasoning import get_global_reasoning_memory


_NUM_RE = re.compile(r"(?P<val>\d+(?:\.\d+)?)\s*(m|mm|cm)?", flags=re.IGNORECASE)


def _parse_dash_pattern(text: str) -> Optional[Dict[str, float]]:
    # look for patterns like '3m on, 6m off' or '3m/6m'
    t = text.replace("—", ",").lower()
    m = re.search(r"(\d+(?:\.\d+)?)m\s*(on|bật)?[,/;]?\s*(\d+(?:\.\d+)?)m\s*(off|tắt)?", t)
    if m:
        try:
            on = float(m.group(1))
            off = float(m.group(3))
            return {"on_m": on, "off_m": off}
        except Exception:
            return None
    # fallback find two numbers
    nums = [float(g.group('val')) for g in _NUM_RE.finditer(t)][:2]
    if len(nums) == 2:
        return {"on_m": nums[0], "off_m": nums[1]}
    return None


def _extract_ops_from_text(text: str) -> Dict[str, Any]:
    t = (text or "").lower()
    ops: Dict[str, Any] = {}
    if any(k in t for k in ("dash", "dashed", "gap", "on", "off", "3m", "m")):
        dp = _parse_dash_pattern(t)
        ops["DASH_PATTERN"] = dp or True
    if any(k in t for k in ("thickness", "độ dày", "width", "mm")):
        m = re.search(r"(\d+(?:\.\d+)?)\s*(mm|cm)?", t)
        if m:
            ops["WIDTH"] = float(m.group(1))
        else:
            ops["WIDTH"] = True
    if any(k in t for k in ("color", "màu", "vàng", "trắng", "xanh")):
        # crude color hint
        if "vàng" in t:
            ops["COLOR"] = "yellow"
        elif "xanh" in t:
            ops["COLOR"] = "blue"
        else:
            ops["COLOR"] = "white"
    # detect window hints like 'ngắn', 'dài'
    if "ngắn" in t or "short" in t:
        ops["WINDOW_BIAS"] = "shorter"
    if "dài" in t or "long" in t:
        ops["WINDOW_BIAS"] = "longer"
    return ops


def apply_feedback(key: str, summary: Dict[str, Any], metrics: Dict[str, float]) -> Dict[str, Any]:
    """Apply feedback: extract operator hints, compute simple rewards and update reasoning memory.

    Returns council hints to be appended to prompts.
    """
    hints: Dict[str, Any] = {}
    text = ""
    if isinstance(summary, dict):
        # summary may have 'summary' or direct fields
        text = summary.get("summary") or " ".join(str(v) for v in summary.values())
    else:
        text = str(summary)

    ops = _extract_ops_from_text(text or "")
    if ops:
        hints["operator_nudges"] = ops

    ic = float((metrics or {}).get("ic", 0.0) or 0.0)
    precision = float((metrics or {}).get("precision", 0.0) or 0.0)
    mdd = float((metrics or {}).get("mdd", 0.0) or 0.0)

    # window bias heuristics
    if ic >= 0.05:
        hints["window_bias"] = "longer"
    elif ic > 0.02:
        hints["window_bias"] = "balanced"
    else:
        hints["window_bias"] = "shorter"

    # normalization override
    if mdd > 0.4 or precision < 0.2:
        hints["normalization_override"] = "RAW"

    # update reasoning memory: reward operators proportional to ic and precision
    reasoning = get_global_reasoning_memory()
    reward = min(max(ic * 0.8 + precision * 0.2, 0.0), 1.0)
    for opname, val in ops.items():
        # store both presence and any numeric param
        op_key = f"{key}::OP::{opname}"
        try:
            reasoning.update(op_key, opname, reward)
        except Exception:
            # best-effort
            pass

    return hints


def get_council_hints(marking_type: str) -> Dict[str, Any]:
    reasoning = get_global_reasoning_memory()
    key = f"MARKING::{marking_type or 'general'}"
    nudges: List[str] = []
    possible_ops = ["DASH_PATTERN", "WIDTH", "COLOR", "GAP", "EDGE"]
    for op in possible_ops:
        sampler_key = f"{key}::OP::{op}"
        try:
            sampler = reasoning.get_sampler(sampler_key, [op])
            probs = sampler.get_probabilities()
            if probs and probs.get(op, 0.0) > 0.45:
                nudges.append(op)
        except Exception:
            continue

    hints: Dict[str, Any] = {}
    if nudges:
        hints["operator_nudges"] = nudges
    return hints

