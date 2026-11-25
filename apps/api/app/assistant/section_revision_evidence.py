"""Read only the fixed page citations selected in the consumed task input."""
import re

from app.creation.intent_use_case import CreationIntentUseCase


def selected_knowledge_pages(context):
    references = list((context.get("target_snapshot") or {}).get("selected_references", []))
    if context.get("knowledge_reference"):
        references.append({"kind": "knowledge", **context["knowledge_reference"]})
    identities = dict.fromkeys((item["knowledge_item_id"], item["version"], item["locator"])
        for item in references if item["kind"] == "knowledge" and item.get("locator"))
    return [{"object_id": item_id, "version_or_identity": version, "locator": locator}
            for item_id, version, locator in identities]


def allows_linked_adoption(origin):
    from app.creation.editing.edit_scope import edit_scope_blocker
    message = origin["raw_command"]
    try:
        CreationIntentUseCase.reject_discussion_only_input(message)
    except ValueError:
        return False
    if edit_scope_blocker(origin, linked=True) or re.search(
        r"(?:不要|先别|先不|暂不|不)(?:采用|应用|覆盖(?:原稿|原版)?)"
        r"|(?:先|只|仅)(?:给我|要|做)?(?:看|预览|提案|给方案|出方案)"
        r"|\b(?:preview|proposal)\s+only\b|\b(?:do not|don't)\s+apply\b",
        message, re.IGNORECASE,
    ):
        return False
    return bool(re.search(
        r"(?:^|[，。；,;\n])\s*(?:请|现在|就)?\s*(?:直接|正式)?(?:采用|应用|保存这次修订)"
        r"|(?:请|把|将|用|根据|按照|按)[^，。！？；?]{0,40}(?:改成|改为|直接修改|修订第|改写第|更新第)"
        r"|(?:^|[,.!;\n])\s*(?:please\s+)?(?:apply|adopt)\b",
        message, re.IGNORECASE,
    ))
