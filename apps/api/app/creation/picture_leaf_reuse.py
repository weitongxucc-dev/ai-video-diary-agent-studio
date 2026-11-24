"""Validate an adopted hard-cut leaf as a read-only, generation-bound input."""
import os
from pathlib import Path
import re
import shutil
import stat
import sys

from app.creation.render_generation import adopted_manifest, adopted_render, settled_render_producer
from app.montage import PICTURE_FRAGMENT_CONTRACT


def file_identity(path):
    info = Path(path).lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('画面输入或产物不是普通文件')
    return {'device': info.st_dev, 'inode': info.st_ino, 'size_bytes': info.st_size,
            'mtime_ns': info.st_mtime_ns, 'ctime_ns': info.st_ctime_ns}


def _installed(path):
    resolved = Path(path).resolve(strict=True)
    return {'path': os.fspath(resolved), 'file_identity': file_identity(resolved)}


def _implementation(module):
    return _installed(sys.executable if getattr(sys, 'frozen', False) and not Path(module.__file__).is_file()
        else module.__file__)


def picture_basis(window, layers, recipe, asset, *, threads, backend_identity):
    """Only use the narrow runtime whose inputs can be pinned, not fallback fonts/graphics."""
    from PIL import Image, ImageDraw, ImageFont, features
    from app import montage
    from app.materials.source_files import material_segment_eligible
    if backend_identity is None:
        return None
    families = set()
    for layer in layers:
        if layer.kind != 'subtitle':
            return None
        family = layer.value.subtitle_style.get('font_family')
        # Match the worker's explicit-family rules without claiming its
        # fallback font or changing when invalid styles fail production.
        if not isinstance(family, str) or not family.strip():
            return None
        families.add(family.strip())
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        return None
    segment = None
    if window.clip.segment_id is not None:
        segment = next((item for item in asset.segments if item.id == window.clip.segment_id), None)
        if (segment is None or not material_segment_eligible(asset, segment, asset.workspace_id)
                or not segment.start_seconds <= window.clip.source_start_seconds < window.clip.source_end_seconds <= segment.end_seconds):
            return None
    fonts = {}
    try:
        for family in sorted(families):
            path, face = montage._resolve_subtitle_font(family)
            fonts[family] = {'file': _installed(path), 'face': face}
        runtime = {'ffmpeg': _installed(ffmpeg), 'ffprobe': _installed(ffprobe),
            'renderer': _implementation(montage),
            'os': list(os.uname()) if hasattr(os, 'uname') else None,
            'raster': [_implementation(module) for module in
                (Image, ImageDraw, ImageFont, Image.core, ImageFont.core)] if fonts else [],
            'freetype': features.version_module('freetype2') if fonts else None,
            'raqm': features.version_feature('raqm') if fonts else None,
            'fonts': fonts}
    except OSError:
        return None
    if not runtime['os']:
        return None
    return {'contract': PICTURE_FRAGMENT_CONTRACT, 'recipe': recipe, 'threads': threads,
        'backend_identity': backend_identity,
        'asset_id': asset.id, 'asset_revision': asset.updated_at.isoformat(),
        'segment_version': segment.version if segment is not None else None,
        'first_frame': window.timeline_start_frame, 'packet_count': window.frame_count, 'runtime': runtime}


def picture_history(repo, job, adoption, *, connection):
    version, facts, _scope = adoption
    payload = adopted_manifest(repo, job, adoption, field='picture_fragment_manifest_event_id',
        suffix='picture_fragment_manifest', event_type='picture_fragment_manifest', connection=connection)
    if payload['head'] != version.parent_version_id:
        raise ValueError('历史画面清单不属于原采用输入')
    if payload['version'] != 'picture-fragment-assembly/v3':
        return None
    if facts['picture_fragment_manifest_digest'] != payload['digest']:
        raise ValueError('历史采用未封存原画面清单摘要')
    return payload


def leaf_reference(origin_version, unit, record, transform, manifest):
    facts = transform.parameters['picture_fragment']
    return {'origin_version_id': origin_version, 'origin_manifest_digest': manifest['digest'],
        'unit_id': unit['id'], 'artifact_id': record.id, 'sha256': record.sha256, 'size_bytes': record.size_bytes,
        'transform_id': transform.id, 'producer_request_id': facts['producer_request_id'],
        'permit_generation': facts['producer_generation'], 'file_identity': facts['file_identity'],
        'source_sha256': facts['source_sha256']}


def read_leaf(repo, store, job, ref, basis, current_source, *, connection, selecting=False):
    adoption = adopted_render(repo, job, ref['origin_version_id'], connection=connection)
    old = picture_history(repo, job, adoption, connection=connection)
    unit = next((u for u in old['units'] if u['id'] == ref['unit_id']), None) if old is not None else None
    if (unit is None or unit.get('reuse') is not None or unit['reuse_basis'] != basis
            or unit['source'] != current_source or unit['artifact_id'] != ref['artifact_id']):
        raise ValueError('历史叶片没有相同配方的原生产检查点')
    record = repo.execution_records.get_artifact_record(job.workspace_id, unit['artifact_id'], connection=connection)
    transform = repo.lineage_records.get_transform_run(job.workspace_id, f'transform_{record.id}', connection=connection)
    facts = transform.parameters['picture_fragment']
    resources = settled_render_producer(repo, job, transform=transform, scope=adoption[2], kind='creation_render',
        unit_key=f'render:picture-fragment:v1:{unit["id"]}', measured_suffix='picture_fragment_output', connection=connection)
    if ((record.task_id, record.producer_job_id, record.kind) != (job.task_id, job.id, 'picture_fragment')
            or (transform.task_id, transform.job_id, transform.transform_type, transform.status, transform.engine)
                != (job.task_id, job.id, 'picture_fragment', 'succeeded', PICTURE_FRAGMENT_CONTRACT)
            or transform.input_digest != unit['id'] or transform.input_refs != record.input_refs
            or transform.output_refs != [record.logical_ref] or not record.input_refs
            or record.input_refs[0] != unit['source_ref']
            or transform.parameters['manifest_digest'] != old['digest']
            or resources['picture_fragment_manifest_digest'] != old['digest']
            or resources['picture_fragment_unit'] != unit['id']
            or resources['picture_recipe_digest'] != old['picture_recipe'] or facts['recipe_digest'] != old['picture_recipe']
            or resources['picture_fragment_recipe'] != basis['recipe']
            or transform.parameters['picture_fragment_recipe'] != basis['recipe']
            or resources['threads'] != basis['threads']
            or resources['picture_fragment'] != {key: basis[key] for key in ('first_frame','packet_count','contract')}
            or any(facts[k] != v for k,v in resources['picture_fragment'].items())
            or re.fullmatch('[0-9a-f]{64}', facts['source_sha256']) is None):
        raise ValueError('历史叶片原许可与配方、时钟或变换不一致')
    path = store.resolve_file(record.logical_ref)
    if file_identity(path) != facts['file_identity'] or facts['file_identity']['size_bytes'] != record.size_bytes:
        raise ValueError('历史叶片文件已替换或失效')
    checked = leaf_reference(adoption[0].id, unit, record, transform, old)
    if (any(checked[key] != value for key, value in ref.items()) if selecting else checked != ref):
        raise ValueError('历史叶片引用与原产物不一致')
    return checked, (record, path, facts)
