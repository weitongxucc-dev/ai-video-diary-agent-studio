from __future__ import annotations

from collections.abc import Sequence
from math import ceil
from pathlib import Path

from PIL import Image


def build_reference_grid_image(
    frame_paths: Sequence[Path],
    output_path: Path,
    *,
    columns: int = 4,
    cell_size: int = 240,
    max_frames: int = 16,
) -> Path:
    selected_frames = list(frame_paths)[:max_frames]
    if not selected_frames:
        raise ValueError("reference grid requires at least one frame")

    rows = ceil(len(selected_frames) / columns)
    sheet = Image.new("RGB", (columns * cell_size, rows * cell_size), "#f4efe4")
    for index, frame_path in enumerate(selected_frames):
        with Image.open(frame_path) as frame:
            thumbnail = _cover_square(frame.convert("RGB"), cell_size)
        x = (index % columns) * cell_size
        y = (index // columns) * cell_size
        sheet.paste(thumbnail, (x, y))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path, quality=92)
    return output_path


def build_product_reference_grid_image(
    product_image_path: Path,
    frame_paths: Sequence[Path],
    output_path: Path,
    *,
    columns: int = 4,
    cell_size: int = 240,
    max_frames: int = 16,
) -> Path:
    product_slots = min(4, max_frames)
    selected_frames = [product_image_path] * product_slots
    selected_frames.extend(list(frame_paths)[: max_frames - product_slots])
    return build_reference_grid_image(
        selected_frames,
        output_path,
        columns=columns,
        cell_size=cell_size,
        max_frames=max_frames,
    )


def _cover_square(image: Image.Image, size: int) -> Image.Image:
    width, height = image.size
    crop_size = min(width, height)
    left = max(0, (width - crop_size) // 2)
    top = max(0, (height - crop_size) // 2)
    cropped = image.crop((left, top, left + crop_size, top + crop_size))
    return cropped.resize((size, size), Image.Resampling.LANCZOS)
