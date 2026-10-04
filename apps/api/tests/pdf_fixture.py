from __future__ import annotations


def _text_pdf(pages: list[str]) -> bytes:
    """Build a minimal multi-page text PDF (uncompressed content stream)."""
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    font = add(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    )
    page_ids: list[int] = []
    content_ids: list[int] = []
    for text in pages:
        escaped_lines = [
            line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
            for line in text.splitlines() or [""]
        ]
        stream = (
            f"BT /F1 12 Tf 72 720 Td 14 TL ({escaped_lines[0]}) Tj"
            + "".join(f" T* ({line}) Tj" for line in escaped_lines[1:])
            + " ET"
        ).encode("latin-1")
        content_ids.append(
            add(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
        )
    pages_id = len(objects) + len(pages) + 1
    for content_id in content_ids:
        page_ids.append(
            add(
                b"<< /Type /Page /Parent "
                + str(pages_id).encode()
                + b" 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 "
                + str(font).encode()
                + b" 0 R >> >> /Contents "
                + str(content_id).encode()
                + b" 0 R >>"
            )
        )
    kids = b" ".join(str(page_id).encode() + b" 0 R" for page_id in page_ids)
    pages_obj = add(b"<< /Type /Pages /Kids [" + kids + b"] /Count " + str(len(page_ids)).encode() + b" >>")
    catalog = add(b"<< /Type /Catalog /Pages " + str(pages_obj).encode() + b" 0 R >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(number).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref_offset = len(out)
    out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n"
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        b"trailer\n<< /Size "
        + str(len(objects) + 1).encode()
        + b" /Root "
        + str(catalog).encode()
        + b" 0 R >>\nstartxref\n"
        + str(xref_offset).encode()
        + b"\n%%EOF\n"
    )
    return bytes(out)