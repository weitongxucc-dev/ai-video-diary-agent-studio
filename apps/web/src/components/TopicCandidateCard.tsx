import Link from "next/link";
import type { TopicCandidate } from "@/lib/types";

function getFeedbackLabel(feedback: TopicCandidate["feedback"]) {
  if (feedback === "down") {
    return "已踩";
  }

  if (feedback === "up") {
    return "已赞";
  }

  return "待复核";
}

function sourceShortLabel(sourceType: string): string {
  const normalized = sourceType.trim().toLowerCase();
  if (normalized === "obsidian") {
    return "OB";
  }
  if (normalized === "product hunt") {
    return "PH";
  }
  if (sourceType.includes("观猹")) {
    return "观";
  }
  if (sourceType.includes("外链")) {
    return "EXT";
  }
  if (sourceType.includes("示例")) {
    return "DEMO";
  }
  return sourceType.slice(0, 4).toUpperCase();
}

export function TopicCandidateCard({
  active,
  topic,
}: {
  active: boolean;
  topic: TopicCandidate;
}) {
  return (
    <Link
      className={`topic-card pixel-card${active ? " selected" : ""}`}
      href={`/topics?topic_id=${encodeURIComponent(topic.id)}`}
    >
      <div className="topic-card-header">
        <div className="topic-card-meta">
          <span className="score-badge">{topic.score}</span>
          <span className="source-badge-list" aria-label="来源依据">
            {topic.source_refs.slice(0, 3).map((source) => (
              <span
                key={`${source.source_type}-${source.ref}`}
                className="source-badge"
                title={`${source.source_type} · ${source.title}`}
              >
                {sourceShortLabel(source.source_type)}
              </span>
            ))}
          </span>
        </div>
        <span className="topic-card-status">{getFeedbackLabel(topic.feedback)}</span>
      </div>
      <h2>{topic.title}</h2>
      <p>{topic.core_viewpoint}</p>
    </Link>
  );
}
