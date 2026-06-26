from __future__ import annotations

from pathlib import Path

from PIL import Image

from app.same_style_reference_images import build_reference_grid_image


def test_build_reference_grid_image_creates_four_column_contact_sheet(tmp_path):
    frame_paths: list[Path] = []
    for index in range(5):
        path = tmp_path / f"frame_{index + 1:04d}.jpg"
        Image.new("RGB", (80, 120), (index * 30, 40, 80)).save(path)
        frame_paths.append(path)

    output_path = tmp_path / "reference_grid.jpg"

    result = build_reference_grid_image(frame_paths, output_path, columns=4, cell_size=180)

    assert result == output_path
    with Image.open(output_path) as image:
        assert image.size == (720, 360)
