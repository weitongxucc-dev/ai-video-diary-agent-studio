"""Text evidence labels shared by lexical retrieval and candidate explanations."""
from app.material_speech import speech_text_for_range
from app.visual_understanding import searchable_ocr_text
from app.materials.contracts import MaterialAsset


def segment_text_evidence(asset, segment):
    visual = segment.understanding
    return (
        ("片段描述", segment.description),
        ("本地画面观察", visual.summary if not segment.description and visual and visual.status == "ready" else ""),
        ("原片画面文字", searchable_ocr_text(visual)),
        ("片段转录", segment.transcript or speech_text_for_range(asset.speech_analysis, segment.start_seconds, segment.end_seconds)),
        ("用户说明", asset.user_description if isinstance(asset, MaterialAsset) else ""),
        ("批次背景", asset.upload_context if isinstance(asset, MaterialAsset) else ""),
    )


def segment_searchable_text(asset, segment):
    return " ".join(value.casefold() for value in (
        *(text for _label, text in segment_text_evidence(asset, segment)),
        *segment.system_tags, *segment.user_tags, *(asset.user_tags if isinstance(asset, MaterialAsset) else ()),
    ) if value)
