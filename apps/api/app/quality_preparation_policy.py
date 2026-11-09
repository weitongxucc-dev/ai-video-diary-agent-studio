"""Finite first use of the existing streaming quality worker, not calibration."""
from app.quality_worker_runtime import QualityBudget, QUALITY_PHASES


QUALITY_FIRST_USE_POLICY = 'quality-stream-first-use/v1'
# QualityWorkerContext drains each pipe in chunks of at most 65536 bytes.
# First use allocates one such chunk to a report/metadata response and one to
# diagnostics. These are engineering allocations, not measured media capacity.
QUALITY_FIRST_USE_PACKET_BYTES = 65536


def quality_preparation_budget(*, host_budget, tools):
    """Borrow only a finite process envelope; inspect tool bytes independently.

    New quality requests use quality_stream_v1: EOF counters, PCM samples and
    source file bytes are work, never accumulated in these packet buffers.
    fit_quality_result may reduce optional examples to fit a report, but must
    fail if identities, measurements and complete summaries do not fit.
    Legacy bulk fields remain bounded by one packet (input by the existing host
    unit ceiling); they do not grant legacy whole-file capacity or alter a
    previously sealed request. No CPU measurement becomes a quality measurement.
    """
    cpu = host_budget.cpu_render
    if cpu is None or host_budget.memory_policy is None:
        raise ValueError('Quality first use needs an existing CPU and host memory policy')
    packet = QUALITY_FIRST_USE_PACKET_BYTES
    # A sealed request includes at most all five phase packets. Admission adds
    # that request allocation separately. Reserve five saved phase packets and
    # one staged worker result here; the source video is read, never copied.
    metadata_disk = (len(QUALITY_PHASES) + 1) * packet
    if (cpu.additional_memory_bytes < (len(QUALITY_PHASES) + 2) * packet
            or host_budget.sample_seconds > .25):
        raise ValueError('CPU process envelope cannot hold the quality pipe allocations')
    return QualityBudget(
        max_input_bytes=min(cpu.max_input_bytes, host_budget.max_unit_input_bytes),
        max_pcm_bytes=packet, max_frame_json_bytes=packet, max_report_bytes=packet,
        max_stderr_bytes=packet, max_timeout_seconds=cpu.max_timeout_seconds,
        decoder_threads=1, max_process_rss_bytes=cpu.additional_memory_bytes,
        max_process_threads=cpu.max_process_threads, runtime_sample_seconds=host_budget.sample_seconds,
        additional_memory_bytes=cpu.additional_memory_bytes, additional_disk_bytes=metadata_disk,
        max_tool_bytes=max(tools['ffmpeg_size_bytes'], tools['ffprobe_size_bytes']),
        ffmpeg_path=tools['ffmpeg_path'], ffprobe_path=tools['ffprobe_path'],
        ffmpeg_sha256=tools['ffmpeg_sha256'], ffprobe_sha256=tools['ffprobe_sha256'],
        worker_sha256=tools['worker_sha256'])
