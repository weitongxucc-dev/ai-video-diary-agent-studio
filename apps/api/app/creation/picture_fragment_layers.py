"""Project approved burn-in layers onto a picture window's integer frame clock."""
from dataclasses import asdict, dataclass

import hashlib
import json

from app.montage import _subtitle_frame_boundary

PICTURE_LAYER_CONTRACT = 'picture-burnin-global-frames/v1'


@dataclass(frozen=True)
class PictureFrameLayer:
    kind: str
    value: object
    first_frame: int
    last_frame: int


def picture_window_layers(window, *, captions, graphic_overlays):
    window.validate()
    layers = []
    def include(kind, value, start, end):
        first = max(window.timeline_start_frame, _subtitle_frame_boundary(start))
        last = min(window.timeline_end_frame, _subtitle_frame_boundary(end))
        if first < last:
            layers.append(PictureFrameLayer(kind, value,
                first - window.timeline_start_frame, last - window.timeline_start_frame))
    for overlay in graphic_overlays:
        include('graphic', overlay, overlay['start_seconds'], overlay['end_seconds'])
    for caption in captions:
        include('subtitle', caption, caption.start_seconds, caption.end_seconds)
    return tuple(layers)


def picture_fragment_recipe(window, frame_layers):
    window.validate()
    payload = {'picture_layer_contract': PICTURE_LAYER_CONTRACT, 'window_id': window.id,
        'layers': [asdict(layer) for layer in frame_layers]}
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    return {**payload, 'digest': hashlib.sha256(encoded).hexdigest()}
