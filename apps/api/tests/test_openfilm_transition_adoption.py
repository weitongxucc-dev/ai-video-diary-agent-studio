"""Original CSS, real edit transactions in a temporary database; no real media."""
from uuid import uuid4

import pytest

from app.creation.openfilm_transition_catalog import ASSETS, CSS_SHA256, TEMPLATE_ID, glitch_template
from app.creation.visual_template_catalog import trusted_inline_visual
from app.media.template_document import template_document


def test_original_glitch_css_is_held_by_existing_offline_clock_and_tampering_fails(tmp_path, monkeypatch):
    import app.creation.openfilm_transition_catalog as adapter
    visual = glitch_template()
    assert (ASSETS / 'overlay.css').read_text() in visual.source
    assert visual.provenance[0].source_sha256 == CSS_SHA256
    assert trusted_inline_visual(visual)
    document = template_document(visual)
    assert "connect-src 'none'" in document
    assert 'filmHost.time(t);await film.frame(t);filmHost.settle(t)' in document
    (tmp_path / 'overlay.css').write_text('changed CSS')
    monkeypatch.setattr(adapter, 'ASSETS', tmp_path)
    with pytest.raises(ValueError, match='固定来源摘要'):
        glitch_template()


def test_glitch_template_reads_previews_adopts_and_undoes_with_original_source(tmp_path, monkeypatch):
    from app import main
    from test_timeline_restore_application import restore_http_state
    state = restore_http_state(tmp_path, monkeypatch)
    monkeypatch.setattr(main, '_wake_assistant_continuations', lambda: None)
    template = state.client.get(f'/api/video-templates/{TEMPLATE_ID}', params={'version': 1})
    assert template.status_code == 200, template.text
    visual = template.json()['settings']['visual']
    assert visual['source'] == glitch_template().source
    original = state.repo.get_creation_task_montage_plan('ws', state.task.id)
    layer = {'kind': 'template', 'id': 'glitch-layer', 'name': '信号故障 · v2',
        'template_id': TEMPLATE_ID, 'template_version': 1, 'definition': visual,
        'start_seconds': 1, 'end_seconds': 1.6, 'width': 1, 'x': .5, 'y': .5,
        'z_index': 1000}
    prepared = state.client.post(state.base_url + '/edits/exact/preview?draft=true', json={
        'operation_type': 'EditGraphicOverlay', 'overlay_action': 'add', 'graphic_overlay': layer})
    assert prepared.status_code == 200, prepared.text
    assert state.repo.get_creation_task_montage_plan('ws', state.task.id) == original
    proposal = prepared.json()['proposal']
    accepted = state.client.post(state.base_url + f"/edits/{proposal['id']}/confirm", json={'request_id': str(uuid4())})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()['operation']['status'] == 'applied'
    montage = state.repo.get_creation_task_montage_plan('ws', state.task.id)
    saved, = montage['graphic_overlays']
    assert saved['definition']['source'] == visual['source']
    assert saved['definition']['provenance'] == visual['provenance']
    assert saved['template_id'] == TEMPLATE_ID and saved['template_version'] == 1
    assert (saved['start_seconds'], saved['end_seconds']) == (1, 1.6)
    assert montage['clips'] == original['clips']
    assert montage['audio_timeline'] == original['audio_timeline']
    head = state.repo.task_records.get_creation_task('ws', state.task.id)
    version = state.repo.timeline_records.get_timeline_version('ws', head.current_timeline_version_id)
    assert version.snapshot['montage']['graphic_overlays'][0]['definition']['provenance'] == visual['provenance']
    undone = state.client.post(state.base_url + '/timeline/undo', json={'request_id': str(uuid4()),
        'target_operation_id': accepted.json()['operation']['id'],
        'expected_timeline_version_id': version.id, 'expected_timeline_sequence': version.sequence})
    assert undone.status_code == 200, undone.text
    assert state.repo.get_creation_task_montage_plan('ws', state.task.id)['graphic_overlays'] == []
