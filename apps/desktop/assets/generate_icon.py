#!/usr/bin/env python3
"""Build desktop icon assets from the reviewed brand master."""

from __future__ import annotations

import io
import struct
from pathlib import Path

from PIL import Image

OUT_DIR = Path(__file__).resolve().parent
SOURCE_PATH = OUT_DIR / "icon-source.png"
SIZES = (16, 32, 64, 128, 256, 512, 1024)
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
TYPE_MAP = {
    16: b"icp4",
    32: b"icp5",
    64: b"icp6",
    128: b"ic07",
    256: b"ic08",
    512: b"ic09",
    1024: b"ic10",
}


def render_png(source: Image.Image, size: int) -> bytes:
    image = source.resize((size, size), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def build_icns(pngs: dict[int, bytes]) -> bytes:
    entries = []
    total_size = 8
    for size in SIZES:
        png = pngs[size]
        entry_size = 8 + len(png)
        entries.append((TYPE_MAP[size], entry_size, png))
        total_size += entry_size

    output = bytearray(total_size)
    struct.pack_into(">4sI", output, 0, b"icns", total_size)
    offset = 8
    for type_code, entry_size, png in entries:
        struct.pack_into(">4sI", output, offset, type_code, entry_size)
        output[offset + 8 : offset + entry_size] = png
        offset += entry_size
    return bytes(output)


def main() -> None:
    if not SOURCE_PATH.is_file():
        raise SystemExit(f"Missing brand master: {SOURCE_PATH}")

    with Image.open(SOURCE_PATH) as opened:
        source = opened.convert("RGBA")
        if source.width != source.height:
            raise SystemExit("Brand master must be square")
        pngs = {size: render_png(source, size) for size in SIZES}
        source.save(OUT_DIR / "icon.ico", format="ICO", sizes=[(size, size) for size in ICO_SIZES])

    (OUT_DIR / "icon.png").write_bytes(pngs[512])
    (OUT_DIR / "icon.icns").write_bytes(build_icns(pngs))
    web_public = OUT_DIR.parents[1] / "web" / "public"
    (web_public / "yaohe-icon.png").write_bytes(pngs[128])
    (web_public / "favicon.png").write_bytes(pngs[32])
    (web_public / "favicon.svg").write_bytes((OUT_DIR / "icon-source.svg").read_bytes())
    print("Generated desktop PNG/ICNS/ICO and Web brand/favicon assets")


if __name__ == "__main__":
    main()
