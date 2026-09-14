"""Anthropic vision. The paid default: best accuracy per cent for a real client."""

from __future__ import annotations

from typing import Any, Dict, List

from ..prompts import ASSISTANT_PREFILL, SYSTEM_PROMPT, USER_PROMPT
from .base import ProviderError, ProviderReply


class AnthropicProvider:
    name = "anthropic"
    free = False
    trains_on_data = False

    def describe(self) -> str:
        return "Anthropic (paid) - best accuracy, US-hosted, data not used for training"

    def call(self, pages, cfg) -> ProviderReply:
        try:
            import anthropic
        except ImportError as exc:
            raise ProviderError(
                "The 'anthropic' package is not installed. Run: pip install -r requirements.txt"
            ) from exc

        # Bound the wait and the retries explicitly. SDK defaults are a 600s
        # timeout and 2 retries -- a stalled call would park a live demo for ten
        # minutes, and one extraction could be billed three times.
        client = anthropic.Anthropic(
            timeout=cfg.api_timeout_s,
            max_retries=cfg.api_max_retries,
        )  # reads ANTHROPIC_API_KEY from the environment

        content: List[Dict[str, Any]] = []
        for page in pages:
            if len(pages) > 1:
                content.append({"type": "text", "text": f"--- Page {page.page_number} ---"})
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": page.media_type, "data": page.b64},
            })
        content.append({"type": "text", "text": USER_PROMPT})

        try:
            response = client.messages.create(
                model=cfg.model,
                max_tokens=cfg.max_output_tokens,
                temperature=0,
                system=SYSTEM_PROMPT,
                messages=[
                    {"role": "user", "content": content},
                    {"role": "assistant", "content": ASSISTANT_PREFILL},
                ],
            )
        except Exception as exc:
            raise ProviderError(f"Anthropic call failed: {exc}") from exc

        raw = ASSISTANT_PREFILL + "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        )
        usage = getattr(response, "usage", None)
        return ProviderReply(
            raw_text=raw,
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            model=cfg.model,
        )
