"""Pinned SVG previews and template adoption; all writes use a temporary DB."""
from uuid import uuid4
from xml.etree import ElementTree

import pytest
from fastapi.testclient import TestClient

from app.creation.fluent_sticker_catalog import ASSETS, fluent_stickers, sticker_manifest
from app.creation.visual_template_catalog import trusted_inline_visual
from app.editing_resource_catalog import editing_resource_catalog
from app.main import app


def test_sticker_pack_exposes_three_original_offline_previews_and_fixed_template_entries():
    client = TestClient(app)
    resources = [item for item in editing_resource_catalog().resources if item.resource_type == 'svg_sticker']
    assert {item.id for item in resources} == {'fluent:sticker-red-heart', 'fluent:sticker-party-popper', 'fluent:sticker-light-bulb'}
    manifest = sticker_manifest()
    for resource, (_, _, definition), asset in zip(resources, fluent_stickers(), manifest['items']):
        assert resource.category_id == 'stickers' and resource.status == 'connected'
        assert resource.runtime_requirements['template_id'] == 'builtin_visual_' + asset['id']
        assert resource.runtime_requirements['width'] == .22
        assert resource.provenance[0].revision == '1ffb34c752ecf5d402f04cfb4b392c77f57c54bc'
        assert resource.provenance[0].license_spdx == 'MIT'
        assert definition.width == definition.height
        assert definition.dependencies == [] and trusted_inline_visual(definition)
        preview = client.get(resource.preview.thumbnail_ref)
        assert preview.status_code == 200
        assert preview.headers['content-type'].startswith('image/svg+xml')
        assert preview.content == (ASSETS / asset['file']).read_bytes()
        root = ElementTree.fromstring(preview.content)
        assert {node.tag.rsplit('}', 1)[-1] for node in root.iter()} <= {'svg', 'path', 'ellipse'}
        assert all(not key.lower().startswith('on') and 'href' not in key
            for node in root.iter() for key in node.attrib)
        assert client.get(f'/api/editing-resources/{resource.id}/preview?version=99').status_code == 404


def test_sticker_adapter_rejects_changed_source(tmp_path, monkeypatch):
    import app.creation.fluent_sticker_catalog as adapter
    manifest = sticker_manifest()
    (tmp_path / 'manifest.json').write_text((ASSETS / 'manifest.json').read_text())
    (tmp_path / 'LICENSE').write_bytes((ASSETS / 'LICENSE').read_bytes())
    (tmp_path / manifest['items'][0]['file']).write_text('<svg/>')
    monkeypatch.setattr(adapter, 'ASSETS', tmp_path)
    with pytest.raises(ValueError, match='固定来源摘要'):
        list(fluent_stickers())


def test_sticker_preview_adopt_and_undo_preserve_geometry_source_and_original_work(tmp_path, monkeypatch):
    from app import main
    from test_timeline_restore_application import restore_http_state
    state = restore_http_state(tmp_path, monkeypatch)
    monkeypatch.setattr(main, '_wake_assistant_continuations', lambda: None)
    template_id = 'builtin_visual_fluent-sticker-party-popper'
    response = state.client.get(f'/api/video-templates/{template_id}?version=1')
    assert response.status_code == 200, response.text
    visual = response.json()['settings']['visual']
    original = state.repo.get_creation_task_montage_plan('ws', state.task.id)
    layer = {'kind': 'template', 'id': 'sticker-layer', 'name': '庆祝礼炮 · Fluent · v1',
        'template_id': template_id, 'template_version': 1, 'definition': visual,
        'start_seconds': .5, 'end_seconds': 2.5, 'width': .25, 'x': .7, 'y': .2, 'z_index': 1000}
    prepared = state.client.post(state.base_url + '/edits/exact/preview?draft=true', json={
        'operation_type': 'EditGraphicOverlay', 'overlay_action': 'add', 'graphic_overlay': layer})
    assert prepared.status_code == 200, prepared.text
    assert state.repo.get_creation_task_montage_plan('ws', state.task.id) == original
    proposal = prepared.json()['proposal']
    accepted = state.client.post(state.base_url + f"/edits/{proposal['id']}/confirm", json={'request_id': str(uuid4())})
    assert accepted.status_code == 200, accepted.text
    operation = accepted.json()['operation']
    assert operation['status'] == 'applied'
    montage = state.repo.get_creation_task_montage_plan('ws', state.task.id)
    saved, = montage['graphic_overlays']
    assert saved['definition'] == visual
    assert saved['width'] == .25 and saved['x'] == .7 and saved['y'] == .2
    assert (saved['start_seconds'], saved['end_seconds']) == (.5, 2.5)
    assert montage['clips'] == original['clips'] and montage['audio_timeline'] == original['audio_timeline']
    head = state.repo.task_records.get_creation_task('ws', state.task.id)
    version = state.repo.timeline_records.get_timeline_version('ws', head.current_timeline_version_id)
    assert version.snapshot['montage']['graphic_overlays'][0]['definition'] == visual
    undone = state.client.post(state.base_url + '/timeline/undo', json={'request_id': str(uuid4()),
        'target_operation_id': operation['id'], 'expected_timeline_version_id': version.id,
        'expected_timeline_sequence': version.sequence})
    assert undone.status_code == 200, undone.text
    assert state.repo.get_creation_task_montage_plan('ws', state.task.id)['graphic_overlays'] == original['graphic_overlays']
