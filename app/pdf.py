"""Minimal, dependency-free PDF writer.

Only what an investigation report needs: paginated, wrapped, monospaced-ish
text using the standard Helvetica font. No external packages, no rendering of
remote content, and deterministic byte output for the same input.
"""

from __future__ import annotations

from collections.abc import Iterable

PAGE_WIDTH = 595
PAGE_HEIGHT = 842
MARGIN_X = 45
MARGIN_TOP = 800
FONT_SIZE = 9
LEADING = 12
BOTTOM_LIMIT = 50
MAX_PAGES = 200


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _wrap(text: str, width: int = 110) -> list[str]:
    if not text:
        return [""]
    lines: list[str] = []
    for raw_line in text.split("\n"):
        if len(raw_line) <= width:
            lines.append(raw_line)
            continue
        current = ""
        for word in raw_line.split(" "):
            if len(current) + len(word) + 1 > width:
                lines.append(current)
                current = word
            else:
                current = f"{current} {word}".strip()
        if current:
            lines.append(current)
    return lines


def _paginate(lines: Iterable[str]) -> list[list[str]]:
    pages: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        current.append(line)
        if len(current) * LEADING > (MARGIN_TOP - BOTTOM_LIMIT):
            pages.append(current)
            current = []
        if len(pages) >= MAX_PAGES:
            break
    if current and len(pages) < MAX_PAGES:
        pages.append(current)
    return pages or [[""]]


def _content_stream(lines: list[str]) -> bytes:
    parts = ["BT", f"/F1 {FONT_SIZE} Tf", f"{MARGIN_X} {MARGIN_TOP} Td", f"{LEADING} TL"]
    for index, line in enumerate(lines):
        if index:
            parts.append("T*")
        parts.append(f"({_escape(line)}) Tj")
    parts.append("ET")
    return "\n".join(parts).encode("latin-1", errors="replace")


def build_pdf(lines: Iterable[str], title: str = "TraceLens investigation report") -> bytes:
    """Render a list of text lines into a simple, valid PDF document."""
    pages = _paginate(lines)
    objects: list[bytes] = []

    page_count = len(pages)
    # Object numbering: 1 catalog, 2 pages, 3 font, then page/content pairs.
    font_obj = 3
    page_objects: list[int] = []
    content_objects: list[int] = []
    next_id = 4
    for _ in pages:
        page_objects.append(next_id)
        content_objects.append(next_id + 1)
        next_id += 2
    total_objects = next_id - 1

    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{pid} 0 R" for pid in page_objects)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    for _page_id, content_id, page_lines in zip(page_objects, content_objects, pages, strict=True):
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
                f"/Resources << /Font << /F1 {font_obj} 0 R >> >> /Contents {content_id} 0 R >>"
            ).encode()
        )
        stream = _content_stream(page_lines)
        objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_offset = len(out)
    out += f"xref\n0 {total_objects + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    safe_title = title.replace("(", "").replace(")", "").replace("\\", "")[:120]
    out += f"trailer\n<< /Size {total_objects + 1} /Root 1 0 R /Info << /Title ({safe_title}) >> >>\n".encode()
    out += f"startxref\n{xref_offset}\n%%EOF\n".encode()
    return bytes(out)
