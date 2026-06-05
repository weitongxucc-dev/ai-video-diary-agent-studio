# Video Retake Dedup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local Whisper-driven second editing pass that automatically removes high-confidence retakes, preserves old files, reports every decision, and regenerates today's Jianying draft from the deduplicated video.

**Architecture:** Keep the existing long-silence processor unchanged as the first pass. Add a focused `RetakeDetector`, a `RetakeVideoRefiner` that performs the second ffmpeg pass, and a `VideoProcessingPipeline` used by HTTP endpoints and the automation worker. Persist the generated report path on the Episode and expose a small report endpoint for the video page.

**Tech Stack:** Python 3.12, FastAPI, dataclasses, `difflib.SequenceMatcher`, ffmpeg, local Whisper, pytest, Next.js 15, TypeScript, React.

---

## Repository Note

This workspace does not contain `.git` metadata. The commit steps normally required between tasks are replaced with explicit verification checkpoints. Do not initialize a repository or delete old media files.

## File Map

Create:

- `apps/api/app/retakes.py`: subtitle-window detection, report dataclasses, report JSON serialization.
- `apps/api/app/video_pipeline.py`: local Whisper scan, report writing, second ffmpeg pass, first-pass/second-pass orchestration.
- `apps/api/tests/test_retakes.py`: detector regression tests, including today's observed retakes.
- `apps/api/tests/test_video_pipeline_dedup.py`: refiner and orchestration tests.

Modify:

- `apps/api/app/video.py`: add `dedup_report_path` to `ProcessedVideo`.
- `apps/api/app/models.py`: persist the Episode report path.
- `apps/api/app/repository.py`: serialize and restore the Episode report path.
- `apps/api/app/services.py`: bind the report path from the final processed video.
- `apps/api/app/schemas.py`: expose the path and report response models.
- `apps/api/app/main.py`: construct the shared pipeline, use it from all endpoints, expose report JSON.
- `apps/api/app/automation.py`: use the shared pipeline in the unattended worker.
- `apps/api/tests/test_episode_flow.py`: prove the HTTP path stores and exposes the report.
- `apps/api/tests/test_automation_worker.py`: prove the worker uses the pipeline.
- `apps/web/src/lib/types.ts`: add report types and Episode report path.
- `apps/web/src/lib/api.ts`: fetch an Episode retake report.
- `apps/web/src/app/video/page.tsx`: show automatic removals and review-only candidates.
- `apps/web/src/app/globals.css`: style the report summary without introducing nested cards.
- `README.md`: document automatic high-confidence retake removal.

### Task 1: Build The Pure Retake Detector

**Files:**

- Create: `apps/api/app/retakes.py`
- Create: `apps/api/tests/test_retakes.py`

- [ ] **Step 1: Write failing detector tests**

Add tests with explicit subtitle timing:

```python
from app.asr import SubtitleSegment
from app.retakes import RetakeDetector


def test_detector_auto_removes_interrupted_take_before_complete_take():
    report = RetakeDetector().detect(
        [
            SubtitleSegment(610_000, 614_300, "最后的一句话呢 GEO 的本质不是让 AI 去"),
            SubtitleSegment(614_300, 618_500, "最后一句话就是 GEO 的本质不是骗 AI 去提到"),
            SubtitleSegment(618_500, 627_900, "而是用真实的经验清晰的定位专业的内容去告诉模型"),
        ]
    )

    assert [(item.remove.start_ms, item.remove.end_ms) for item in report.auto_removed] == [
        (610_000, 614_300)
    ]
    assert report.auto_removed[0].keep.start_ms == 614_300


def test_detector_collapses_three_intro_takes_and_keeps_last_complete_version():
    report = RetakeDetector().detect(
        [
            SubtitleSegment(13_800, 15_800, "我今天刚看到一篇论文"),
            SubtitleSegment(15_800, 21_200, "标题就叫深城市引擎搜索优化如果主导人工智能的搜索"),
            SubtitleSegment(21_200, 22_600, "今天我刚看到一篇论文"),
            SubtitleSegment(22_600, 24_600, "标题叫深城市引擎搜"),
            SubtitleSegment(24_600, 27_000, "今天我刚看到一篇论文"),
            SubtitleSegment(27_000, 31_600, "标题叫深城市引擎优化如何主导人工智能的搜索"),
        ]
    )

    assert [(item.remove.start_ms, item.remove.end_ms) for item in report.auto_removed] == [
        (13_800, 24_600)
    ]
    assert report.auto_removed[0].keep.start_ms == 24_600


def test_detector_keeps_small_self_correction_for_review():
    report = RetakeDetector().detect(
        [
            SubtitleSegment(315_500, 317_000, "真正值得花钱的呢"),
            SubtitleSegment(317_000, 320_600, "我觉得是需要有真正值得花钱的"),
        ]
    )

    assert report.auto_removed == []
    assert len(report.review_only) == 1
```

- [ ] **Step 2: Run tests and confirm the module is missing**

Run:

```bash
cd apps/api && .venv/bin/pytest tests/test_retakes.py -q
```

Expected: failure because `app.retakes` does not exist.

- [ ] **Step 3: Implement report types and conservative matching**

Add immutable dataclasses and one detector:

```python
@dataclass(frozen=True)
class TimeRange:
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class RetakeCandidate:
    remove: TimeRange
    keep: TimeRange
    confidence: float
    reason: str


@dataclass(frozen=True)
class RetakeDetection:
    auto_removed: list[RetakeCandidate]
    review_only: list[RetakeCandidate]
```

Implement `RetakeDetector.detect(segments)` with centralized constants:

```python
MIN_EFFECTIVE_CHARS = 8
MAX_GAP_MS = 30_000
MAX_WINDOW_SEGMENTS = 3
AUTO_SIMILARITY = 0.84
ABORTED_AUTO_SIMILARITY = 0.76
REVIEW_SIMILARITY = 0.62
```

Build windows of one to three consecutive subtitle segments, normalize punctuation and whitespace, compare non-overlapping nearby windows with `SequenceMatcher`, then:

- classify as `auto_remove` when similarity is at least `AUTO_SIMILARITY`;
- also classify as `auto_remove` when the first take touches the second take, similarity is at least `ABORTED_AUTO_SIMILARITY`, and the second text is not shorter;
- classify weaker nearby overlap as `review_only`;
- merge touching automatic removals that point to overlapping retained windows, so three intro attempts become one removal ending at the final take start;
- sort results by removal start time.

Add `retake_detection_payload()` so JSON fields use seconds rounded to three decimals.

- [ ] **Step 4: Run detector tests**

Run:

```bash
cd apps/api && .venv/bin/pytest tests/test_retakes.py -q
```

Expected: all detector tests pass.

### Task 2: Add The Local Whisper Refinement Pass

**Files:**

- Create: `apps/api/app/video_pipeline.py`
- Create: `apps/api/tests/test_video_pipeline_dedup.py`
- Modify: `apps/api/app/video.py`

- [ ] **Step 1: Add failing refiner tests**

Use a fake configured transcriber, fake duration reader, and runner that writes command outputs. Assert:

```python
refined = refiner.refine(first_pass_video)

assert refined.file_name == "talk_preview_dedup.mp4"
assert refined.output_path == str(output / "talk_preview_dedup.mp4")
assert refined.edit_plan_path == str(output / "talk_preview_dedup.edit_plan.json")
assert refined.dedup_report_path == str(output / "talk_preview_dedup_report.json")
assert "concat=n=2:v=1:a=1" in " ".join(calls[-1])
```

Add separate tests proving:

- a missing local Whisper configuration returns the original first-pass video and writes `status: "skipped"`;
- no high-confidence candidates returns the original video and writes `status: "no_changes"`;
- `VideoProcessingPipeline.process_batch()` calls merge/silence processing before refinement.

- [ ] **Step 2: Run focused tests and confirm failure**

Run:

```bash
cd apps/api && .venv/bin/pytest tests/test_video_pipeline_dedup.py -q
```

Expected: failure because `app.video_pipeline` and `ProcessedVideo.dedup_report_path` do not exist.

- [ ] **Step 3: Extend `ProcessedVideo`**

Add an optional field:

```python
@dataclass(frozen=True)
class ProcessedVideo:
    file_name: str
    output_path: str
    source_file_name: str
    edit_plan_path: str | None = None
    source_file_names: list[str] = field(default_factory=list)
    dedup_report_path: str | None = None
```

- [ ] **Step 4: Implement `RetakeVideoRefiner`**

Construct it with:

```python
RetakeVideoRefiner(
    output_path: str | Path,
    transcriber: ASRTranscriber,
    detector: RetakeDetector | None = None,
    runner: Callable[[list[str]], None] | None = None,
    duration_reader: Callable[[Path], float] | None = None,
)
```

Implement `refine(video: ProcessedVideo) -> ProcessedVideo`:

1. Create `*_dedup_report.json` beside the first-pass video.
2. If `transcriber.configured` is false, write `status: "skipped"` and return the original video with `dedup_report_path`.
3. Extract temporary 16 kHz mono WAV audio with ffmpeg.
4. Transcribe and call `RetakeDetector.detect`.
5. Write a report even when there are no automatic removals.
6. Compute complement keep ranges from the automatic removals and probed video duration.
7. Generate `*_dedup.mp4` with ffmpeg trim/atrim/concat filters.
8. Generate `*_dedup.edit_plan.json`.
9. Return a new `ProcessedVideo` preserving source names and pointing at the deduplicated output.

Catch `ASRConfigurationError`, `ASRTranscriptionError`, `OSError`, and `subprocess.CalledProcessError` from the scan stage. For scan failures, write `status: "skipped"` and continue with the first-pass output. Let second-pass ffmpeg failures propagate after writing `status: "failed"`.

- [ ] **Step 5: Implement the pipeline wrapper**

Add:

```python
class VideoProcessingPipeline:
    def __init__(self, processor: FfmpegVideoProcessor, refiner: RetakeVideoRefiner) -> None:
        self.processor = processor
        self.refiner = refiner

    def process(self, file_name: str) -> ProcessedVideo:
        return self.refiner.refine(self.processor.process(file_name))

    def process_batch(self, file_names: list[str]) -> ProcessedVideo:
        return self.refiner.refine(self.processor.process_batch(file_names))
```

Add a `video_processing_pipeline_from_settings(settings)` factory that uses `LocalWhisperTranscriber(settings)` for the first scan. Final draft subtitle generation continues to use the existing fallback ASR transcriber.

- [ ] **Step 6: Run focused and existing video tests**

Run:

```bash
cd apps/api && .venv/bin/pytest tests/test_retakes.py tests/test_video_pipeline.py tests/test_video_pipeline_dedup.py -q
```

Expected: all tests pass.

### Task 3: Persist And Expose The Report

**Files:**

- Modify: `apps/api/app/models.py`
- Modify: `apps/api/app/repository.py`
- Modify: `apps/api/app/services.py`
- Modify: `apps/api/app/schemas.py`
- Modify: `apps/api/app/main.py`
- Modify: `apps/api/tests/test_episode_flow.py`

- [ ] **Step 1: Add failing persistence and endpoint assertions**

Extend the Episode flow fixture with a processed video carrying `dedup_report_path`. Assert:

```python
assert body["video_dedup_report_path"].endswith("raw_preview_dedup_report.json")

report_response = client.get(f"/api/episodes/{episode['id']}/retake-report")
assert report_response.status_code == 200
assert report_response.json()["status"] == "applied"
```

Add a missing-report test expecting HTTP `404`.

- [ ] **Step 2: Run the endpoint regression test**

Run:

```bash
cd apps/api && .venv/bin/pytest tests/test_episode_flow.py -q
```

Expected: failure because Episode has no report path and the report endpoint is missing.

- [ ] **Step 3: Persist the path**

Add:

```python
video_dedup_report_path: str | None = None
```

to `Episode`. Serialize it in `_episode_to_dict()`, restore it defensively in `_episode_from_dict()`, expose it in `EpisodeResponse`, and bind it in `EpisodeService.bind_processed_video()`:

```python
episode.video_dedup_report_path = video.dedup_report_path
```

- [ ] **Step 4: Add report response schemas and endpoint**

Add response models for:

```python
class RetakeRangeResponse(BaseModel):
    start: float
    end: float


class RetakeCandidateResponse(BaseModel):
    remove: RetakeRangeResponse
    keep: RetakeRangeResponse
    confidence: float
    reason: str


class RetakeReportResponse(BaseModel):
    status: Literal["applied", "no_changes", "skipped", "failed"]
    source_video: str
    output_video: str | None
    auto_removed: list[RetakeCandidateResponse]
    review_only: list[RetakeCandidateResponse]
    message: str | None = None
```

Expose:

```python
@app.get("/api/episodes/{episode_id}/retake-report", response_model=RetakeReportResponse)
def get_episode_retake_report(episode_id: str) -> RetakeReportResponse:
    episode = get_episode_or_404(episode_id)
    if not episode.video_dedup_report_path:
        raise HTTPException(status_code=404, detail="retake report not found")
    report_path = Path(episode.video_dedup_report_path)
    if not report_path.is_file():
        raise HTTPException(status_code=404, detail="retake report not found")
    return RetakeReportResponse.model_validate_json(report_path.read_text(encoding="utf-8"))
```

- [ ] **Step 5: Run Episode flow tests**

Run:

```bash
cd apps/api && .venv/bin/pytest tests/test_episode_flow.py -q
```

Expected: all Episode flow tests pass.

### Task 4: Route Every Processing Entry Through One Pipeline

**Files:**

- Modify: `apps/api/app/main.py`
- Modify: `apps/api/app/automation.py`
- Modify: `apps/api/tests/test_episode_flow.py`
- Modify: `apps/api/tests/test_automation_worker.py`

- [ ] **Step 1: Add failing integration assertions**

Use a capturing fake pipeline with `process()` and `process_batch()` in API tests. Assert the standalone, planned, ad hoc single, and ad hoc batch routes call it. In the worker test, inject the same interface and assert the worker calls `process("raw.mov")`.

```python
class CapturingVideoPipeline:
    def __init__(self, processed):
        self.processed = processed
        self.single_calls = []
        self.batch_calls = []

    def process(self, file_name):
        self.single_calls.append(file_name)
        return self.processed

    def process_batch(self, file_names):
        self.batch_calls.append(file_names)
        return self.processed


pipeline = CapturingVideoPipeline(
    ProcessedVideo(
        file_name="raw_preview.mp4",
        output_path=str(processed / "raw_preview.mp4"),
        source_file_name="raw.mov",
        source_file_names=["raw.mov"],
    )
)
worker = AgentAutomationWorker(
    repo=repo,
    workflow=LangGraphEpisodeWorkflow(_service()),
    video_inbox=VideoInbox(inbox),
    video_processor=pipeline,
    draft_workspace=DraftWorkspace(settings, runner=runner),
)

worker.process_pending_videos_once()

assert pipeline.single_calls == ["raw.mov"]
```

- [ ] **Step 2: Run integration tests and confirm direct processor usage**

Run:

```bash
cd apps/api && .venv/bin/pytest tests/test_episode_flow.py tests/test_automation_worker.py -q
```

Expected: new assertions fail because routes and worker still call `FfmpegVideoProcessor` directly.

- [ ] **Step 3: Wire the shared pipeline**

In `main.py`, construct:

```python
video_processor = FfmpegVideoProcessor(settings)
video_pipeline = video_processing_pipeline_from_settings(settings, processor=video_processor)
```

Recreate both values in `_reload_runtime_dependencies()`. Replace processing calls with `video_pipeline.process()` or `video_pipeline.process_batch()`.

In `automation.py`, accept a processing object with `process()` and `process_batch()` methods, then build the worker with `video_processing_pipeline_from_settings(settings)`.

- [ ] **Step 4: Run integration and full API tests**

Run:

```bash
cd apps/api && .venv/bin/pytest tests/test_episode_flow.py tests/test_automation_worker.py -q
cd apps/api && .venv/bin/pytest -q
```

Expected: all API tests pass.

### Task 5: Show The Report On The Video Page

**Files:**

- Modify: `apps/web/src/lib/types.ts`
- Modify: `apps/web/src/lib/api.ts`
- Modify: `apps/web/src/app/video/page.tsx`
- Modify: `apps/web/src/app/globals.css`
- Modify: `README.md`

- [ ] **Step 1: Add TypeScript report types**

Add:

```ts
export type RetakeCandidate = {
  remove: { start: number; end: number };
  keep: { start: number; end: number };
  confidence: number;
  reason: string;
};

export type RetakeReport = {
  status: "applied" | "no_changes" | "skipped" | "failed";
  source_video: string;
  output_video: string | null;
  auto_removed: RetakeCandidate[];
  review_only: RetakeCandidate[];
  message: string | null;
};
```

Add `video_dedup_report_path: string | null` to `Episode`.

- [ ] **Step 2: Fetch report data when available**

Add `getEpisodeRetakeReport(episodeId)` in `apps/web/src/lib/api.ts`. In `loadVideoState()`, fetch the report only when the Episode has `video_dedup_report_path`; treat a report fetch failure as `null` so the video page still loads.

- [ ] **Step 3: Render an unframed report section**

Below `ProcessingRefresh`, render:

```tsx
{retakeReport ? (
  <section className="retake-report" aria-label="重复口播检查">
    <div className="retake-report-heading">
      <strong>重复口播检查</strong>
      <span>已自动剪掉 {retakeReport.auto_removed.length} 段</span>
    </div>
    {retakeReport.auto_removed.map((item) => (
      <div className="retake-report-row" key={`${item.remove.start}-${item.remove.end}`}>
        <span>剪掉 {formatTimeline(item.remove.start)} - {formatTimeline(item.remove.end)}</span>
        <span>保留 {formatTimeline(item.keep.start)} - {formatTimeline(item.keep.end)}</span>
        <strong>{Math.round(item.confidence * 100)}%</strong>
      </div>
    ))}
    {retakeReport.review_only.length > 0 ? (
      <>
        <p>另有 {retakeReport.review_only.length} 段需要人工复核，未自动删除。</p>
        {retakeReport.review_only.map((item) => (
          <div className="retake-report-row review" key={`review-${item.remove.start}-${item.remove.end}`}>
            <span>复核 {formatTimeline(item.remove.start)} - {formatTimeline(item.remove.end)}</span>
            <span>{item.reason}</span>
            <strong>{Math.round(item.confidence * 100)}%</strong>
          </div>
        ))}
      </>
    ) : null}
  </section>
) : null}
```

Use compact rows and existing palette colors. Do not add a card inside `.video-main`.

- [ ] **Step 4: Update documentation**

Add a README Phase 1 bullet explaining that local Whisper now drives high-confidence retake removal, reports all decisions, preserves prior videos, and regenerates final subtitles after the second pass.

- [ ] **Step 5: Run frontend verification**

Run:

```bash
corepack pnpm --dir apps/web lint
corepack pnpm --dir apps/web build
node apps/web/scripts/workflow-contract-check.mjs
node apps/web/scripts/pixel-style-contract-check.mjs
```

Expected: all commands pass.

### Task 6: Reprocess Today's Video And Import The New Draft

**Files:**

- Read: `/Users/Zhuanz/Movies/AI视频日记/01_待处理视频/VID_20260601_132319.mp4`
- Create through the application: `/Users/Zhuanz/Movies/AI视频日记/06_待发布成片/VID_20260601_132319_preview_dedup.mp4`
- Create through the application: `/Users/Zhuanz/Movies/AI视频日记/06_待发布成片/VID_20260601_132319_preview_dedup_report.json`
- Create through the application: a new package under `/Users/Zhuanz/Movies/AI视频日记/05_剪映草稿`
- Create through the application: a new imported draft under `/Users/Zhuanz/Movies/JianyingPro/User Data/Projects/com.lveditor.draft`

- [ ] **Step 1: Start or confirm local services**

Run:

```bash
corepack pnpm dev:api
corepack pnpm --dir apps/web dev
```

Expected: API responds at `http://127.0.0.1:8000`, web responds at `http://localhost:3000`.

- [ ] **Step 2: Trigger background reprocessing**

Fetch today's Episode, then submit the existing source video:

```bash
EPISODE_ID="$(curl -s http://127.0.0.1:8000/api/episodes/today | apps/api/.venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
curl -s -X POST "http://127.0.0.1:8000/api/episodes/$EPISODE_ID/process-ad-hoc-video" \
  -H 'Content-Type: application/json' \
  -d '{"file_name":"VID_20260601_132319.mp4"}'
```

Use the concrete Episode id returned by the first command. Poll `/api/episodes/today` until status becomes `draft_ready`.

- [ ] **Step 3: Verify the report**

Run:

```bash
curl -s "http://127.0.0.1:8000/api/episodes/$EPISODE_ID/retake-report"
```

Expected:

- `status` is `applied`;
- `output_video` ends with `_dedup.mp4`;
- an automatic removal covers the intro retake before `00:24.6`;
- an automatic removal covers the interrupted outro ending at `06:14.3`;
- the light correction around `05:15.5` is not automatically removed.

- [ ] **Step 4: Import the newest package**

Read the new `draft_package_name` from `/api/episodes/today`, then run:

```bash
PACKAGE_NAME="$(curl -s http://127.0.0.1:8000/api/episodes/today | apps/api/.venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["draft_package_name"])')"
curl -s -X POST http://127.0.0.1:8000/api/drafts/import \
  -H 'Content-Type: application/json' \
  -d "{\"package_name\":\"$PACKAGE_NAME\"}"
```

Expected: response status is `imported`, with a new Jianying draft path. Existing packages and drafts remain present.

- [ ] **Step 5: Verify final media and UI**

Run:

```bash
ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 \
  "/Users/Zhuanz/Movies/AI视频日记/06_待发布成片/VID_20260601_132319_preview_dedup.mp4"
curl -I http://localhost:3000/video
```

Open `http://localhost:3000/video` in the in-app Browser and confirm:

- the page renders without a server-side exception;
- the repeat-check section lists the automatically removed clips;
- the old processed video still exists;
- the new Jianying draft contains one video track and an imported subtitle track.

### Task 7: Final Verification

- [ ] **Step 1: Run the complete API suite**

Run:

```bash
cd apps/api && .venv/bin/pytest -q
```

Expected: all tests pass.

- [ ] **Step 2: Run the complete frontend suite**

Run:

```bash
corepack pnpm --dir apps/web lint
corepack pnpm --dir apps/web build
node apps/web/scripts/workflow-contract-check.mjs
node apps/web/scripts/pixel-style-contract-check.mjs
```

Expected: all commands pass.

- [ ] **Step 3: Record completion**

Report:

- final API and frontend verification results;
- generated `_dedup.mp4`, report, package, and Jianying draft paths;
- automatic removal ranges and review-only ranges;
- whether any manual review remains.
