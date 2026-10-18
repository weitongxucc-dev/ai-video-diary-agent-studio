from app.creation.intent_use_case import CreationIntentUseCase
import pytest


def test_frozen_delivery_keeps_explicit_voice_clear_and_independent_versions():
    shared = {"language": "zh-CN", "selected_voice": {"profile_id": "shared", "version": "1"},
        "selected_person": None, "duration_target_seconds": 60}
    frozen = {"goal": "产品介绍", "name": "English", "language": "en-US", "aspect_ratio": "16:9",
        "duration_target_seconds": 30, "presenter_share_target": 0, "lipsync_quality": "high",
        "selected_voice": None, "selected_person": {"profile_id": "presenter", "version": "4", "other": "fact"}}
    result = CreationIntentUseCase.frozen_delivery_inputs(shared, frozen)
    assert result["selected_voice"] is None
    assert result["selected_person"] == {"profile_id": "presenter", "version": "4"}
    assert result["language"] == "en-US" and result["target_count"] == 1
    assert shared["selected_voice"] == {"profile_id": "shared", "version": "1"}


@pytest.mark.parametrize("message", ["先讨论怎么制作视频", "只写文案，不要开始", "先不制作，看看方案"])
def test_discussion_input_cannot_be_relabelled_as_delegation(message):
    with pytest.raises(ValueError, match="讨论"):
        CreationIntentUseCase.receive_intent({"kind": "goal_delegation"}, source={"member_id": "member"},
            target_count=1, original_message=message)


def test_delegation_keeps_a_prohibition_on_new_narration_without_rejecting_video_goal():
    intent = CreationIntentUseCase.receive_intent({"kind": "goal_delegation"}, source={"member_id": "member"},
        target_count=1, original_message="制作1条视频，不要生成新配音")
    assert intent["kind"] == "goal_delegation"


@pytest.mark.parametrize("message", [
    "你帮我剪一个30秒的，关于我们工厂的脱壳机的介绍视频，要求是中英文版本各一条",
    "Please make a thirty second introduction video about the factory machine, one Chinese and one English version.",
])
def test_typed_delegation_does_not_depend_on_verb_word_distance_or_chinese_only_matching(message):
    intent = CreationIntentUseCase.receive_intent({"kind": "goal_delegation"}, source={"member_id": "member"},
        target_count=2, original_message=message)
    assert intent["kind"] == "goal_delegation"


def test_final_copy_is_not_rewritten_and_system_adoption_needs_saved_authority():
    with pytest.raises(ValueError, match="实际用户输入"):
        CreationIntentUseCase.receive_intent({"kind": "final_script", "final_script": {"title": "正文", "text": "模型改写"}},
            source={"member_id": "member"}, target_count=1, original_message="开始制作，定稿：用户原文")
    with pytest.raises(ValueError, match="明确制作委托"):
        CreationIntentUseCase.adoption(None, kind="delegated_system", member_id="member", version="version")
    with pytest.raises(ValueError, match="可信成员"):
        CreationIntentUseCase.adoption({"kind": "goal_delegation", "source": {"member_id": "another"}},
            kind="delegated_system", member_id="member", version="version")
