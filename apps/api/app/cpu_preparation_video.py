"""Construct the measured CPU video probe, including actual RGBA compositing."""
import math

from PIL import Image

from app.visual_effects import color_filters, framing_filter, geometry_filters


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
