"""HTTP identity and persisted proposal inputs for edit commands."""
from app.creation.editing.linked_timeline_use_case import LinkedEditApplyCommand
from app.creation.editing.metadata_use_case import metadata_edit_command


class TaskEditCommandHttpAdapter:
    def __init__(self, *, repo_for, content_reader, assistant_member, exact_case_for):
        self.repo_for = repo_for
        self.content_reader = content_reader
        self.assistant_member = assistant_member
        self.exact_case_for = exact_case_for

    def linked_timeline_command(self, task,request,operation_type,values,*,source=None):
        member_id,diagnostic=self.content_reader(request,task.workspace_id)
        if source is not None:
            member_id,diagnostic=self.assistant_member(request,task.workspace_id),False
        identity={'member_id':member_id,'local_diagnostic':diagnostic}
        reader_identity=None
        if operation_type == 'InsertClip':
            reader,reader_diagnostic=self.content_reader(request,task.workspace_id)
            reader_identity={'member_id':reader,'local_diagnostic':reader_diagnostic}
        return self.exact_case_for().linked_command(task,identity,operation_type,values,
            source=source,reader_identity=reader_identity)

    def linked_timeline_apply_command(self, task, proposal, request, payload=None):
        source = self.repo_for().edit_records.get_edit_intent(task.workspace_id, proposal.intent_id).constraints.get("request_source")
        command = self.linked_timeline_command(task, request, proposal.operation["operation_type"], proposal.operation["payload"], source=source)
        requesting = payload is not None and payload.request_id is not None
        return LinkedEditApplyCommand(task.workspace_id, task.id, proposal.id, command.actor_identity,
            self.assistant_member(request, task.workspace_id), source=source,
            request_id=str(payload.request_id) if requesting else None,
            expected_base_version_id=payload.expected_base_version_id if requesting else None,
            base_version_submitted=requesting and "expected_base_version_id" in payload.model_fields_set,
            expected_input_digest=payload.expected_input_digest if requesting else None,
            request_fields=payload.model_dump(mode="json", exclude_unset=True) if requesting else {})

    def metadata_edit_command(self, task, request, operation_type, values, *, source=None):
        user_id, diagnostic = self.content_reader(request, task.workspace_id)
        if source is not None:
            user_id, diagnostic = self.assistant_member(request, task.workspace_id), False
        return metadata_edit_command(task, {"member_id":user_id, "local_diagnostic":diagnostic}, operation_type, values, source=source)

    def compound_proposal_command(self, task, proposal, request):
        source = self.repo_for().edit_records.get_edit_intent(task.workspace_id, proposal.intent_id).constraints.get("request_source")
        return self.metadata_edit_command(task, request, "EditCompoundTimeline", proposal.operation["payload"], source=source)

    def subtitle_proposal_command(self, task, proposal, request):
        source = self.repo_for().edit_records.get_edit_intent(task.workspace_id, proposal.intent_id).constraints.get("request_source")
        return self.metadata_edit_command(task, request, proposal.operation["operation_type"], proposal.operation["payload"], source=source)
