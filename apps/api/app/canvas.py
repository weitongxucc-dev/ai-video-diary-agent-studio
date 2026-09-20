"""Supported output canvases shared by versioned rendering and previews."""
from typing import Literal

AspectRatio = Literal["9:16", "16:9", "1:1"]


def canvas_size(aspect_ratio: str = "9:16") -> tuple[int, int]:
    sizes = {"9:16":(1080,1920),"16:9":(1920,1080),"1:1":(1080,1080)}
    if aspect_ratio not in sizes:
        raise ValueError("画幅仅支持9:16、16:9和1:1")
    return sizes[aspect_ratio]


def preview_size(aspect_ratio: str = "9:16") -> tuple[int,int]:
    width,height=canvas_size(aspect_ratio)
    return round(width/max(width,height)*640/2)*2,round(height/max(width,height)*640/2)*2
