"""Reuse visual design without copying a previous work's inputs or adoption."""
from copy import deepcopy
from math import ulp
from uuid import uuid4

from app.creation.narration_duration import DELIVERY_FRAME_RATE
from app.creation.visual_template_contracts import VisualTemplateOverlay


def reusable_components(montage):
    result = []
    for item in montage.graphic_overlays:
        if item.get('kind') != 'template':
            continue
        overlay = VisualTemplateOverlay.model_validate(item)
        definition = overlay.definition.model_dump()
        definition['values'] = {}  # New subject must supply its own text/data.
        duration = overlay.end_seconds-overlay.start_seconds
        result.append({'template_id': overlay.template_id, 'template_version': overlay.template_version,
            'name': overlay.name, 'definition': definition,
            'start_fraction': overlay.start_seconds/montage.duration_seconds,
            'duration_fraction': duration/montage.duration_seconds,
            'x': overlay.x, 'y': overlay.y, 'width': overlay.width, 'opacity': overlay.opacity,
            'z_index': overlay.z_index,
            'animation_offset_seconds': overlay.animation_offset_seconds,
            'keyframes': [{**frame.model_dump(), 'seconds': frame.seconds/duration} for frame in overlay.keyframes]})
    return result


def _delivery_boundary(seconds):
    # Fractional template placement may round across an exact frame after scaling.
    # Recover the frame time only within floating-point precision.
    frame_time = round(seconds * DELIVERY_FRAME_RATE) / DELIVERY_FRAME_RATE
    return frame_time if abs(seconds - frame_time) <= 2 * ulp(frame_time) else seconds


def instantiate_components(settings, duration):
    result = []
    for stored in settings.get('visual_components', []):
        item = deepcopy(stored)
        scaled_start = item.pop('start_fraction')*duration
        scaled_span = item.pop('duration_fraction')*duration
        start = _delivery_boundary(scaled_start)
        end = _delivery_boundary(scaled_start+scaled_span)
        span = end-start
        item['keyframes'] = [{**key, 'seconds': key['seconds']*span} for key in item['keyframes']]
        overlay = VisualTemplateOverlay(id=f'overlay_{uuid4().hex[:12]}', start_seconds=start,
            end_seconds=end, **item)
        result.append(overlay.model_dump())
    return result
