from __future__ import annotations

import copy
import random
from typing import Any, Dict, List


def mutate_style(style: Dict[str, Any]) -> Dict[str, Any]:
    """Perturb numeric style parameters slightly to create a variant."""
    s = copy.deepcopy(style)
    # numeric fields we may perturb
    for k in list(s.keys()):
        v = s[k]
        if isinstance(v, (int, float)):
            # apply small relative noise
            noise = random.uniform(-0.2, 0.2) * max(1.0, abs(float(v)))
            s[k] = max(0.0, float(v) + noise)
        elif k == "dash" and isinstance(v, (list, tuple)) and len(v) >= 2:
            # preserve dash pattern but add tiny jitter
            d0 = max(0.1, float(v[0]) + random.uniform(-0.3, 0.3))
            d1 = max(0.1, float(v[1]) + random.uniform(-0.3, 0.3))
            s[k] = [d0, d1]
    # sometimes toggle dash/solid
    if random.random() < 0.2 and "dash" in s:
        if isinstance(s.get("dash"), (list, tuple)):
            # reduce to solid by empty dash
            s["dash"] = []
        else:
            s["dash"] = not bool(s.get("dash"))
    return s


def evolve_drawing_candidates(base: Dict[str, Any], n: int = 5) -> List[Dict[str, Any]]:
    """Given a base drawing instruction dict, produce n candidate variants.

    Assumes base has a top-level 'style' dict and 'polyline4d' etc. We only mutate style for now.
    """
    candidates: List[Dict[str, Any]] = []
    for i in range(n):
        c = copy.deepcopy(base)
        style = c.get("style", {})
        c["style"] = mutate_style(style)
        # small jitter on coordinates
        if "polyline4d" in c and isinstance(c["polyline4d"], list):
            jittered = []
            for pt in c["polyline4d"]:
                if isinstance(pt, (list, tuple)):
                    jitter = [float(x) + random.uniform(-0.05, 0.05) for x in pt]
                    jittered.append(jitter)
                else:
                    jittered.append(pt)
            c["polyline4d"] = jittered
        candidates.append(c)
    return candidates
