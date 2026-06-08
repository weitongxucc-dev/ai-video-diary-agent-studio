from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ScriptRewriteIntent:
    id: str
    label: str
    description: str
    prompt_hint: str


_SCRIPT_REWRITE_INTENTS: tuple[ScriptRewriteIntent, ...] = (
    ScriptRewriteIntent(
        id="sharper_angle",
        label="更犀利",
        description="强化冲突和判断，让观点更直接。",
        prompt_hint="把这一段改得更犀利，先给明确判断，再解释原因，减少铺垫。",
    ),
    ScriptRewriteIntent(
        id="more_conversational",
        label="更口语",
        description="减少书面感，更像自然口播。",
        prompt_hint="把这一段改得更口语，像真人边复盘边讲，避免书面腔和营销腔。",
    ),
    ScriptRewriteIntent(
        id="more_real_case",
        label="更真实",
        description="补足真实经历、现场感和具体例子。",
        prompt_hint="把这一段改得更像真实经历，加入具体现场、踩坑或选择依据。",
    ),
    ScriptRewriteIntent(
        id="shorter",
        label="更短",
        description="压缩表达，保留关键判断。",
        prompt_hint="压缩到更短、更紧凑，删掉重复解释，保留关键判断。",
    ),
    ScriptRewriteIntent(
        id="more_review_like",
        label="更像复盘",
        description="强化问题、原因、修正和经验。",
        prompt_hint="把这一段改得更像复盘，讲清问题、原因、修正动作和下次经验。",
    ),
)


def script_rewrite_intents() -> list[dict[str, str]]:
    return [asdict(intent) for intent in _SCRIPT_REWRITE_INTENTS]


def get_script_rewrite_intent(intent_id: str) -> dict[str, str]:
    for intent in _SCRIPT_REWRITE_INTENTS:
        if intent.id == intent_id:
            return asdict(intent)
    raise KeyError(f"unknown script rewrite intent: {intent_id}")


def script_rewrite_reason(
    intent_id: str,
    *,
    section_title: str,
    user_note: str | None = None,
) -> str:
    intent = get_script_rewrite_intent(intent_id)
    reason = f"快捷重写：{section_title} -> {intent['label']}；{intent['prompt_hint']}"
    if user_note and user_note.strip():
        reason = f"{reason}补充要求：{user_note.strip()}"
    return reason
