import { Clapperboard, ClipboardList, FileText, Lightbulb, Mic, Scissors } from "lucide-react";

// The leading glyph identifies the stage; the adjacent text owns its state.
const stageIcons = { topic: Lightbulb, script: FileText, narration: Mic, montage: Scissors, review: ClipboardList };

export function CreationTaskStageIcon({ stage, className }: { stage: string; className?: string }) {
  const Icon = Object.hasOwn(stageIcons, stage) ? stageIcons[stage as keyof typeof stageIcons] : Clapperboard;
  return <Icon aria-hidden="true" className={className} data-task-stage={stage} size={16} strokeWidth={1.7} />;
}
