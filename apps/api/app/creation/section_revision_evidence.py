"""Fixed knowledge pages used by one existing section revision."""
from dataclasses import asdict
import hashlib
import json

from app.source_policy import content_allows_use


def fixed_page(sources, workspace_id, member_id, reference):
    item_id, version, locator = (reference[key] for key in ("object_id", "version_or_identity", "locator"))
    try:
        item = sources.get_knowledge_reference_readable_by(workspace_id, item_id, version, user_id=member_id)
    except KeyError as exc:
        raise ValueError("修订引用的资料版本已不可读取。") from exc
    if not content_allows_use(item.use_state, "reference"):
        raise ValueError("修订引用的资料当前使用范围不允许此操作。")
    sections = [asdict(section) for section in item.sections if section.locator == locator]
    parameters = [asdict(parameter) for parameter in item.parameters if parameter.locator == locator]
    if not sections and not parameters:
        raise ValueError("修订引用的页码不在固定资料版本中。")
    identity = json.dumps([item_id, version, locator], ensure_ascii=False)
    content = json.dumps([sections, parameters], ensure_ascii=False, sort_keys=True)
    return {
        "reference_id": "knowledge-page:" + hashlib.sha256(identity.encode()).hexdigest(),
        "kind": "knowledge", "object_id": item_id, "version_or_identity": version, "locator": locator,
        "content_digest": hashlib.sha256(content.encode()).hexdigest(), "binding_status": "fixed",
    }
