"""Tiny dependency-free PDF writer for generated loan documents.

Produces plain text pages in Helvetica (WinAnsi). Characters outside Latin-1
(e.g. Azerbaijani 'ə') are transliterated so the file always opens.
"""

_TRANSLIT = str.maketrans({
    "ə": "e", "Ə": "E", "ğ": "g", "Ğ": "G", "ı": "i", "İ": "I",
    "ş": "s", "Ş": "S", "ç": "c", "Ç": "C", "ö": "o", "Ö": "O",
    "ü": "u", "Ü": "U", "—": "-", "–": "-", "’": "'", "“": '"', "”": '"',
})

LINES_PER_PAGE = 50


def _escape(text: str) -> str:
    text = text.translate(_TRANSLIT).encode("latin-1", "replace").decode("latin-1")
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _page_stream(title: str, lines: list[str], page_no: int, pages: int) -> bytes:
    out = ["BT", "/F2 15 Tf", "50 800 Td", f"({_escape(title)}) Tj", "ET"]
    out += ["BT", "/F1 9 Tf", "12 TL", "50 770 Td"]
    for line in lines:
        out.append(f"({_escape(line)}) Tj T*")
    out.append("ET")
    out += ["BT", "/F1 8 Tf", "50 30 Td", f"(Page {page_no} of {pages}) Tj", "ET"]
    return "\n".join(out).encode("latin-1")


def build_pdf(title: str, lines: list[str]) -> bytes:
    chunks = [lines[i:i + LINES_PER_PAGE] for i in range(0, len(lines), LINES_PER_PAGE)] or [[]]
    n = len(chunks)

    # Object numbers: 1 catalog, 2 pages, 3 font regular, 4 font bold,
    # then for each page: page object, content stream.
    objects: dict[int, bytes] = {}
    kids = []
    for i, chunk in enumerate(chunks):
        page_obj = 5 + i * 2
        content_obj = page_obj + 1
        kids.append(f"{page_obj} 0 R")
        stream = _page_stream(title, chunk, i + 1, n)
        objects[content_obj] = (
            f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"
        )
        objects[page_obj] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> "
            f"/Contents {content_obj} 0 R >>"
        ).encode()

    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[2] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {n} >>".encode()
    objects[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    objects[4] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"

    buf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for num in sorted(objects):
        offsets[num] = len(buf)
        buf += f"{num} 0 obj\n".encode() + objects[num] + b"\nendobj\n"
    xref = len(buf)
    total = max(objects) + 1
    buf += f"xref\n0 {total}\n0000000000 65535 f \n".encode()
    for num in range(1, total):
        buf += f"{offsets[num]:010d} 00000 n \n".encode()
    buf += f"trailer\n<< /Size {total} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(buf)
