from app.asr import SubtitleSegment
from app.retakes import (
    RetakeCandidate,
    RetakeDetection,
    RetakeDetector,
    TimeRange,
    retake_detection_payload,
)


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
    assert report.auto_removed[0].keep.end_ms == 627_900


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
    assert report.auto_removed[0].keep.end_ms == 31_600


def test_detector_keeps_small_self_correction_for_review():
    report = RetakeDetector().detect(
        [
            SubtitleSegment(315_500, 317_000, "真正值得花钱的呢"),
            SubtitleSegment(317_000, 320_600, "我觉得是需要有真正值得花钱的"),
        ]
    )

    assert report.auto_removed == []
    assert len(report.review_only) == 1


def test_detector_does_not_auto_remove_distant_similar_expression():
    report = RetakeDetector().detect(
        [
            SubtitleSegment(10_000, 13_000, "搜索优化需要真实经验和专业内容"),
            SubtitleSegment(80_000, 83_000, "搜索优化需要真实经验和专业内容"),
        ]
    )

    assert report.auto_removed == []


def test_detector_merges_chain_of_three_segment_windows_into_full_previous_version_remove_range():
    report = RetakeDetector().detect(
        [
            SubtitleSegment(0, 1_000, "第一段我们先把核心结论说清楚"),
            SubtitleSegment(1_000, 2_000, "第二段继续把原因展开一点"),
            SubtitleSegment(2_000, 3_000, "第三段补一个具体例子"),
            SubtitleSegment(3_000, 4_000, "第四段把这一版收完整"),
            SubtitleSegment(4_000, 5_000, "第一段我们先把核心结论说清楚"),
            SubtitleSegment(5_000, 6_000, "第二段继续把原因展开一点"),
            SubtitleSegment(6_000, 7_000, "第三段补一个具体例子"),
            SubtitleSegment(7_000, 8_000, "第四段把这一版收完整"),
        ]
    )

    assert [(item.remove.start_ms, item.remove.end_ms) for item in report.auto_removed] == [
        (0, 4_000)
    ]


def test_detector_keeps_only_two_followup_segments_after_keep_window_at_real_srt_granularity():
    intro_report = RetakeDetector().detect(
        [
            SubtitleSegment(13_800, 15_800, "我今天刚看到一篇论文"),
            SubtitleSegment(15_800, 21_200, "标题就叫深城市引擎搜索优化如果主导人工智能的搜索"),
            SubtitleSegment(21_200, 22_600, "今天我刚看到一篇论文"),
            SubtitleSegment(22_600, 24_600, "标题叫深城市引擎搜"),
            SubtitleSegment(24_600, 27_000, "今天我刚看到一篇论文"),
            SubtitleSegment(27_000, 29_400, "标题叫深城市引擎优化如何主导"),
            SubtitleSegment(29_400, 31_600, "人工智能的搜索"),
            SubtitleSegment(31_600, 34_000, "然后我还想补一句新的内容"),
        ]
    )

    outro_report = RetakeDetector().detect(
        [
            SubtitleSegment(374_300, 376_500, "最后的一句话呢 GEO 的本质不是让 AI 去"),
            SubtitleSegment(376_500, 378_500, "最后一句话就是 GEO 的本质不是骗 AI 去提到"),
            SubtitleSegment(378_500, 383_500, "而是用真实的经验清晰的定位"),
            SubtitleSegment(383_500, 387_900, "专业的内容去告诉模型"),
            SubtitleSegment(387_900, 390_500, "然后再接一句新的收尾"),
        ]
    )

    assert intro_report.auto_removed[0].keep.end_ms == 31_600
    assert outro_report.auto_removed[0].keep.end_ms == 387_900


def test_retake_detection_payload_uses_seconds_rounded_to_three_decimals():
    payload = retake_detection_payload(
        RetakeDetection(
            auto_removed=[
                RetakeCandidate(
                    remove=TimeRange(13_801, 24_605),
                    keep=TimeRange(24_605, 31_609),
                    confidence=0.87654,
                    reason="repeated_take",
                )
            ],
            review_only=[],
        )
    )

    assert payload == {
        "auto_removed": [
            {
                "remove": {"start": 13.801, "end": 24.605},
                "keep": {"start": 24.605, "end": 31.609},
                "confidence": 0.877,
                "reason": "repeated_take",
            }
        ],
        "review_only": [],
    }
