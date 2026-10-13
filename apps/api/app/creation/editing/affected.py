"""Source authority scope of an actual before/after picture change."""
from dataclasses import asdict


def affected_source_clip_ids(before, after):
    """Freeze changed shots and changed cuts/dissolves, retaining original source IDs.

    New split/insert IDs have no original row; their original shot or explicit
    replacement reference supplies the source facts. Unchanged distant shots
    are not included merely because they belong to the same task.
    """
    old = {clip.id:clip for clip in before.clips}
    new = {clip.id:clip for clip in after.clips}
    changed = {clip_id for clip_id in old.keys() | new.keys() if clip_id not in old or clip_id not in new
        or asdict(old[clip_id]) != asdict(new[clip_id])}
    def edges(plan):
        return {(left.id, right.id, right.transition_in_seconds) for left,right in zip(plan.clips,plan.clips[1:])}
    previous, current = edges(before), edges(after)
    affected = set(changed)
    for left,right,_transition in previous ^ current:
        affected.update((left,right))
    for left,right,transition in previous | current:
        if transition and ({left,right} & changed):
            affected.update((left,right))
    return tuple(clip.id for clip in before.clips if clip.id in affected)


def affected_clip_facts(plan, clip_ids):
    return [{'position':index, 'clip':asdict(clip)} for index,clip in enumerate(plan.clips) if clip.id in clip_ids]
