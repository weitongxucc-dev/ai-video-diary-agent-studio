"""Project a fixed knowledge citation through the current member's read authority."""


class KnowledgeReferenceUnavailable(LookupError):
    pass


def read_knowledge_reference(sources, workspace_id, knowledge_item_id, version, locator=None, *,
                             user_id, local_diagnostic=False, include_content=False):
    try:
        item = sources.get_knowledge_reference_readable_by(
            workspace_id, knowledge_item_id, version, user_id=user_id, local_diagnostic=local_diagnostic,
        )
    except KeyError as exc:
        raise KnowledgeReferenceUnavailable("所引用知识版本不存在或当前不可访问。") from exc
    sections = [section for section in item.sections if locator is None or section.locator == locator]
    parameters = [parameter for parameter in item.parameters if locator is None or parameter.locator == locator]
    if locator is not None and not sections and not parameters:
        raise KnowledgeReferenceUnavailable("所引用定位不在这个知识版本中，请重新查看该版正文。")
    facts = {"knowledge_item_id": item.id, "version": item.version, "locator": locator,
             "title": item.title, "content_hash": item.content_hash, "use_state": item.use_state,
             "instruction": "固定版本知识事实，只读引用；不授权编辑、用于任务或发布。"}
    if include_content:
        facts.update(body=item.body if locator is None else None,
                     sections=[{"locator": section.locator, "title": section.title, "text": section.text} for section in sections],
                     parameters=[{"locator": parameter.locator, "key": parameter.key, "value": parameter.value, "unit": parameter.unit} for parameter in parameters])
    return facts
