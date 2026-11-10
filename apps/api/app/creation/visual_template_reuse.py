"""Reuse visual design without copying a previous work's inputs or adoption."""
from copy import deepcopy
from uuid import uuid4

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


def instantiate_components(settings, duration):
    result = []
    for stored in settings.get('visual_components', []):
        item = deepcopy(stored)
        start = item.pop('start_fraction')*duration
        span = item.pop('duration_fraction')*duration
        item['keyframes'] = [{**key, 'seconds': key['seconds']*span} for key in item['keyframes']]
        overlay = VisualTemplateOverlay(id=f'overlay_{uuid4().hex[:12]}', start_seconds=start,
            end_seconds=start+span, **item)
        result.append(overlay.model_dump())
    return result
