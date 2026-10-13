import type { CreationTask } from "@/lib/types";

// The complete GET owns collection membership; task props carry finite facts only.
export type CreationTaskListFacts = { complete: boolean; items: CreationTask[] };

export function initialTaskListFacts(items: CreationTask[]): CreationTaskListFacts {
  return applyTaskFactUpdates({ complete: false, items: [] }, items);
}

export function applyTaskFactUpdates(current: CreationTaskListFacts, updates: CreationTask[]): CreationTaskListFacts {
  const existing = new Map(current.items.map(task => [task.id, task]));
  const ordered = new Map<string, CreationTask>();
  for (const update of updates) {
    const prior = ordered.get(update.id) ?? existing.get(update.id);
    if (current.complete && !prior) continue;
    ordered.set(update.id, prior && (Date.parse(prior.updated_at) > Date.parse(update.updated_at) || (ordered.has(update.id) && prior.updated_at === update.updated_at)) ? prior : update);
  }
  for (const task of current.items) if (!ordered.has(task.id)) ordered.set(task.id, task);
  return { complete: current.complete, items: [...ordered.values()] };
}

export function applyTaskListSnapshot(
  current: CreationTaskListFacts,
  items: CreationTask[],
  taskFactsArrivedDuringRead: boolean,
): CreationTaskListFacts {
  const existing = new Map(current.items.map(task => [task.id, task]));
  return { complete: true, items: items.map(task => {
    const prior = existing.get(task.id);
    if (!prior) return task;
    const priorTime = Date.parse(prior.updated_at);
    const nextTime = Date.parse(task.updated_at);
    // A newer task receipt stays current, but never restores an absent collection member.
    return priorTime > nextTime || (taskFactsArrivedDuringRead && priorTime === nextTime) ? prior : task;
  }) };
}
