"""
Fast, cost-aware document -> image conversion.

Vision token cost is (width x height) / 750. Resolution is therefore the single
biggest lever on the bill, so every image is downscaled to a hard maximum edge
BEFORE it is ever encoded. A 300-DPI A4 render is ~8.7k tokens; the same page at
1120px is ~1.5k tokens and extracts just as accurately for printed invoices.
"""

from __future__ import annotations

import base64
import hashlib
import io
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from PIL import Image

# A 20 KB PNG can decode to a multi-gigabyte bitmap. Pillow warns at ~89 MP by
# default; make it a hard refusal instead, since no real invoice needs more.
Image.MAX_IMAGE_PIXELS = 80_000_000

SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}
SUPPORTED_SUFFIXES = SUPPORTED_IMAGE_SUFFIXES | {".pdf"}


@dataclass
class RenderedPage:
    """One page, ready to be sent to a vision model."""

    page_number: int
    image: Image.Image
    media_type: str
    b64: str
    width: int
    height: int
    #: The document this page came from. Carried so a fixture-backed provider
    #: can identify the file by content rather than guessing from the pixels.
    source_path: Optional[Path] = None

    @property
    def estimated_image_tokens(self) -> int:
        """Anthropic's documented approximation: (w x h) / 750."""
        return int((self.width * self.height) / 750)


def file_fingerprint(path: Path) -> str:
    """SHA-256 of the file bytes. The cache key -- identical file, zero API cost."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _downscale(img: Image.Image, max_edge: int) -> Image.Image:
    """Shrink so the longest edge is at most `max_edge`. Never upscales."""
    longest = max(img.width, img.height)
    if longest <= max_edge:
        return img
    scale = max_edge / longest
    return img.resize(
        (max(1, int(img.width * scale)), max(1, int(img.height * scale))),
        Image.LANCZOS,
    )


def _encode(img: Image.Image, quality: int = 88) -> tuple[str, str]:
    """JPEG-encode for the wire. Returns (media_type, base64)."""
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return "image/jpeg", base64.b64encode(buf.getvalue()).decode("ascii")


def render_document(
    path: Path | str,
    max_pages: int = 3,
    max_edge: int = 1120,
    render_scale: float = 2.4,
) -> List[RenderedPage]:
    """Turn a PDF or image file into a list of model-ready pages.

    Args:
        path:         PDF or image on disk.
        max_pages:    HARD CAP. A 400-page PDF will never bill you for 400 pages.
        max_edge:     Longest edge in pixels after downscaling (the cost lever).
        render_scale: pypdfium2 render scale before downscaling. 2.4 ~= 173 DPI,
                      which keeps small print legible prior to the resize.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"No such document: {path}")

    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(
            f"Unsupported file type '{suffix}'. Supported: {sorted(SUPPORTED_SUFFIXES)}"
        )

    images: List[Image.Image] = []

    if suffix == ".pdf":
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(path))
        try:
            page_count = len(pdf)
            for index in range(min(page_count, max_pages)):
                page = pdf[index]
                bitmap = page.render(scale=render_scale)
                images.append(bitmap.to_pil())
        finally:
            pdf.close()
    else:
        with Image.open(path) as opened:
            images.append(opened.convert("RGB"))

    pages: List[RenderedPage] = []
    for number, img in enumerate(images, start=1):
        small = _downscale(img, max_edge)
        media_type, b64 = _encode(small)
        pages.append(
            RenderedPage(
                page_number=number,
                image=small,
                media_type=media_type,
                b64=b64,
                width=small.width,
                height=small.height,
                source_path=path,
            )
        )
    return pages


def page_count(path: Path | str) -> int:
    """Count pages without rendering them -- used by the cost guard pre-flight."""
    path = Path(path)
    if path.suffix.lower() != ".pdf":
        return 1
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    try:
        return len(pdf)
    finally:
        pdf.close()
