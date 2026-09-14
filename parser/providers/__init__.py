"""Provider registry. `PARSER_PROVIDER` picks the vision backend."""

from __future__ import annotations

import os
from typing import Callable, Dict

from .base import ProviderError, ProviderReply, VisionProvider

_BUILDERS: Dict[str, Callable[[], VisionProvider]] = {}


def _register(name: str, builder: Callable[[], VisionProvider]) -> None:
    _BUILDERS[name] = builder


def _anthropic() -> VisionProvider:
    from .anthropic_provider import AnthropicProvider
    return AnthropicProvider()


def _gemini() -> VisionProvider:
    from .gemini_provider import GeminiProvider
    return GeminiProvider()


def _mock() -> VisionProvider:
    from .mock_provider import MockProvider
    return MockProvider()


def _demo() -> VisionProvider:
    from .demo_provider import DemoProvider
    return DemoProvider()


# Imports stay lazy so a missing optional SDK never breaks an unrelated run.
_register("anthropic", _anthropic)
_register("gemini", _gemini)
_register("mock", _mock)
# The right default for a public demo: correct for the bundled samples, and it
# refuses rather than inventing an answer for anything else.
_register("demo", _demo)


def available_providers() -> list[str]:
    return sorted(_BUILDERS)


def get_provider(name: str | None = None) -> VisionProvider:
    """Build the named provider, or the one PARSER_PROVIDER selects."""
    name = (name or os.getenv("PARSER_PROVIDER", "demo")).strip().lower()
    if name not in _BUILDERS:
        raise ProviderError(
            f"Unknown provider '{name}'. Available: {', '.join(available_providers())}. "
            "Set PARSER_PROVIDER to one of these."
        )
    return _BUILDERS[name]()


__all__ = [
    "ProviderError", "ProviderReply", "VisionProvider",
    "get_provider", "available_providers",
]
