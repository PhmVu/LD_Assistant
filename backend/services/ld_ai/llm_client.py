from __future__ import annotations

import base64
from typing import Any, Dict, List, Optional

import httpx

from core.config import settings


class LLMClient:
    """Thin wrapper around SiliconFlow / Ollama chat APIs."""

    def __init__(self) -> None:
        self.provider = settings.LD_LLM_PROVIDER.strip().lower()
        self.base_url = settings.LD_LLM_BASE_URL.rstrip("/")
        self.api_key = settings.LD_LLM_API_KEY

    # ─── helpers ────────────────────────────────────────────────
    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def supports_vision(self, model: str | None = None) -> bool:
        if self.provider == "siliconflow":
            name = (model or settings.LD_VISION_MODEL).lower()
            return any(t in name for t in ("vl", "vision", "llava", "mm", "qwen2-vl", "qwen-vl", "qwen3-vl"))
        if self.provider == "ollama":
            name = (model or "").lower()
            return any(t in name for t in ("vl", "vision", "llava", "mm"))
        return False

    # ─── SiliconFlow (OpenAI-compatible) ────────────────────────
    def _siliconflow_chat(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        temperature: float,
        max_tokens: int,
        image_bytes: Optional[bytes] = None,
    ) -> str:
        # Grey-compatible: No ValueError if api_key is missing (may rely on transparent proxy)

        # If image provided and model supports vision, embed as base64
        if image_bytes and self.supports_vision(model):
            b64 = base64.b64encode(image_bytes).decode()
            # Append image to the last user message
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        msg["content"] = [
                            {"type": "text", "text": content},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                            },
                        ]
                    break

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        # Qwen3 supports enable_thinking for deeper reasoning
        if "qwen3" in model.lower() and not self.supports_vision(model):
            payload["enable_thinking"] = False  # False = faster, set True for deep reasoning
        # base_url already includes /v1 (e.g. https://api.siliconflow.cn/v1)
        if self.base_url.endswith("/v1"):
            url = f"{self.base_url}/chat/completions"
        else:
            url = f"{self.base_url}/v1/chat/completions"
        with httpx.Client(timeout=90.0) as client:
            resp = client.post(url, headers=self._headers(), json=payload)
            resp.raise_for_status()
        choices = resp.json().get("choices") or []
        return (choices[0].get("message") or {}).get("content") or "" if choices else ""

    # ─── Ollama ─────────────────────────────────────────────────
    def _ollama_chat(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        temperature: float,
        max_tokens: int,
        image_bytes: Optional[bytes] = None,
    ) -> str:
        # If image provided and model supports vision, attach as base64
        if image_bytes and self.supports_vision(model):
            b64 = base64.b64encode(image_bytes).decode()
            # Append image to the last user message
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    if "images" not in msg:
                        msg["images"] = []
                    msg["images"].append(b64)
                    break

        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        url = f"{self.base_url}/api/chat"
        with httpx.Client(timeout=90.0) as client:
            resp = client.post(url, headers={"Content-Type": "application/json"}, json=payload)
            resp.raise_for_status()
        return (resp.json().get("message") or {}).get("content") or ""

    # ─── Public ─────────────────────────────────────────────────
    def chat(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        temperature: float,
        max_tokens: int,
        image_bytes: Optional[bytes] = None,
    ) -> str:
        if self.provider == "siliconflow":
            return self._siliconflow_chat(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                image_bytes=image_bytes,
            )
        if self.provider == "ollama":
            return self._ollama_chat(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                image_bytes=image_bytes,
            )
        raise ValueError(f"Unsupported LD_LLM_PROVIDER: '{self.provider}'. Use 'siliconflow' or 'ollama'.")
