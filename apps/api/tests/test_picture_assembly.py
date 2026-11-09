import hashlib
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.creation.assembly_manifest import iter_picture_windows
from app.montage import MontageClip, render_granted_picture_window


def clip(identifier, start, end):
    return MontageClip(id=identifier, cue_id=identifier, asset_id='source', segment_id='source-segment',
        source_path='workspace://source', source_start_seconds=0, source_end_seconds=end-start,
        timeline_start_seconds=start, timeline_end_seconds=end, match_score=1,
        match_reasons=['已批准'], needs_confirmation=False)


def test_windows_cover_absolute_frames_without_resetting_original_fade_recipe():
    first, second = clip('first', 0, 1.1), clip('second', 1.1, 7/3)
    first.visual_effects = {'fade_in_seconds': .2, 'fade_out_seconds': .2}
    plan = SimpleNamespace(clips=[first, second], duration_seconds=7/3, aspect_ratio='16:9')
    windows = list(iter_picture_windows(plan, source_identities={'workspace://source': {'sha256': 'a'*64}}, max_frames_per_unit=7))
    assert windows[0].timeline_start_frame == 0 and windows[-1].timeline_end_frame == 70
    assert all(left.timeline_end_frame == right.timeline_start_frame for left, right in zip(windows, windows[1:]))
    assert all(0 < window.frame_count <= 7 for window in windows)
    original = [window for window in windows if window.clip.id == 'first']
    assert len({window.filter_recipe for window in original}) == 1
    assert [window.first_frame_in_clip for window in original] == [0, 7, 14, 21, 28]
    for window in windows:
        window.validate()
    with pytest.raises(ValueError, match='封存摘要'):
        replace(windows[1], first_frame_in_clip=0).validate()
    second.transition_in_seconds = .1
    with pytest.raises(ValueError, match='非硬切'):
        list(iter_picture_windows(plan, source_identities={'workspace://source': {}}, max_frames_per_unit=7))


def test_native_windows_match_whole_cut_frames_across_fps_conversion_and_fades(tmp_path, granted_media):
    import subprocess
    source = tmp_path / '24fps.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=64x64:rate=24:duration=1',
        '-an', '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', str(source)], check=True)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    cut = clip('original', 0, 1)
    cut.visual_effects = {'fade_in_seconds': .2, 'fade_out_seconds': .2, 'exposure': .04}
    plan = SimpleNamespace(clips=[cut], duration_seconds=1, aspect_ratio='16:9')
    identities = {'workspace://source': {'sha256': before, 'size_bytes': source.stat().st_size}}
    whole = next(iter_picture_windows(plan, source_identities=identities, max_frames_per_unit=30))
    windows = list(iter_picture_windows(plan, source_identities=identities, max_frames_per_unit=7))
    def render_and_hash(window):
        output = tmp_path / f'{window.id}.nut'
        with granted_media([source], kind='creation_render') as context:
            render_granted_picture_window(window, source, output, context)
            result = context.run(['ffmpeg', '-v', 'error', '-threads', '1', '-i', str(output),
                '-map', '0:v:0', '-f', 'framemd5', '-'])
            assert result.returncode == 0
            hashes = [line.rsplit(',', 1)[1].strip() for line in result.stdout.decode('ascii').splitlines()
                if line and not line.startswith('#')]
        assert len(hashes) == window.frame_count
        return hashes
    expected = render_and_hash(whole)
    actual = [value for window in windows for value in render_and_hash(window)]
    assert len(actual) == 30 and actual == expected
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_native_fmp4_tree_preserves_every_encoded_frame_and_exact_packet_clock(tmp_path, granted_media):
    import json
    import subprocess
    from app.canvas import canvas_size
    from app.montage import render_granted_picture_fragment, concatenate_granted_picture_fragments
    source = tmp_path / 'source.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=64x64:rate=24:duration=1',
        '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(source)], check=True)
    cut = clip('original', 0, 1)
    cut.visual_effects = {'fade_in_seconds': .2, 'fade_out_seconds': .2}
    plan = SimpleNamespace(clips=[cut], duration_seconds=1, aspect_ratio='16:9')
    windows = iter_picture_windows(plan, source_identities={'workspace://source': {
        'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}}, max_frames_per_unit=7)
    def frame_hashes(path, context):
        result = context.run(['ffmpeg', '-v', 'error', '-threads', '1', '-i', str(path),
            '-map', '0:v:0', '-f', 'framemd5', '-'])
        assert result.returncode == 0
        return [line.rsplit(',', 1)[1].strip() for line in result.stdout.decode('ascii').splitlines()
            if line and not line.startswith('#')]
    level, expected = [], []
    for window in windows:
        path = tmp_path / f'{window.id}.mp4'
        with granted_media([source], kind='creation_render') as context:
            render_granted_picture_fragment(window, source, path, context)
            expected.extend(frame_hashes(path, context))
        level.append((path, window.frame_count))
    generation = 0
    while len(level) > 1:
        following = []
        for offset in range(0, len(level), 2):
            parents = level[offset:offset+2]
            if len(parents) == 1:
                following.extend(parents)
                continue
            path = tmp_path / f'level-{generation}-{offset}.mp4'
            with granted_media([item[0] for item in parents], kind='creation_render') as context:
                concatenate_granted_picture_fragments(parents, path, context, expected_size=canvas_size('16:9'))
            following.append((path, sum(item[1] for item in parents)))
        level = following
        generation += 1
    final, count = level[0]
    with granted_media([final], kind='creation_render') as context:
        assert frame_hashes(final, context) == expected and len(expected) == count == 30
        result = context.run(['ffprobe', '-v', 'error', '-show_entries', 'packet=pts,dts,duration', '-of', 'json', str(final)])
        packets = json.loads(result.stdout)['packets']
        assert [(row['pts'], row['dts'], row['duration']) for row in packets] == [
            (frame*3000, frame*3000, 3000) for frame in range(30)]
        with pytest.raises(ValueError, match='全部真实父片段'):
            concatenate_granted_picture_fragments([(final, 30), (final, 30)], tmp_path/'spoof.mp4',
                context, expected_size=canvas_size('16:9'))
