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
    """Authorize immediate adoption from the consumed member instruction."""
    from app.creation.editing.edit_scope import edit_scope_blocker
    message = origin["raw_command"]
    try:
        CreationIntentUseCase.reject_discussion_only_input(message)
    except ValueError:
        return False
    # An explicit hold or condition takes precedence across the entire input.
    # Pi's interpretation cannot turn a request for confirmation into consent.
    confirmation = r"(?:确认|同意|审阅|审核|批准|授权|过目|点头|核对|核实)"
    if edit_scope_blocker(origin, linked=True) or re.search(
        r"[?？吗]|(?:是否|能否|可否|要不要)"
        r"|(?:不要|先别|先不|暂不|不能|不应|不)\s*(?:直接|立即)?(?:采用|应用|覆盖|保存)"
        r"|(?:先|只|仅)(?:给我|要|做)?(?:看|预览|提案|给方案|出方案)"
        r"|(?:采用|应用|保存|覆盖)(?:之)?前"
        rf"|先[^先，。！？；,!?;\n]*{confirmation}"
        rf"|(?:让|给|由)我\s*{confirmation}"
        rf"|{confirmation}(?:通过)?\s*(?:后|之后|以后|再|才)"
        r"|(?:等(?:待)?|待)\s*(?:我|用户|人工|确认|批准|审核|授权)"
        r"|(?:等一下|先等|稍后|暂缓|晚点)"
        r"|(?:如果|假如|倘若|若|除非|前提|视情况|满足条件)"
        r"|\b(?:preview|proposal)\s+only\b|\b(?:do not|don['’]t)\s+(?:apply|adopt|save)\b"
        r"|\b(?:if|unless|whether|wait|hold off|not yet|before applying|before adopting)\b"
        r"|\b(?:after|once|until|pending|when)\b[^.!?;\n]*\b(?:confirm\w*|approv\w*|review\w*|sign[- ]off)\b"
        r"|\b(?:should|could|would|can)\s+(?:I|we|you)\b",
        message, re.IGNORECASE,
    ):
        return False
    return bool(re.search(
        r"(?:^|[，。；,;\n])\s*(?:请|现在|就)?\s*(?:直接|正式)?(?:采用|应用|保存这次修订)"
        r"|(?:请|把|将|用|根据|按照|按)[^，。！？；?]{0,40}(?:改成|改为|直接修改|修订第|改写第|更新第)"
        r"|(?:^|[,.!;\n])\s*(?:please\s+)?(?:apply|adopt)\b",
        message, re.IGNORECASE,
    ))
