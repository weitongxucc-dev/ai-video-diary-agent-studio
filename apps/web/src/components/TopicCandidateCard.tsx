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
        <span className="score-badge">{topic.score}</span>
        <span className="topic-card-status">{getFeedbackLabel(topic.feedback)}</span>
      </div>
      <h2>{topic.title}</h2>
      <p>{topic.core_viewpoint}</p>
      <div className="source-row">
        {topic.source_refs.map((source) => (
          <span key={`${source.source_type}-${source.ref}`} className="source-badge">
            {source.source_type}
          </span>
        ))}
      </div>
    </Link>
  );
}
