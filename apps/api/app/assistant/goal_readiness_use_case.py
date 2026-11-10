"""Current original-goal evidence and bounded output checkpoints.

Readiness is read-only. Existing continuation and conversation owners persist
its facts and claim the next event with their original CAS and transactions.
"""
from app.assistant.references import (AssistantReferenceConflict, normalize_selection,
    validate_timeline_reference, timeline_object_refs)
from app.creation.duration_requirement import DurationRequirement
from app.montage import creation_task_montage_plan_from_payload


class GoalEvidenceUnavailable(Exception):
    """A formerly transport-mapped evidence read failed at its original gate."""
    def __init__(self, status_code, detail):
        super().__init__(str(detail))
        self.status_code, self.detail = status_code, detail


def sealed_goal_reference(scope,item):
    if item not in scope.get('selected_references',[]):
        raise AssistantReferenceConflict('原目标引用未在采用时封存，未读取私人资料')
    return item


class GoalReadinessUseCase:
    def __init__(self, *, projection_for, business_for, read_timeline, read_script,
                 completed_sources, read_request, read_media, read_execution,
                 verify_resume, observations, registered_output, read_plan,
                 read_subtitles, safe_text):
        self.projection_for, self.business_for = projection_for, business_for
        self.read_timeline, self.read_script = read_timeline, read_script
        self.completed_sources, self.read_request = completed_sources, read_request
        self.read_media, self.read_execution = read_media, read_execution
        self.verify_resume, self.observations = verify_resume, observations
        self.registered_output, self.read_plan = registered_output, read_plan
        self.read_subtitles, self.safe_text = read_subtitles, safe_text

    def selection_context(self, goal, task, version, scope, removed_refs):
        """Rebuild only derived continuation references over this goal's proved chain."""
        original = goal["request_context"]["target_snapshot"].get("selected_references", [])
        timeline_kinds = {"shot", "narration", "audio", "subtitle", "effect"}
        if not any(item["kind"] in timeline_kinds for item in original):
            return {}, None
        chain = self.projection_for().selection_version_chain(goal, version)
        if chain is None:
            raise AssistantReferenceConflict("接续引用没有同一输入的已核验版本链")
        prefixes = {"shot": "clip", "narration": "narration", "audio": "audio", "subtitle": "cue", "effect": "overlay"}
        items = []
        for item in original:
            if item["kind"] in timeline_kinds:
                if f"{prefixes[item['kind']]}:{item['object_id']}" in removed_refs:
                    continue
                if item["task_id"] != task.id or item["version"] not in chain:
                    raise AssistantReferenceConflict("接续引用不属于原目标的版本链")
                item = {**item, "version": version.id}
            items.append(item)
        # Materialize current subtitle identities through their existing owner;
        # derive track refs from the same selected cue, never a name/language guess.
        tracks = self.read_subtitles(task, version) if any(item["kind"] == "subtitle" for item in items) else []
        items = [validate_timeline_reference(item, task=task, timeline=version, subtitle_tracks=tracks)
                 if item["kind"] in timeline_kinds else sealed_goal_reference(scope,item) for item in items]
        reference = normalize_selection({"kind": "selection", "items": items}) if items else None
        items = reference["items"] if reference else []
        refs = timeline_object_refs(items, task.id, subtitle_tracks=tracks)
        return {"selected_references": items}, {"selected_reference": reference, "selected_object_refs": refs}

    def verify(self, goal, event):
        owner = self.projection_for()
        business=self.business_for()
        workspace_id,_unused_task_id=goal['workspace_id'],goal['task_id']
        try:
            actor,task,scope=business.original(goal)
            version=self.read_timeline(workspace_id,task.current_timeline_version_id) if task.current_timeline_version_id else None
        except (KeyError,ValueError,PermissionError):
            return owner.unavailable_fact('identity')
        decision = owner.verify_version(goal, event, task)
        if decision is not None:
            return decision
        if event.get("phase") == "script_adoption":
            if task.confirmed_script_id != event["script_id"]:
                return {"status": "invalidated", "reason": "adopted_script_changed"}
            script = self.read_script(workspace_id, event["script_id"])
            if script.updated_at.isoformat() != event["script_version"]:
                return {"status": "invalidated", "reason": "adopted_script_changed"}
        try:
            completed_sources = self.completed_sources(goal, actor)
        except (KeyError, ValueError, PermissionError, OSError):
            return owner.unavailable_fact("completed_sources")
        pending = self.projection_for().pending_preview(goal, task.current_timeline_version_id)
        if pending is not None:
            return {**pending, "completed_source_slots": completed_sources}
        facts = {"business_event": event, "task": {"id": task.id, "stage": task.stage, "output_ready": task.output_ready},
            "completed_source_slots": completed_sources}
        if event.get('phase')=='creation_resume':
            def read_media(*args):
                try:
                    return self.read_media(*args)
                except GoalEvidenceUnavailable as exc:
                    raise ValueError(str(exc.detail)) from exc
            reconciled=self.verify_resume(goal,task,actor,business,owner,
                read_request=self.read_request,read_media=read_media)
            if reconciled['status']!='ready':
                return reconciled
            facts.update(reconciled['facts'])
        no_side_effect = False
        removed_refs = owner.removed_references(goal)
        if event.get("phase") == "request":
            decision, located = owner.request_locator(event)
            if decision is not None:
                return decision
            actual = self.read_request(task,actor,located["request_id"],located["phase"])
            facts["original_request"] = actual
            decision, no_side_effect = owner.request_verification(goal, event, actual)
            if decision is not None:
                return decision
        if event.get("phase") == "execution":
            try:
                facts["execution_status"] = self.read_execution(business,goal,actor,job_id=event["job_id"])
            except (KeyError, ValueError, GoalEvidenceUnavailable):
                return owner.unavailable_fact("execution")
            decision = owner.execution_verification(facts["execution_status"])
            if decision is not None:
                return decision
        elif owner.needs_media_receipt(event):
            try:
                actual_job = self.read_media(task,actor,event["job_id"])
            except GoalEvidenceUnavailable:
                return owner.unavailable_fact("media")
            decision = owner.media_verification(actual_job)
            if decision is not None:
                return decision
            facts["media_job"] = actual_job
        decision, adopted, removed_refs = owner.adopted_operation(goal, event, task, removed_refs)
        if decision is not None:
            return decision
        if adopted is not None:
            facts["adopted_operation"] = adopted
        deferred = owner.deferred_edit_execution(goal, event)
        if deferred is not None:
            return deferred
        facts["observations"] = self.observations(task,goal["member_id"])
        context = goal["request_context"]
        for key in ('profile_reference','knowledge_reference'):
            if context.get(key):
                if key not in scope:
                    return owner.unavailable_fact('reference')
                facts[key]=scope[key]
        checkpoint = self.output_checkpoint(goal, event, facts, task, version, actor, business)
        try:
            reference_snapshot, selection_context = self.selection_context(goal, task, version, scope, removed_refs)
        except (AssistantReferenceConflict, GoalEvidenceUnavailable):
            return owner.unavailable_fact("reference")
        fresh = owner.ready(task, version, actor, facts, no_side_effect, completed_sources, removed_refs, checkpoint)
        fresh["task_version"].update(reference_snapshot)
        return {**fresh, "selection_context": selection_context, "business": business}

    def output_checkpoint(self, goal, event, facts, task, version, actor, business):
        owner = self.projection_for()
        observation = owner.output_observation(goal, event, facts, task, version)
        if observation is None:
            return None
        try:
            _path, output, _binding = self.registered_output(task.workspace_id, task.id, actor)
        except (KeyError, OSError, ValueError, PermissionError, GoalEvidenceUnavailable):
            return None
        sources = owner.output_sources(goal, task, version, observation, output)
        if sources is None:
            return None
        for row in sources["unknown_requests"]:
            located = owner.unknown_request_for_goal(goal, row)
            if located is not None:
                original = self.read_request(task,actor,located["request_id"],located["phase"])
                if owner.request_still_unknown(original):
                    return None
        source = owner.output_execution_source(goal, version, sources)
        if source is None:
            return None
        actual = self.read_execution(business,goal,actor,job_id=source["job_id"])
        duration_target = None
        if goal.get("creation_adoption"):
            montage = creation_task_montage_plan_from_payload(version.snapshot["montage"])
            duration_target = DurationRequirement.for_output(self.read_plan(task), montage).target_seconds
        checkpoint = owner.completed_output(observation, output, version, source, actual,
            goal=goal, duration_target_seconds=duration_target)
        if checkpoint is not None:
            checkpoint["unavailable"] = [{"scope": item["scope"], "reason": self.safe_text(item["reason"], task.workspace_id)}
                for item in checkpoint["unavailable"]]
        return checkpoint
