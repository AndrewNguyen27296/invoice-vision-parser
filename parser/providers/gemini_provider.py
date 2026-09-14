"""
Google Gemini vision. The free-tier option -- with one condition attached.

COST:    Gemini's free tier bills nothing for input or output on its Flash
         models, which is what makes a public demo free to run.

THE CATCH, AND IT MATTERS COMMERCIALLY:
         Google states that free-tier content IS used to improve their
         products, whereas paid-tier content is not. That is fine for the three
         fictional sample invoices in this repo. It is NOT fine the moment a
         prospect drops a real supplier invoice into the demo -- that is a GDPR
         conversation you do not want to be having live.

         So: free tier for fictional samples, and for anything belonging to a
         client use a paid tier or a provider whose terms you can show them.
         `PARSER_GEMINI_FREE_TIER=0` turns off the "free" badge and starts
         pricing calls, for when you have moved to a paid key.

MODEL ID:
         Google's model names rotate. Rather than hardcode one that may 404,
         this provider asks the API which models the key can actually use and
         puts that list in the error message.
"""

from __future__ import annotations

import json
import os
from typing import Any, List

from ..prompts import SYSTEM_PROMPT, USER_PROMPT
from .base import ProviderError, ProviderReply


class GeminiProvider:
    name = "gemini"

    def __init__(self) -> None:
        self.free = os.getenv("PARSER_GEMINI_FREE_TIER", "1") != "0"
        # On the free tier Google states submitted content is used to improve
        # their products. That is fine for the fictional bundled samples and
        # not fine for a prospect's real supplier invoice, so the UI gates
        # uploads on this flag rather than on price.
        self.trains_on_data = self.free
        self.model = os.getenv("PARSER_GEMINI_MODEL", "gemini-flash-latest")

    def describe(self) -> str:
        if self.free:
            return (f"Google Gemini free tier ({self.model}) - $0.00, but Google may use "
                    "this content to improve their products: fictional samples only")
        return f"Google Gemini paid tier ({self.model}) - content not used for training"

    def _client(self):
        key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not key:
            raise ProviderError(
                "No Gemini key found. Get one free at https://aistudio.google.com/apikey "
                "and set GEMINI_API_KEY in your .env."
            )
        try:
            from google import genai
        except ImportError as exc:
            raise ProviderError(
                "The 'google-genai' package is not installed. Run: pip install google-genai"
            ) from exc
        return genai.Client(api_key=key)

    def available_models(self) -> List[str]:
        """What this key can actually call. Used to make 404s self-explanatory."""
        try:
            client = self._client()
            return sorted(
                m.name.removeprefix("models/")
                for m in client.models.list()
                if "generateContent" in (getattr(m, "supported_actions", None) or
                                         getattr(m, "supported_generation_methods", None) or [])
            )
        except Exception:
            return []

    def call(self, pages, cfg) -> ProviderReply:
        client = self._client()
        from google.genai import types

        parts: List[Any] = []
        for page in pages:
            if len(pages) > 1:
                parts.append(types.Part.from_text(text=f"--- Page {page.page_number} ---"))
            parts.append(types.Part.from_bytes(
                data=__import__("base64").b64decode(page.b64),
                mime_type=page.media_type,
            ))
        parts.append(types.Part.from_text(text=USER_PROMPT))

        try:
            response = client.models.generate_content(
                model=self.model,
                contents=[types.Content(role="user", parts=parts)],
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    temperature=0,
                    max_output_tokens=cfg.max_output_tokens,
                    # Ask for JSON at the protocol level rather than trusting the
                    # prompt. Removes the markdown-fence failure mode entirely.
                    response_mime_type="application/json",
                ),
            )
        except Exception as exc:
            hint = ""
            if "not found" in str(exc).lower() or "404" in str(exc):
                models = self.available_models()
                hint = (f" Models available to this key: {', '.join(models[:12])}."
                        if models else "")
                hint += " Set PARSER_GEMINI_MODEL to one of them."
            raise ProviderError(f"Gemini call failed: {exc}.{hint}") from exc

        raw = response.text or ""
        usage = getattr(response, "usage_metadata", None)
        return ProviderReply(
            raw_text=raw,
            input_tokens=int(getattr(usage, "prompt_token_count", 0) or 0),
            output_tokens=int(getattr(usage, "candidates_token_count", 0) or 0),
            model=self.model,
        )
