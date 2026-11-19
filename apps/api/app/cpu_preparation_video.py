"""Construct measured CPU video probes for the production render paths."""
import hashlib
import json
import math
from fractions import Fraction

from PIL import Image

from app.visual_effects import color_filters, framing_filter, geometry_filters


PRESENTER_DELIVERY_CONTRACT = 'presenter-h264-serial-ffv1-delivery/v1'
PRESENTER_SOURCE_FRAME_RATE = 25


def _media_digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def _probe_video(plan, run, path, *, codec, size, frames, frame_rate=30, audio_codec=None):
    counter = 'frames' if audio_codec else 'packets'
    count_field = f'nb_read_{counter}'
    result = run([plan['ffprobe'], '-v', 'error', '-threads', '1', f'-count_{counter}',
        '-show_entries', f'stream=codec_type,codec_name,width,height,avg_frame_rate,{count_field}:format=duration',
        '-of', 'json', str(path)])
    if result.stderr:
        raise ValueError('人物组合 CPU 样片媒体检查报告了解码错误')
    probe = json.loads(result.stdout)
    video = next(item for item in probe['streams'] if item['codec_type'] == 'video')
    duration = float(probe['format']['duration'])
    if (video['codec_name'] != codec or (video['width'], video['height']) != size
            or int(video[count_field]) != frames
            or (codec == 'h264' and Fraction(video['avg_frame_rate']) != frame_rate)
            or not math.isfinite(duration) or abs(duration - frames / frame_rate) > 1 / frame_rate + 1e-6):
        raise ValueError(f'人物组合 CPU 样片未达到 {codec} 尺寸或帧数')
    if audio_codec and not any(item['codec_type'] == 'audio' and item['codec_name'] == audio_codec
                               for item in probe['streams']):
        raise ValueError('人物组合 CPU 样片缺少预期音轨')
    return {'codec': codec, 'width_px': size[0], 'height_px': size[1], 'frames': frames, 'frame_rate': frame_rate,
        'duration_seconds': duration, 'bytes': path.stat().st_size, 'sha256': _media_digest(path)}


def measure_presenter_delivery(plan, shape, audio, run, *, max_bytes):
    """Use encoded H.264 inputs and the production FFV1 and delivery commands."""
    from app.audio_timeline import narration_audio_timeline
    from app.montage import (CreationTaskMontagePlan, MontageClip, MontageRenderer,
        _write_frame_subtitle_input, bounded_render_command, serial_picture_command)

    root = audio.parent
    size = shape.width_px, shape.height_px
    material_size = shape.source_width_px or size[0], shape.source_height_px or size[1]
    total_frames = round(plan['probe_seconds'] * 30)
    frame_counts = [total_frames // shape.clip_count + (index < total_frames % shape.clip_count)
                    for index in range(shape.clip_count)]
    # LatentSync 1.6 emits 25 fps H.264. Its actual picture job reframes
    # to the same 30 fps delivery clock as the original material.
    groups = (('presenter', size, PRESENTER_SOURCE_FRAME_RATE, frame_counts[:shape.presenter_clip_count]),
              ('material', material_size, 30, frame_counts[shape.presenter_clip_count:]))
    paths = [audio]
    inputs, clips, shot_paths, caption_spans = {}, [], [], []
    picture_lines = ['ffconcat version 1.0']
    caption = root / 'presenter-caption.png'
    with Image.new('RGBA', size, (255, 255, 255, 32)) as bitmap:
        bitmap.save(caption)
    paths.append(caption)
    position = 0
    for kind, source_size, source_rate, counts in groups:
        source = root / f'{kind}-h264.mp4'
        fixture_frames = math.ceil(max(counts) * source_rate / 30) + 1
        fixture_filter = f'testsrc2=size={source_size[0]}x{source_size[1]}:rate={source_rate}'
        if kind == 'presenter':
            fixture_filter += ',hue=s=0.5'
        paths.append(source)
        run([plan['ffmpeg'], '-v', 'error', '-y', '-filter_threads', '1', '-f', 'lavfi',
            '-i', fixture_filter, '-frames:v', str(fixture_frames), '-an', '-c:v', 'libx264',
            '-threads', '1', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p',
            '-fs', str(max_bytes), str(source)], tuple(paths))
        inputs[kind] = _probe_video(plan, run, source, codec='h264', size=source_size,
            frames=fixture_frames, frame_rate=source_rate)
        for frames in counts:
            index = len(clips)
            target = root / f'presenter-probe-{index:03}.nut'
            clip = MontageClip(f'probe-{index}', 'probe', kind, None, str(source),
                0, frames / 30, position / 30, (position + frames) / 30, 1, [], False,
                person_render_id='probe-person' if kind == 'presenter' else None,
                visual_effects=shape.visual_effects or {})
            remaining = max_bytes - sum(path.stat().st_size for path in paths)
            if remaining <= 0:
                raise ValueError('人物组合 CPU 样片已达到中间文件磁盘上限')
            command = serial_picture_command(clip, source, target, size=size, frames=frames,
                threads=1, max_bytes=remaining)
            command[0] = plan['ffmpeg']
            paths.append(target)
            run(command, tuple(paths))
            _probe_video(plan, run, target, codec='ffv1', size=size, frames=frames)
            shot_paths.append(target)
            picture_lines.extend((f'file {target.name}', f'duration {frames / 30:.10f}'))
            caption_spans.append((position, position + frames))
            clips.append(clip)
            position += frames

    pictures = root / 'presenter-probe.ffconcat'
    pictures.write_text('\n'.join(picture_lines) + '\n', encoding='utf-8')
    captions, offset = _write_frame_subtitle_input(caption_spans, [caption] * len(caption_spans),
        root, size=size, frame_count=total_frames)
    caption_paths = captions, root / 'subtitle-transparent.png'
    duration = total_frames / 30
    montage = CreationTaskMontagePlan(id='presenter-cpu-preparation', workspace_id=plan['workspace_id'],
        task_id='presenter-cpu-preparation', script_id='probe', script_title='Presenter CPU preparation',
        status='ready', cues=[], clips=clips, duration_seconds=duration, low_confidence_clip_count=0,
        audio_timeline=narration_audio_timeline('narration://artifact/presenter-cpu-preparation', duration))
    output = root / 'presenter-delivery.mp4'
    command = MontageRenderer(root).build_command(montage, audio, output, [], subtitle_intervals=[],
        subtitle_sequence=(captions, offset), picture_sequence=pictures)
    command[0] = plan['ffmpeg']
    temporary_bytes = sum(path.stat().st_size for path in (caption, *caption_paths, pictures, *shot_paths))
    bounded_paths = (*paths, pictures, *caption_paths)
    output_limit = max_bytes - sum(path.stat().st_size for path in bounded_paths)
    if output_limit <= 0:
        raise ValueError('人物组合 CPU 样片已达到交付文件磁盘上限')
    run(bounded_render_command(command, 1, output_limit), (*bounded_paths, output))
    media = _probe_video(plan, run, output, codec='h264', size=size, frames=total_frames, audio_codec='aac')
    return {**media, 'contract': PRESENTER_DELIVERY_CONTRACT, 'inputs': inputs,
        'clip_count': len(shot_paths), 'presenter_clip_count': shape.presenter_clip_count,
        'serial_codec': 'ffv1', 'audio_codec': 'aac', 'temporary_bytes': temporary_bytes}


def preparation_video_command(plan, shape, video):
    duration, count = plan['probe_seconds'], shape.clip_count
    command = [plan['ffmpeg'], '-hide_banner', '-loglevel', 'error', '-y',
        '-filter_threads', '1', '-filter_complex_threads', '1']
    graphic_paths = ()
    if shape.overlay_count:
        overlay = video.with_name('overlay.png')
        # A full-canvas translucent input exercises the actual PNG decoder and
        # alpha compositor; it does not claim unmeasured zero-layer capacity.
        with Image.new('RGBA', (shape.width_px, shape.height_px), (255, 255, 255, 96)) as image:
            image.save(overlay)
        graphic_paths = (overlay,)
        for _ in range(shape.overlay_count):
            command += ['-threads', '1', '-loop', '1', '-framerate', str(shape.frame_rate), '-i', str(overlay)]
    total_frames = math.ceil(duration * shape.frame_rate)
    frame_counts = [total_frames // count + (index < total_frames % count) for index in range(count)]
    source_width = shape.source_width_px or shape.width_px
    source_height = shape.source_height_px or shape.height_px
    treatments = []
    if shape.visual_effects is not None:
        treatments.extend(color_filters(shape.visual_effects))
    if (source_width, source_height) != (shape.width_px, shape.height_px):
        treatments.append(framing_filter('fill', .5, .5, shape.width_px, shape.height_px))
    if shape.visual_effects is not None:
        treatments.extend(geometry_filters(shape.visual_effects, shape.width_px, shape.height_px))
    treatment = ','.join([*treatments, 'setsar=1']) + ',' if treatments else ''
    # Synthetic sources stay in the explicitly bounded complex graph, avoiding
    # one automatic graph thread pool per lavfi input.
    graph = ''.join(f'testsrc2=size={source_width}x{source_height}:rate={shape.frame_rate}:'
        f'duration={frames / shape.frame_rate},{treatment}trim=end_frame={frames},setpts=PTS-STARTPTS[v{i}];'
        for i, frames in enumerate(frame_counts))
    label = 'base' if shape.overlay_count else 'v'
    graph += ''.join(f'[v{i}]' for i in range(count)) + f'concat=n={count}:v=1:a=0[{label}]'
    for index in range(shape.overlay_count):
        output = 'v' if index == shape.overlay_count - 1 else f'layer{index}'
        graph += f';[{label}][{index}:v]overlay=0:0:shortest=1[{output}]'
        label = output
    command += ['-filter_complex', graph, '-map', '[v]', '-t', str(duration),
        '-c:v', plan['policy']['encoder'], '-threads', '1', '-preset', plan['policy']['encoder_preset'],
        '-crf', str(plan['policy']['encoder_crf']), '-pix_fmt', 'yuv420p', str(video)]
    return command, graphic_paths


def preparation_delivery_command(plan, shape, video, audio):
    """Exercise the production three-input picture/audio/caption delivery path."""
    from app.montage import (CreationTaskMontagePlan, MontageClip, MontageRenderer,
        bounded_render_command)
    from app.audio_timeline import narration_audio_timeline

    duration = plan['probe_seconds']
    caption = video.with_name('delivery-caption.png')
    with Image.new('RGBA', (shape.width_px, shape.height_px), (255, 255, 255, 32)) as image:
        image.save(caption)
    pictures = video.with_name('delivery-picture.ffconcat')
    pictures.write_text(f'ffconcat version 1.0\nfile {video.name}\nduration {duration}\n', encoding='utf-8')
    captions = video.with_name('delivery-caption.ffconcat')
    captions.write_text(f'ffconcat version 1.0\nfile {caption.name}\nduration {duration}\nfile {caption.name}\n', encoding='utf-8')
    output = video.with_name('delivery.mp4')
    montage = CreationTaskMontagePlan(id='cpu-preparation', workspace_id=plan['workspace_id'],
        task_id='cpu-preparation', script_id='cpu-preparation', script_title='CPU preparation',
        status='ready', cues=[], clips=[MontageClip('probe', 'probe', 'probe', 'probe',
            str(video), 0, duration, 0, duration, 1, [], False)],
        duration_seconds=duration, low_confidence_clip_count=0,
        audio_timeline=narration_audio_timeline('narration://artifact/cpu-preparation', duration))
    command = MontageRenderer(video.parent).build_command(montage, audio, output, [],
        subtitle_intervals=[], subtitle_sequence=(captions, (0, 0)), picture_sequence=pictures)
    command[0] = plan['ffmpeg']
    return bounded_render_command(command, 1, plan['bootstrap_budget']['cpu_render']['max_output_bytes']), (
        output, caption, pictures, captions)
