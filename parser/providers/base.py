"""
The vision-provider contract.

V0 hardcoded a call to Anthropic. That was fine for one evening and wrong for a
business: the model vendor is the single most negotiable part of this product.
A prospect will ask "does our data go to the US?", "can this run on our own
hardware?", "we already pay Google, can you use that?" -- and the answer needs
to be a config change, not a rewrite.

A provider does exactly one thing: turn rendered page images into the model's
raw reply text, plus token counts. It does NOT parse, validate, audit, cache or
price -- all of that is shared pipeline, identical whichever vendor answered, so
the math audit means the same thing no matter who extracted the numbers.

Returning RAW TEXT rather than parsed JSON is deliberate. Tokens are billed the
moment the call returns, so the pipeline must be able to write the spend to the
ledger before it risks raising on a malformed reply.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover
    from ..cost_guard import GuardConfig
    from ..pdf_utils import RenderedPage


class ProviderError(RuntimeError):
    """The provider could not be reached, configured or authorised."""


@dataclass
class ProviderReply:
    """What a vision model gave back, before anyone has trusted it."""

    raw_text: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""


@runtime_checkable
class VisionProvider(Protocol):
    """Any backend that can read an invoice image."""

    name: str
    #: True when calls do not cost money (a free tier, or a local model). Shown
    #: in the UI and used to keep free traffic out of the spend ledger.
    free: bool
    #: True when the vendor's terms allow them to use submitted content to
    #: improve their products. Distinct from `free` on purpose: this is a data
    #: -protection property, not a pricing one, and the UI blocks uploads of a
    #: prospect's own documents to any provider where it is True.
    trains_on_data: bool

    def describe(self) -> str:
        """One line for the sidebar and the CLI, e.g. the model and cost posture."""
        ...

    def call(self, pages: "List[RenderedPage]", cfg: "GuardConfig") -> ProviderReply:
        """Send the pages, return the raw reply. Raise ProviderError on failure."""
        ...
