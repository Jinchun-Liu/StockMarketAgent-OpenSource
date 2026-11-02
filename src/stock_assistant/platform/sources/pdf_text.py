"""Bounded text-only PDF extraction in a disposable, credential-free process."""

from __future__ import annotations

import io
import resource
import sys

from pypdf import PdfReader
from pypdf.errors import PdfReadError

MAX_PDF_BYTES = 12 * 1024 * 1024
MAX_TEXT_CHARS = 100_000
LIMIT_MARKER = "\n[PDF extraction limit reached; remaining pages unavailable]\n"


def extract_pdf_text(content: bytes) -> str:
    reader = PdfReader(io.BytesIO(content), strict=True)
    if reader.is_encrypted or len(reader.pages) > 350:
        raise ValueError("WEB_PDF_UNSUPPORTED")
    parts: list[str] = []
    length = 0
    for index, page in enumerate(reader.pages):
        stream = page.get_contents()
        if stream is not None and len(stream.get_data()) > 2 * 1024 * 1024:
            raise ValueError("WEB_PDF_PAGE_TOO_LARGE")
        # A smaller estimated character width preserves gaps between narrow
        # numeric columns on pages containing wider text glyphs. Omit only
        # inferred blank lines to keep the existing text budget useful.
        text = page.extract_text(
            extraction_mode="layout",
            layout_mode_scale_weight=1.5,
            layout_mode_space_vertically=False,
        )
        if not text.strip():
            continue
        section = f"\n[PDF page {index + 1}]\n{text}\n"
        if length + len(section) > MAX_TEXT_CHARS - len(LIMIT_MARKER):
            parts.append(LIMIT_MARKER)
            break
        parts.append(section)
        length += len(section)
    if length < 100:
        raise ValueError("WEB_PDF_TEXT_UNAVAILABLE")
    return "".join(parts)


def main() -> int:
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    if sys.platform == "linux":
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    content = sys.stdin.buffer.read(MAX_PDF_BYTES + 1)
    if len(content) > MAX_PDF_BYTES:
        return 2
    try:
        result = extract_pdf_text(content)
    except (PdfReadError, ValueError, KeyError, TypeError, OSError, MemoryError, RecursionError):
        return 2
    sys.stdout.write(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
