"""Measured HTML and native-program extensions of the existing preparation owner."""
from dataclasses import asdict, replace
import math
import time

import psutil

from app.device_resource_profile import DeviceProfileUnavailable
from app.host_resource_probes import NativeCommandBudget


def extend_render_budget(shape, render, output_limit):
    if shape.template_count:
        from app.media.template_frames import renderer_identity
        renderer_identity()  # Missing dependencies reject before submission.
        render = replace(render, max_template_count=shape.template_count,
            additional_disk_bytes=output_limit*(shape.template_count+1),
            max_process_threads=max(render.max_process_threads, (psutil.cpu_count() or 1)*16))
    return render


def measure_template_inputs(shape, video, duration, command, run):
    if shape.template_count:
        from app.media.template_frames import frame_command, frame_manifest
        from app.creation.visual_template_catalog import visual_templates
        from app.creation.visual_template_contracts import VisualTemplateOverlay
        definition = next(visual_templates())[2]
        overlay = VisualTemplateOverlay(name='设备准备样本', template_id='preparation',
            template_version=1, definition=definition, start_seconds=0, end_seconds=duration)
        for index in range(shape.template_count):
            directory = video.parent / f'template-{index}'
            argv, environment = frame_command(overlay.model_dump(), directory,
                size=(shape.width_px, shape.height_px), first_frame=0,
                frame_count=math.ceil(duration*shape.frame_rate), fps=shape.frame_rate)
            run(argv, (directory,), env=environment)
            manifest = frame_manifest(directory, math.ceil(duration*shape.frame_rate), shape.frame_rate)
            # Replace the corresponding synthetic still input with
            # the measured, real animated RGBA frame sequence.
            offset = command.index('-loop')
            command[offset:offset+6] = ['-f', 'concat', '-safe', '0',
                '-protocol_whitelist', 'file', '-i', str(manifest)]


def measure_native_inputs(shape, video, duration, settings, run):
    native_receipt = None
    if shape.native_command:
        from app.native_preparation_probe import native_probe
        argv, environment, outputs, facts = native_probe(video.parent, settings, shape, duration)
        native_started = time.monotonic()
        measured = run(argv, (outputs,), env=environment)
        native_receipt = {**facts, 'elapsed_seconds': time.monotonic()-native_started,
            'observed_resources': asdict(measured.observed_resources),
            'actual_output_bytes': sum(path.stat().st_size for path in outputs.rglob('*') if path.is_file())}
    return native_receipt


def extend_native_budget(shape, budget, receipt, plan, delivery_duration, original, policy):
    if shape.native_command:
        measured = receipt['native_command']
        observed = measured['observed_resources']
        if not observed['rss_sample_complete'] or not observed['threads_sample_complete']:
            raise DeviceProfileUnavailable('通用程序的独立资源观察缺失，未借用CPU媒体预算')
        ratio = delivery_duration/plan['probe_seconds']
        capacity = math.ceil(max(measured['output_capacity_bytes'], measured['actual_output_bytes'])*ratio*policy.measured_margin)
        native_rss = min(original.cpu_render.additional_memory_bytes, math.ceil(observed['peak_rss_bytes']*policy.measured_margin))
        native = NativeCommandBudget((shape.source_bytes or 0)+capacity, capacity,
            math.ceil(measured['frames']*ratio)+16, 65536, 262144, 1,
            measured['elapsed_seconds']*max(1,ratio)*policy.measured_margin,
            native_rss, math.ceil(observed['peak_threads']*policy.measured_margin), policy.sample_seconds,
            native_rss, 2*capacity)
        budget = replace(budget, native_command=native,
            max_unit_input_bytes=max(budget.max_unit_input_bytes, native.max_input_bytes))
    return budget
