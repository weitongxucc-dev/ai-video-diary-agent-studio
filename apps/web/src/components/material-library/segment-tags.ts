import type { MaterialSegment } from "@/lib/types";
export function observedTagSuggestions(segment: MaterialSegment) {
    if (segment.description.trim() || segment.understanding?.status !== "ready")
        return [];
    const seen = new Set(segment.user_tags);
    return segment.understanding.facts.flatMap(fact => {
        const label = (fact.label || fact.text).trim();
        if (!label || label.length > 32 || seen.has(label))
            return [];
        seen.add(label);
        const source = fact.kind === "action" ? "动作观察" : fact.kind === "scene" ? "场景观察" : "主体观察";
        return [{ label, source, times: fact.sample_times }];
    });
}
