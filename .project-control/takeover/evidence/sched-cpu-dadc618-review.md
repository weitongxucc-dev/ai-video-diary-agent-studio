# SCHED CPU render admission `dadc618` independent review

Candidate: `dadc6188ee62bd7546dda3ab9b1c18ccd88e90f4`, parent `fb8ef24`. Verdict: **PASS for the bounded resource-probe component**, with production calibration and consumer trust still pending.

`HostBudget.cpu_render` defaults to `None`, so `creation_render` is unknown without an explicit device budget. Admission requires `execution_mode='cpu_ffmpeg'`, complete finite duration/timeout and integer size/clip/overlay/output/thread fields. A value above any configured limit returns busy; missing/unsupported fields or absent samples return unknown. The host samples CPU, memory, swap, disk and I/O before admission and continuation. `DeviceScheduler.submit` persists resources returned by its trusted owner verifier, not fields supplied directly in a request. The process tree remains separately checked before grant release; this CPU path has no resident model/GPU claim.

Focused candidate tests: **3 passed** (default unknown, bounded admission/rejection, simulated pressure and isolated real FFmpeg grant). The tests use a synthetic budget and short media; they do not establish a calibrated target-device budget, truthful production `input_bytes`/duration/output facts, source/output disk coverage, or five-user responsiveness. No main-path integration or product acceptance is inferred. No product source was edited.
