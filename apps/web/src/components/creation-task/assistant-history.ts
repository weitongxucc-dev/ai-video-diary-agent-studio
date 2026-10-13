import type { CreationTaskAssistantHistory, AssistantTurnActivity } from "@/lib/types";
export type AssistantTurnRecord = {
    command: string;
    reply: string;
    context: string;
    activity?: AssistantTurnActivity;
};
export function assistantHistoryTurns(items: CreationTaskAssistantHistory["items"]): AssistantTurnRecord[] {
    const turns: AssistantTurnRecord[] = [];
    let pending: CreationTaskAssistantHistory["items"][number] | null = null;
    for (const item of items) {
        if (item.role === "user") {
            if (pending)
                turns.push({ command: pending.message, reply: "上次没有得到完整回复。", context: pending.stage });
            pending = item;
        }
        else {
            turns.push({ command: pending?.message ?? "", reply: item.message, context: item.stage });
            pending = null;
        }
    }
    if (pending)
        turns.push({ command: pending.message, reply: "上次没有得到完整回复。", context: pending.stage });
    return turns.slice(-8);
}
