import type { ScriptSection } from "@/lib/types";

function getFeedbackLabel(feedback: ScriptSection["feedback"]) {
  if (feedback === "down") {
    return "已踩";
  }

  if (feedback === "up") {
    return "已赞";
  }

  return "待复核";
}

export function ScriptSectionEditor({
  formId,
  section
}: {
  formId: string;
  section: ScriptSection;
}) {
  const charCount = section.content.length;
  const charsPerSecond = Math.round((charCount / Math.max(section.estimated_seconds, 1)) * 10) / 10;

  return (
    <section className="script-section pixel-card" id={`script-section-${section.id}`}>
      <input form={formId} name="section_id" type="hidden" value={section.id} />
      <input
        form={formId}
        name={`section_feedback:${section.id}`}
        type="hidden"
        value={section.feedback ?? ""}
      />
      <div className="script-section-header">
        <div>
          <span className="script-section-label">段落</span>
          <input
            aria-label={`${section.title} 段落标题`}
            className="script-section-title-input"
            form={formId}
            name={`section_title:${section.id}`}
            defaultValue={section.title}
          />
        </div>
        <div className="script-section-meta">
          <label>
            预计
            <input
              aria-label={`${section.title} 预计秒数`}
              className="script-section-seconds-input"
              form={formId}
              min="1"
              name={`section_seconds:${section.id}`}
              type="number"
              defaultValue={String(section.estimated_seconds)}
            />
            s
          </label>
          <span>{getFeedbackLabel(section.feedback)}</span>
        </div>
      </div>
      <textarea
        aria-label={`${section.title} 口播稿内容`}
        defaultValue={section.content}
        form={formId}
        name={`section_content:${section.id}`}
      />
      <div className="script-section-footer">
        <span>{charCount} 字</span>
        <span>{charsPerSecond} 字/s</span>
        <span>口播节奏：先故事，再观点，最后行动</span>
      </div>
    </section>
  );
}
