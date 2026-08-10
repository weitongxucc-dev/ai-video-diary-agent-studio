#!/usr/bin/env python3
"""Generate pixel-art icon set for AI视频日记 Agent Studio.
Produces icon.icns (macOS) and icon.png (Linux/Windows fallback).
"""

import struct
import sys
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent

# Pixel art: 16×16 "A" in a mini video frame style
# 1 = dark pixel (#1a1a2e), 2 = accent (#e94560), 0 = transparent/bg (#c0c0c0)
ICON_16 = [
    0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,
    0,0,0,0,0,1,1,1,1,1,1,0,0,0,0,0,
    0,0,0,0,1,2,2,2,2,2,2,1,0,0,0,0,
    0,0,0,1,2,2,2,2,2,2,2,2,1,0,0,0,
    0,0,0,1,2,1,1,2,1,1,2,2,1,0,0,0,
    0,0,0,1,2,2,2,2,2,2,2,2,1,0,0,0,
    0,0,0,1,2,2,1,1,1,1,1,2,1,0,0,0,
    0,0,0,1,2,2,1,2,2,2,1,2,1,0,0,0,
    0,0,0,1,2,2,2,2,2,2,2,2,1,0,0,0,
    0,0,0,1,2,1,1,2,2,1,1,2,1,0,0,0,
    0,0,0,0,1,2,2,2,2,2,2,1,0,0,0,0,
    0,0,0,0,0,1,1,1,1,1,1,0,0,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,
]

# Colors (RGBA)
BG = (0xc0, 0xc0, 0xc0, 0xff)       # classic window gray
DARK = (0x1a, 0x1a, 0x2e, 0xff)     # dark navy
ACCENT = (0xe9, 0x45, 0x60, 0xff)   # red accent
BORDER = (0x00, 0x00, 0x00, 0xff)   # black border
HIGHLIGHT = (0xff, 0xff, 0xff, 0xff)  # white
GRAY = (0x80, 0x80, 0x80, 0xff)     # mid gray


def generate_png(size: int) -> bytes:
    """Generate a PNG from the pixel art template, scaled to `size`."""
    from PIL import Image

    factor = size // 16
    img = Image.new('RGBA', (size, size), BG)

    # Draw 1px border for classic window look
    for x in range(size):
        img.putpixel((x, 0), BORDER)
        img.putpixel((x, size - 1), BORDER)
        img.putpixel((0, x), BORDER)
        img.putpixel((size - 1, x), BORDER)

    # Title bar (top ~15%)
    bar_h = max(2, size // 8)
    for y in range(1, bar_h):
        for x in range(1, size - 1):
            img.putpixel((x, y), DARK)

    # Title text line (white line in title bar)
    text_y = bar_h // 2
    if text_y > 1:
        for x in range(size // 5, size * 4 // 5):
            if x % 3 != 0:
                img.putpixel((x, text_y), HIGHLIGHT)

    # Close button (red square, top-left area)
    btn_size = max(3, size // 12)
    btn_x, btn_y = size - btn_size - 3, 2
    for y in range(btn_y, btn_y + btn_size):
        for x in range(btn_x, btn_x + btn_size):
            img.putpixel((x, y), ACCENT if (x + y) % 2 == 0 else DARK)

    # Content area: "V" shape representing video diary
    content_top = bar_h + 2
    content_bottom = size - 2
    content_left = size // 5
    content_right = size * 4 // 5
    mid_y = (content_top + content_bottom) // 2

    # Play triangle
    cx = size // 2
    cy = (content_top + content_bottom) // 2
    tri_h = size // 5
    for row in range(tri_h):
        rw = row * 2
        for col in range(-rw, rw + 1):
            px = cx + col
            py = cy - tri_h // 2 + row
            if content_left < px < content_right and content_top < py < content_bottom:
                if (px + py) % 3 == 0:
                    img.putpixel((px, py), ACCENT)
                else:
                    img.putpixel((px, py), DARK)

    # Bottom bar
    for y in range(size - bar_h - 1, size - 1):
        for x in range(1, size - 1):
            img.putpixel((x, y), GRAY if (x + y) % 2 == 0 else DARK)

    # Save to bytes
    import io
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


def png_to_icns(png_sizes: dict[int, bytes]) -> bytes:
    """Pack multiple PNG sizes into an .icns file."""
    ICNS_MAGIC = b'icns'
    # ICNS type codes for each size
    TYPE_MAP = {
        16: b'icp4',
        32: b'icp5',
        64: b'icp6',
        128: b'ic07',
        256: b'ic08',
        512: b'ic09',
        1024: b'ic10',
    }

    entries = []
    total_size = 8  # header

    for size, png_data in png_sizes.items():
        type_code = TYPE_MAP.get(size)
        if not type_code:
            continue
        entry_size = 8 + len(png_data)
        entries.append((type_code, entry_size, png_data))
        total_size += entry_size

    buf = bytearray(total_size)
    struct.pack_into('>4sI', buf, 0, ICNS_MAGIC, total_size)
    offset = 8
    for type_code, entry_size, data in entries:
        struct.pack_into('>4sI', buf, offset, type_code, entry_size)
        buf[offset + 8:offset + entry_size] = data
        offset += entry_size

    return bytes(buf)


def main():
    try:
        from PIL import Image
    except ImportError:
        print("Pillow not installed. Run: pip install Pillow")
        print("Generating minimal placeholder icon.icns instead...")
        # Create minimal valid icns with a 16x16 solid PNG
        import io
        from PIL import Image as PILImage
        # Actually we can't create PNG without PIL. Just create a placeholder.
        OUT_DIR.joinpath('icon.icns').write_bytes(_minimal_icns())
        OUT_DIR.joinpath('icon.png').write_bytes(_minimal_png())
        print("Placeholder icons created.")
        return

    sizes = [16, 32, 64, 128, 256, 512]
    pngs = {}
    for s in sizes:
        pngs[s] = generate_png(s)
        print(f"  Generated {s}×{s} PNG")

    # Write individual PNGs
    for s, data in pngs.items():
        OUT_DIR.joinpath(f'icon_{s}x{s}.png').write_bytes(data)

    # Write main icon.png (512px)
    OUT_DIR.joinpath('icon.png').write_bytes(pngs[512])

    # Write icon.icns
    icns_data = png_to_icns(pngs)
    OUT_DIR.joinpath('icon.icns').write_bytes(icns_data)
    print(f"  icon.icns created ({len(icns_data)} bytes)")

    print("Icon generation complete!")


def _minimal_png() -> bytes:
    """Absolute minimal valid 16x16 PNG (solid gray)."""
    import zlib
    def chunk(ctype, data):
        c = ctype + data
        return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)
    ihdr = struct.pack('>IIBBBBB', 16, 16, 8, 2, 0, 0, 0)  # 16x16, 8-bit RGB
    raw = b''
    for y in range(16):
        raw += b'\x00'  # filter none
        raw += b'\xc0\xc0\xc0' * 16  # gray pixels
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')


def _minimal_icns() -> bytes:
    """Minimal valid .icns with 16x16 icon."""
    png = _minimal_png()
    entry_size = 8 + len(png)
    total = 8 + entry_size
    buf = bytearray(total)
    struct.pack_into('>4sI', buf, 0, b'icns', total)
    struct.pack_into('>4sI', buf, 8, b'icp4', entry_size)
    buf[16:] = png
    return bytes(buf)


if __name__ == '__main__':
    main()
