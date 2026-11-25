"""Read only the fixed page citations selected in the consumed task input."""


def selected_knowledge_pages(context):
    references = list((context.get("target_snapshot") or {}).get("selected_references", []))
    if context.get("knowledge_reference"):
        references.append({"kind": "knowledge", **context["knowledge_reference"]})
    identities = dict.fromkeys((item["knowledge_item_id"], item["version"], item["locator"])
        for item in references if item["kind"] == "knowledge" and item.get("locator"))
    return [{"object_id": item_id, "version_or_identity": version, "locator": locator}
            for item_id, version, locator in identities]
