"""Pinned OpenFilm CSS in the existing deterministic, offline HTML host."""
import hashlib
from pathlib import Path

from app.creation.visual_template_contracts import VisualTemplate

ASSETS = Path(__file__).parents[1] / 'resource_assets/openfilm/transition/glitch'
IDENTITY = 'openfilm-transition-glitch'
TEMPLATE_ID = f'builtin_visual_{IDENTITY}'
CSS_SHA256 = '6063423ad31af44cca1fc88ed0b1bde72beb863125ebbd314265e7cb71335449'


def glitch_template():
    css = (ASSETS / 'overlay.css').read_bytes()
    if hashlib.sha256(css).hexdigest() != CSS_SHA256:
        raise ValueError('OpenFilm Glitch CSS 与固定来源摘要不符')
    # No reimplementation of the effect: the host freezes the original CSS
    # animation at t. The empty frame hook is the existing host's interface.
    source = ('<!doctype html><html><head><meta charset="utf-8"><style>'
              + css.decode('utf-8')
              + '</style></head><body><div class="l"></div>'
                '<script>window.film={frame(t){}};</script></body></html>')
    return VisualTemplate(
        category='reaction', width=384, height=216, duration_seconds=.6,
        source=source, bindings=[], dependencies=[], provenance=[{
            'repository': 'https://github.com/openfilm/library',
            'revision': '19b26b60c1286b412c01ad64971263ae0e2aef13',
            'path': 'transition/glitch/overlay.css', 'license': 'CC0-1.0',
            'source_sha256': CSS_SHA256,
        }])
