"""
Lightweight learning utilities inspired by SOVA's ThompsonSampler and ReasoningMemory.

Provides:
- ThompsonSampler: bandit-style sampler to maintain weights for discrete "arms" (operators)
- ReasoningMemory: persistent store of samplers sharded by key (e.g., regime or market)

This module is intentionally small and dependency-free (uses json + pathlib).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Any
import random


class ThompsonSampler:
    """A simple Thompson Sampling implementation for discrete arms.

    Each arm keeps alpha/beta counts; sample() returns the arm with highest Beta draw.
    """

    def __init__(self, arms: List[str], prior_alpha: float = 1.0, prior_beta: float = 1.0):
        self.arms: List[str] = list(arms)
        self.alpha: Dict[str, float] = {}
        self.beta: Dict[str, float] = {}
        for a in self.arms:
            self.alpha[a] = float(prior_alpha)
            self.beta[a] = float(prior_beta)

    def sample(self) -> str:
        draws = {}
        for a in self.arms:
            # sample from Beta(alpha, beta)
            draws[a] = random.betavariate(self.alpha[a], self.beta[a])
        return max(draws, key=draws.get)

    def sample_top_k(self, k: int) -> List[str]:
        draws = {a: random.betavariate(self.alpha[a], self.beta[a]) for a in self.arms}
        return sorted(draws, key=draws.get, reverse=True)[:k]

    def update(self, arm: str, reward: float) -> None:
        """Update posterior for an arm.

        reward should be a non-negative scalar where larger indicates better outcome.
        We map reward to alpha increment; negative or zero reward increments beta.
        """
        if arm not in self.alpha:
            # new arm — initialize
            self.alpha[arm] = 1.0
            self.beta[arm] = 1.0
            if arm not in self.arms:
                self.arms.append(arm)

        try:
            r = float(reward)
        except Exception:
            r = 0.0

        if r > 0:
            # scale reward (small increments to keep stability)
            self.alpha[arm] += min(r, 1.0)
        else:
            self.beta[arm] += 0.5

    def add_arm(self, arm: str) -> None:
        if arm not in self.alpha:
            self.alpha[arm] = 1.0
            self.beta[arm] = 1.0
            self.arms.append(arm)

    def get_probabilities(self) -> Dict[str, float]:
        return {a: (self.alpha[a] / (self.alpha[a] + self.beta[a])) for a in self.arms}

    def to_dict(self) -> Dict[str, Any]:
        return {"arms": self.arms, "alpha": self.alpha, "beta": self.beta}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ThompsonSampler":
        arms = list(d.get("arms", []))
        obj = cls(arms)
        obj.alpha = {k: float(v) for k, v in d.get("alpha", {}).items()}
        obj.beta = {k: float(v) for k, v in d.get("beta", {}).items()}
        return obj


class ReasoningMemory:
    """Persistent store of ThompsonSamplers keyed by a string (e.g., 'REGIME::OP').

    Saves JSON to a memory file under `ld_memory/reasoning_weights.json` relative to the LD backend.
    """

    def __init__(self, root: Path | None = None):
        if root is None:
            # default memory location inside the LD backend folder
            root = Path(__file__).resolve().parents[3] / "data" / "ld_memory"
        self.root: Path = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "reasoning_weights.json"
        self._samplers: Dict[str, ThompsonSampler] = {}
        self._load()

    def get_sampler(self, key: str, arms: List[str]) -> ThompsonSampler:
        if key not in self._samplers:
            self._samplers[key] = ThompsonSampler(arms)
        else:
            # ensure arms are present
            for a in arms:
                if a not in self._samplers[key].arms:
                    self._samplers[key].add_arm(a)
        return self._samplers[key]

    def update(self, key: str, arm: str, reward: float) -> None:
        if key not in self._samplers:
            self._samplers[key] = ThompsonSampler([arm])
        else:
            self._samplers[key].add_arm(arm)
        self._samplers[key].update(arm, reward)
        self._save()

    def _load(self) -> None:
        try:
            if self.path.exists():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                for k, v in raw.items():
                    try:
                        self._samplers[k] = ThompsonSampler.from_dict(v)
                    except Exception:
                        # skip corrupted entries
                        continue
        except Exception:
            # best-effort load
            self._samplers = {}

    def _save(self) -> None:
        try:
            payload = {k: v.to_dict() for k, v in self._samplers.items()}
            self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass


# Singleton instance (convenience)
_global_reasoning_memory: ReasoningMemory | None = None

def get_global_reasoning_memory() -> ReasoningMemory:
    global _global_reasoning_memory
    if _global_reasoning_memory is None:
        _global_reasoning_memory = ReasoningMemory()
    return _global_reasoning_memory
