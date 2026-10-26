import hashlib
import subprocess
from types import SimpleNamespace

from app.creation.assembly_manifest import iter_picture_windows
from app.creation.picture_fragment_layers import picture_window_layers
from app.montage import _subtitle_frame_boundary, render_granted_picture_window
from app.subtitle_schedule import TimedCaption
from test_picture_assembly import clip


def layers():
    captions = [TimedCaption(.01, .4333334, '第一条', {'font_size': 64, 'color': '#FFFFFF'}),
        TimedCaption(.2333334, .8333334, '第二条', {'font_size': 72, 'color': '#FFE070', 'background_opacity': .5})]
    graphics = [{'kind': 'text', 'id': 'title', 'text': '标题',
        'start_seconds': .1666667, 'end_seconds': .7333334, 'font_size': 72, 'color': '#80FF90',
        'x': .5, 'y': .15, 'width': .8}]
    return captions, graphics


def test_window_burnin_uses_global_quantization_and_preserves_layer_order():
    cut = clip('original', 0, 1)
    plan = SimpleNamespace(clips=[cut], duration_seconds=1, aspect_ratio='16:9')
    captions, graphics = layers()
    windows = list(iter_picture_windows(plan, source_identities={'workspace://source': {}}, max_frames_per_unit=7))
    for window in windows:
        projected = picture_window_layers(window, captions=captions, graphic_overlays=graphics)
        for frame in range(window.frame_count):
            absolute = frame + window.timeline_start_frame
            actual = [(item.kind, item.value) for item in projected if item.first_frame <= frame < item.last_frame]
            expected = [('graphic', item) for item in graphics
                if _subtitle_frame_boundary(item['start_seconds']) <= absolute < _subtitle_frame_boundary(item['end_seconds'])]
            expected += [('subtitle', item) for item in captions
                if _subtitle_frame_boundary(item.start_seconds) <= absolute < _subtitle_frame_boundary(item.end_seconds)]
            assert actual == expected
    assert next(item for item in picture_window_layers(windows[1], captions=captions, graphic_overlays=[])
        if item.value is captions[1]).first_frame == 0


def test_native_window_burnin_matches_full_picture_graphic_and_overlapping_captions(tmp_path, granted_media):
    source = tmp_path / 'source.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=64x64:rate=24:duration=1',
        '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(source)], check=True)
    cut = clip('original', 0, 1)
    cut.visual_effects = {'fade_in_seconds': .2, 'fade_out_seconds': .2}
    plan = SimpleNamespace(clips=[cut], duration_seconds=1, aspect_ratio='16:9')
    identities = {'workspace://source': {'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}}
    captions, graphics = layers()
    def render_hashes(window, name):
        output = tmp_path / f'{name}.nut'
        projected = picture_window_layers(window, captions=captions, graphic_overlays=graphics)
        with granted_media([source], kind='creation_render') as context:
            render_granted_picture_window(window, source, output, context, frame_layers=projected)
            result = context.run(['ffmpeg', '-v', 'error', '-threads', '1', '-i', str(output),
                '-map', '0:v:0', '-f', 'framemd5', '-'])
            assert result.returncode == 0
        return [line.rsplit(',', 1)[1].strip() for line in result.stdout.decode().splitlines()
            if line and not line.startswith('#')]
    whole = next(iter_picture_windows(plan, source_identities=identities, max_frames_per_unit=30))
    expected = render_hashes(whole, 'whole')
    windows = iter_picture_windows(plan, source_identities=identities, max_frames_per_unit=7)
    actual = [value for index, window in enumerate(windows) for value in render_hashes(window, f'window-{index}')]
    assert len(actual) == 30 and actual == expected


def test_production_render_recipe_and_fragment_bind_actual_burnin_contract(tmp_path, monkeypatch):
    from dataclasses import replace
    from datetime import datetime, timezone
    import pytest
    import app.creation.render_stage_use_case as render_stage
    from app.creation.picture_fragment_layers import picture_fragment_recipe, PICTURE_LAYER_CONTRACT
    from app.montage import CreationTaskMontagePlan, render_granted_picture_fragment
    now = datetime.now(timezone.utc)
    cut = clip('original', 0, 1)
    plan = CreationTaskMontagePlan(id='plan', workspace_id='local', task_id='task', script_id='script',
        script_title='approved', status='ready', cues=[], clips=[cut], duration_seconds=1,
        low_confidence_clip_count=0, created_at=now, updated_at=now, aspect_ratio='16:9')
    current = render_stage.render_input_payload(plan, subtitles_visible=True, subtitle_tracks=[])
    old = {key: value for key, value in current.items() if key != 'picture_layer_contract'}
    assert current['picture_layer_contract'] == PICTURE_LAYER_CONTRACT
    assert render_stage.render_input_digest_for(old) != render_stage.render_input_digest_for(current)
    monkeypatch.setattr(render_stage, 'PICTURE_LAYER_CONTRACT', 'different-raster-contract')
    changed = render_stage.render_input_payload(plan, subtitles_visible=True, subtitle_tracks=[])
    assert render_stage.render_input_digest_for(changed) != render_stage.render_input_digest_for(current)
    window = next(iter_picture_windows(plan, source_identities={'workspace://source': {}}, max_frames_per_unit=30))
    captions, graphics = layers()
    projected = picture_window_layers(window, captions=captions, graphic_overlays=graphics)
    sealed = picture_fragment_recipe(window, projected)
    changed_layers = (*projected[:-1], replace(projected[-1], value=replace(captions[-1], text='changed text')))
    assert picture_fragment_recipe(window, changed_layers)['digest'] != sealed['digest']
    context = SimpleNamespace(check=lambda: None, grant=SimpleNamespace(request_id='frozen-request'),
        scheduler=SimpleNamespace(_get=lambda _: SimpleNamespace(resources={'picture_fragment_recipe': sealed})))
    with pytest.raises(PermissionError, match='实际图层内容或栅格合同'):
        render_granted_picture_fragment(window, tmp_path/'not-read.mp4', tmp_path/'not-created.mp4', context,
            frame_layers=changed_layers)
    assert not (tmp_path/'not-created.mp4').exists()
