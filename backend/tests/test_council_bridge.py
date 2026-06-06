import tempfile
import unittest
from pathlib import Path

from services.ld_ai import reasoning
from services.ld_ai.council_bridge import apply_feedback, get_council_hints


class CouncilBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        reasoning._global_reasoning_memory = reasoning.ReasoningMemory(Path(self.tmpdir.name))

    def tearDown(self) -> None:
        reasoning._global_reasoning_memory = None
        self.tmpdir.cleanup()

    def test_apply_feedback_extracts_ops_and_bias(self) -> None:
        hints = apply_feedback(
            "MARKING::dashed",
            {"summary": "vạch nét đứt 3m on, 6m off, độ dày 12mm, màu vàng"},
            {"ic": 0.06, "precision": 0.4, "mdd": 0.1},
        )
        self.assertIn("operator_nudges", hints)
        nudges = hints["operator_nudges"]
        self.assertIsInstance(nudges, dict)
        self.assertIn("DASH_PATTERN", nudges)
        self.assertIn("WIDTH", nudges)
        self.assertIn("COLOR", nudges)
        self.assertEqual(hints.get("window_bias"), "longer")

        council = get_council_hints("dashed")
        self.assertIn("operator_nudges", council)
        self.assertIn("DASH_PATTERN", council["operator_nudges"])

    def test_normalization_override_on_low_precision(self) -> None:
        hints = apply_feedback(
            "MARKING::edge",
            {"summary": "độ dày 10mm"},
            {"ic": 0.01, "precision": 0.1, "mdd": 0.2},
        )
        self.assertEqual(hints.get("normalization_override"), "RAW")


if __name__ == "__main__":
    unittest.main()
