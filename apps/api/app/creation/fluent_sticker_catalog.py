"""Three pinned, unmodified Fluent SVGs in the existing offline template host."""
import hashlib
import json
from pathlib import Path

from app.creation.visual_template_contracts import VisualTemplate

ASSETS = Path(__file__).parents[1] / 'resource_assets/fluent-emoji'


def sticker_manifest():
    return json.loads((ASSETS / 'manifest.json').read_text(encoding='utf-8'))


def _checked_source(file, digest):
    source = (ASSETS / file).read_bytes()
    if hashlib.sha256(source).hexdigest() != digest:
        raise ValueError(f'Fluent 贴纸文件与固定来源摘要不符：{file}')
    return source.decode('utf-8')


def fluent_stickers():
    manifest = sticker_manifest()
    license_text = _checked_source(manifest['license_file'], manifest['license_sha256'])
    origin = {key: manifest[key] for key in ('repository', 'revision', 'license')}
    for item in manifest['items']:
        svg = _checked_source(item['file'], item['sha256'])
        # Retain the upstream art and license when the definition is copied into
        # a task. The existing host supplies the clock, sandbox and renderer.
        source = ('<!doctype html><html><head><meta charset="utf-8">'
            '<style>html,body{margin:0;width:100%;height:100%;overflow:hidden;background:transparent}'
            'svg{display:block;width:100%;height:100%}</style></head><body>'
            + '<!--\n' + license_text + '\n-->\n' + svg
            + '<script>window.film={frame(t){}};</script></body></html>')
        yield item['id'], item['name'] + ' · Fluent', VisualTemplate(
            category='reaction', width=320, height=320, duration_seconds=3,
            source=source, bindings=[], dependencies=[], provenance=[
                origin | {'path': item['path'], 'source_sha256': item['sha256']},
                origin | {'path': 'LICENSE', 'source_sha256': manifest['license_sha256']},
            ])


def sticker_preview_path(resource_id):
    for item in sticker_manifest()['items']:
        if resource_id == 'fluent:' + item['id'].removeprefix('fluent-'):
            return ASSETS / item['file']
    return None
