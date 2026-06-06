from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.ld_identity import normalize_labeler_username


def test_direct_username_normalization_smoke():
    full, display = normalize_labeler_username(" jr-nguyenthanhtuan-ty ")
    assert full == "jr-nguyenthanhtuan-ty"
    assert display == "nguyenthanhtuan"
