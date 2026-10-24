"""Execution facts must not imply measurement, admission or whole-task support."""
import json
from dataclasses import replace
from types import SimpleNamespace

from app.creation.execution_capabilities import execution_capability_facts
from app.device_runtime import OwnerRegistration
from app.host_resource_probes import CpuAudioBudget, HostBudget, NarrationBudget


def forbidden(*args, **kwargs):
    raise AssertionError("capability reads must not execute owners or probe resources")


def configured_runtime():
    audio = CpuAudioBudget(2, 33554432, 30, 30, 48000, 2, 8388608, 1, 30,
                           268435456, 16, .1, 268435456, 33554432)
    narration = NarrationBudget(100, 1, 1024, 30, 3145728, 1, 300,
                               268435456, 16, .1, 268435456, 33554432)
    budget = HostBudget(100, 1073741824, None, 33554432, 1000000, 33554432,
                        narration=narration, cpu_audio=audio)
    owner = SimpleNamespace(verify_submission=forbidden, render_facts=forbidden,
                            audio_mix_facts=forbidden, pcm_assembly_baseline=forbidden,
                            segments=SimpleNamespace(next_initial=forbidden),
                            private_path="/private/credential.json")
    executor = SimpleNamespace(execute=forbidden, render_action=forbidden,
                               recover_pcm_unit=forbidden, narration_stage=object())
    return budget, {"creation_job": OwnerRegistration(owner, executor)}


def by_purpose(facts, purpose):
    return next(item for item in facts["capabilities"] if item["purpose"] == purpose)


def test_missing_runtime_reports_unknown_without_inventing_limits():
    facts = execution_capability_facts(budget=None, registrations={})
    assert facts["admission"] == "not_evaluated"
    assert facts["whole_task_support"] == "not_inferred"
    assert all(not item["configured"] and item["limits"] == [] for item in facts["capabilities"])
    assert all(item["implementation_status"] == "not_installed" for item in facts["capabilities"])
    assert facts["task_total_budget"]["provenance"] == "not_supplied"


def test_installed_units_do_not_imply_verified_measurements_or_unbounded_continuation():
    budget, registrations = configured_runtime()
    facts = execution_capability_facts(budget=budget, registrations=registrations)
    pcm = by_purpose(facts, "pcm_append")
    mix = by_purpose(facts, "final_audio_mix")
    picture = by_purpose(facts, "picture_render")
    assert pcm["configured"] and pcm["implementation_status"] == "installed"
    assert "persisted_append_receipt" in pcm["continuation"]
    assert mix["continuation"] == [] and mix["gaps"]
    assert picture["continuation"] == ["serial_work_inside_one_grant"]
    assert all(item["verification_status"] == "unknown" and item["evidence_ref"] is None
               and item["verified_input_range"] is None for item in facts["capabilities"])
    assert by_purpose(facts, "quality_check")["implementation_status"] == "not_installed"


def test_budget_without_owner_does_not_claim_installed_execution():
    budget, _ = configured_runtime()
    facts = execution_capability_facts(budget=budget, registrations={})
    audio = by_purpose(facts, "final_audio_mix")
    assert audio["configured"]
    assert audio["implementation_status"] == "not_installed"
    assert audio["continuation"] == []


def test_only_whitelisted_numeric_facts_are_public_and_legacy_scope_is_explicit():
    budget, registrations = configured_runtime()
    facts = execution_capability_facts(budget=budget, registrations=registrations)
    limits = {item["name"]: item for item in by_purpose(facts, "narration_segment")["limits"]}
    assert limits["max_timeout_seconds"] == {
        "name": "max_timeout_seconds", "value": 300, "unit": "seconds", "scope": "legacy_parent_ceiling"}
    assert limits["max_text_chars"]["scope"] == "legacy_stage"
    assert limits["max_process_rss_bytes"]["scope"] == "safety"
    assert "/private/credential.json" not in json.dumps(facts)
    single = replace(budget, narration=replace(budget.narration, input_scope="single_call_experiment_v1"))
    projected = execution_capability_facts(budget=single, registrations=registrations)
    fields = {item["name"]: item for item in by_purpose(projected, "narration_segment")["limits"]}
    assert fields["max_text_chars"]["scope"] == "unit"
    assert fields["max_timeout_seconds"]["scope"] == "legacy_parent_ceiling"
    assert projected["task_total_budget"]["limits"] == []
    assert projected["task_total_budget"]["verification_status"] == "unknown"


def test_quality_paths_and_worker_hash_are_not_returned():
    budget, registrations = configured_runtime()
    private = SimpleNamespace(max_input_bytes=10, max_report_bytes=20,
                              ffmpeg_path="/private/ffmpeg", worker_sha256="secret-worker")
    budget = replace(budget, quality=private)
    registrations["quality_check"] = OwnerRegistration(
        SimpleNamespace(verify_submission=forbidden), SimpleNamespace(execute=forbidden))
    facts = execution_capability_facts(budget=budget, registrations=registrations)
    quality = by_purpose(facts, "quality_check")
    assert quality["implementation_status"] == "installed" and quality["configured"]
    assert {limit["name"] for limit in quality["limits"]} == {"max_report_bytes"}
    assert {limit["name"] for limit in quality["legacy_configuration_limits"]} == {"max_input_bytes"}
    assert "/private/ffmpeg" not in json.dumps(facts)
    assert "secret-worker" not in json.dumps(facts)


def test_production_registration_and_automation_owner_keep_only_interface_facts():
    from app.automation_runtime import restrict_automation_registration
    from app.member_permits import creation_job_registration

    budget, _ = configured_runtime()
    registration = creation_job_registration(
        object(), render_action=forbidden, render_is_valid=forbidden,
        narration_budget=budget.narration, narration_audio_budget=budget.cpu_audio,
        narration_stage=forbidden, audio_mix_facts=forbidden)
    registration.owner.segments = SimpleNamespace(next_initial=forbidden)
    wrapped = restrict_automation_registration(registration)
    facts = execution_capability_facts(budget=budget, registrations={"creation_job": wrapped})
    for purpose in ("narration_segment", "pcm_append", "final_audio_mix", "picture_render"):
        capability = by_purpose(facts, purpose)
        assert capability["implementation_status"] == "installed"
        assert capability["implementation_basis"] == "registered_owner_executor_interfaces"
        assert capability["verification_status"] == "unknown"
    assert facts["whole_task_support"] == "not_inferred"
    assert facts["task_total_budget"]["provenance"] == "not_supplied"
    assert facts["task_total_budget"]["limits"] == []


def test_pcm_append_limits_name_the_accumulated_file_and_prefix_cost():
    budget, registrations = configured_runtime()
    facts = execution_capability_facts(budget=budget, registrations=registrations)
    pcm = {item['name']: item for item in by_purpose(facts, 'pcm_append')['limits']}
    mix = {item['name']: item for item in by_purpose(facts, 'final_audio_mix')['limits']}
    assert pcm['max_output_bytes']['value'] == mix['max_output_bytes']['value']
    assert pcm['max_output_bytes']['scope'] == 'artifact_total'
    assert pcm['max_output_bytes']['subject'] == 'assembled_wav_file'
    assert pcm['max_output_bytes']['accounting'] == 'absolute_size_including_existing_pcm_and_wav_header'
    assert pcm['max_input_count']['accounting'] == 'new_sources_plus_existing_prefix'
    assert pcm['max_input_count']['existing_prefix_slots'] == 1
    assert pcm['max_input_bytes']['accounting'] == 'new_source_files_plus_existing_pcm_and_prefix_wav_header'
    assert mix['max_output_bytes']['scope'] == 'unit'
    assert 'existing_prefix_slots' not in mix['max_input_count']


def test_quality_stream_projection_matches_real_preparation_without_inventing_media_caps():
    from app.quality_worker_runtime import QualityBudget, QUALITY_STREAM_PROTOCOL, quality_stream_resources
    quality = QualityBudget(max_input_bytes=10, max_pcm_bytes=20, max_frame_json_bytes=30,
        max_report_bytes=1024, max_stderr_bytes=1024, max_timeout_seconds=60, decoder_threads=1,
        max_process_rss_bytes=1048576, max_process_threads=4, runtime_sample_seconds=.1,
        additional_memory_bytes=1048576, additional_disk_bytes=1048576, max_tool_bytes=1024,
        ffmpeg_path='/private/ffmpeg', ffprobe_path='/private/ffprobe',
        ffmpeg_sha256='a'*64, ffprobe_sha256='b'*64, worker_sha256='c'*64)
    budget, registrations = configured_runtime()
    registrations['quality_check'] = OwnerRegistration(
        SimpleNamespace(verify_submission=forbidden), SimpleNamespace(execute=forbidden))
    facts = execution_capability_facts(budget=replace(budget, quality=quality), registrations=registrations)
    projected = by_purpose(facts, 'quality_check')
    # Pure demand calculation only; this source is never opened or executed.
    resources = quality_stream_resources({'budget':quality.__dict__,
        'source':{'render_ref':'source-digest','size_bytes':1000000}}, 'metadata', {})
    assert projected['protocol'] == QUALITY_STREAM_PROTOCOL
    assert projected['read_mode'] == resources['read_mode'] == 'stream'
    assert projected['workload_provenance'] == resources['workload_provenance']
    legacy = {'max_input_bytes','max_pcm_bytes','max_frame_json_bytes'}
    assert not legacy.intersection(item['name'] for item in projected['limits'])
    assert {item['name'] for item in projected['legacy_configuration_limits']} == legacy
    assert all(item['scope'] == 'legacy_buffered_request' for item in projected['legacy_configuration_limits'])
    timeout = next(item for item in projected['limits'] if item['name'] == 'max_timeout_seconds')
    assert timeout['scope'] == 'preparation_attempt' and timeout['subject'] == 'stream_phase_deadline'
    assert projected['verification_status'] == 'unknown' and projected['verified_input_range'] is None
    assert projected['applies_to'] == 'new_requests' and projected['gaps']
    assert facts['whole_task_support'] == 'not_inferred'
    public = json.dumps(projected)
    assert '/private/' not in public and quality.worker_sha256 not in public
    assert 'source-digest' not in public
