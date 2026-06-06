import tempfile
import unittest
from pathlib import Path

from services.ld_ai import reasoning, synaptic_vortex
from services.ld_ai.council_bridge import apply_feedback


class FeedbackLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        reasoning._global_reasoning_memory = reasoning.ReasoningMemory(Path(self.tmpdir.name))
        synaptic_vortex._GLOBAL_VORTEX = synaptic_vortex.SynapticVortex(Path(self.tmpdir.name))

    def tearDown(self) -> None:
        reasoning._global_reasoning_memory = None
        synaptic_vortex._GLOBAL_VORTEX = None
        self.tmpdir.cleanup()

    def test_feedback_stores_in_vortex(self) -> None:
        vortex = synaptic_vortex.get_global_vortex()
        rec = vortex.add(
            "QA_USER::u-01",
            {"type": "variant_selection", "variant": {"width": 12, "dash": True}},
            metrics={"ic": 0.04},
        )
        recalled = vortex.recall("QA_USER::u-01", top_n=1)
        self.assertEqual(len(recalled), 1)
        self.assertEqual(recalled[0].id, rec.id)
        self.assertEqual(recalled[0].content.get("type"), "variant_selection")

    def test_feedback_updates_reasoning_memory(self) -> None:
        hints = apply_feedback(
            "MARKING::edge",
            {"summary": "vạch liền, độ dày 10mm"},
            {"ic": 0.03, "precision": 0.2, "mdd": 0.15},
        )
        self.assertIn("operator_nudges", hints)
        nudges = hints["operator_nudges"]
        self.assertIn("WIDTH", nudges)


if __name__ == "__main__":
    unittest.main()
