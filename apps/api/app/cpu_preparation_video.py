"""Construct the measured CPU video probe, including actual RGBA compositing."""
import math

from PIL import Image


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
    # Synthetic sources stay in the explicitly bounded complex graph, avoiding
    # one automatic graph thread pool per lavfi input.
    graph = ''.join(f'testsrc2=size={shape.width_px}x{shape.height_px}:rate={shape.frame_rate}:'
        f'duration={frames / shape.frame_rate},trim=end_frame={frames},setpts=PTS-STARTPTS[v{i}];'
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
