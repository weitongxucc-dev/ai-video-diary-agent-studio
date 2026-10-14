"""Linked clip application using opaque file identities and synthetic audio only."""
from app.creation.contracts import EditCommand
from test_linked_speed_application import speed_state


def test_insert_preserves_material_reader_identity_and_original_clip_construction(tmp_path):
    state = speed_state(tmp_path)
    identity = {**state.identity, 'material_reader_identity': {'member_id': None, 'local_diagnostic': True}}
    outcome = state.application.apply(EditCommand('ws', state.task.id, identity, state.base.id, 'InsertClip',
        {'base_version_id': state.base.id, 'clip_id': 'clip', 'asset_id': state.neighbour.id,
         'segment_id': state.neighbour.segments[0].id, 'source_start_seconds': 0.1256}))
    inserted = outcome.montage.clips[1]
    assert inserted.asset_id == state.neighbour.id and inserted.segment_id == 'neighbour-segment'
    assert inserted.source_path == state.repo.workspace_media_reference_for_asset('ws', state.neighbour.id)
    assert inserted.source_start_seconds == 0.126 and inserted.source_end_seconds == 4
    assert inserted.cue_id == state.plan.clips[0].cue_id and inserted.role == 'b_roll' and inserted.manually_selected
    assert inserted.match_reasons == ['由用户插入', '当前工作区可用素材']
    assert inserted.match_score == 1 and not inserted.needs_confirmation
    assert outcome.montage.duration_seconds == 7.874
    assert outcome.operation.operation_type == 'InsertClip'
