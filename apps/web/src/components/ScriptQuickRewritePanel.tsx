import { regenerateScriptSectionAction } from "@/app/actions";
import type { ScriptRewriteIntent } from "@/lib/types";

export function ScriptQuickRewritePanel({
  episodeId,
  intents,
  sectionId,
  sectionTitle,
}: {
  episodeId: string;
  intents: ScriptRewriteIntent[];
  sectionId: string;
  sectionTitle: string;
}) {
  if (intents.length === 0) {
    return null;
  }

  return (
    <form action={regenerateScriptSectionAction} className="script-quick-rewrite-panel">
      <input name="episode_id" type="hidden" value={episodeId} />
      <input name="section_id" type="hidden" value={sectionId} />
      <input name="rewrite_section_title" type="hidden" value={sectionTitle} />
      <div className="script-quick-rewrite-head">
        <span className="section-kicker">QUICK REWRITE</span>
        <strong>快捷重写</strong>
      </div>
      <div className="script-quick-rewrite-grid" aria-label={`${sectionTitle} 快捷重写`}>
        {intents.map((intent) => (
          <button
            className="pixel-button compact secondary"
            key={intent.id}
            name="rewrite_intent_id"
            title={intent.description}
            type="submit"
            value={intent.id}
          >
            {intent.label}
          </button>
        ))}
      </div>
      <input
        className="script-quick-rewrite-note"
        name="rewrite_user_note"
        placeholder="补充要求"
        type="text"
      />
    </form>
  );
}
