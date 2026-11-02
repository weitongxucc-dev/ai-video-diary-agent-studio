"""HTTP confirmation of generic and metadata edit proposals."""
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from app.creation_execution import CreationExecutionActor
from app.creation.cover_use_case import COVER_OPERATION
from app.creation.editing.audio_edit_use_case import AudioEditRejected
from app.creation.editing.exact_preview_use_case import ExactEditResult
from app.creation.editing.linked_timeline_use_case import LINKED_EDIT_OPERATIONS, InsertMaterialMissing, InsertSourceRangeError
from app.creation.editing.metadata_use_case import EditPreviewRejected, LegacyMontageReference, InvalidMontage
from app.creation.editing.subtitle_use_case import is_subtitle_edit
from app.schemas import CreationTaskEditConfirmResponse, CreationTaskExactEditRequest


class TaskEditConfirmationHttpAdapter:
    def __init__(self, *, repo_for, workspace, task404, cover_confirmation, metadata_confirmations_for,
                 source_facts, content_reader, member_id, task_open, auth_enabled, request_actor,
                 exact_case_for, metadata_response, topic_failure):
        self.repo_for = repo_for
        self.workspace = workspace
        self.task404 = task404
        self.cover_confirmation = cover_confirmation
        self.metadata_confirmations_for = metadata_confirmations_for
        self.source_facts = source_facts
        self.content_reader = content_reader
        self.member_id = member_id
        self.task_open = task_open
        self.auth_enabled = auth_enabled
        self.request_actor = request_actor
        self.exact_case_for = exact_case_for
        self.metadata_response = metadata_response
        self.topic_failure = topic_failure

    def confirm(self, task_id: str, proposal_id: str, request: Request) -> CreationTaskEditConfirmResponse:
        workspace_id = self.workspace(request)
        task = self.task404(workspace_id, task_id)
        try:
            proposal = self.repo_for().edit_records.get_edit_proposal(workspace_id, proposal_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='creation task edit proposal not found') from exc
        if proposal.task_id == task_id and proposal.operation.get('operation_type') == COVER_OPERATION:
            return self.cover_confirmation(task, proposal, request)
        if proposal.task_id != task_id or proposal.status != 'proposed':
            raise HTTPException(status_code=409, detail='creation task edit proposal is not confirmable')
        if proposal.operation.get('operation_type') in LINKED_EDIT_OPERATIONS:
            return self.metadata_confirmations_for().confirm_linked_timeline_response(task, proposal, request)
        if is_subtitle_edit(proposal.operation.get('operation_type'), proposal.operation.get('payload', {})):
            return self.metadata_confirmations_for().confirm_subtitle_response(task, proposal, request)
        if proposal.operation.get('operation_type') == 'EditCompoundTimeline':
            return self.metadata_confirmations_for().confirm_compound_response(task, proposal, request)
        source = self.repo_for().edit_records.get_edit_intent(workspace_id, proposal.intent_id).constraints.get('request_source')
        if source is not None and source['source_facts'] != self.source_facts(task, CreationTaskExactEditRequest.model_validate(source['action']), request):
            raise HTTPException(status_code=409, detail='预览后的源文件或片段修订已变化，请重新预览')
        operation_draft = proposal.operation
        operation_payload = operation_draft.get('payload')
        operation_type = operation_draft.get('operation_type')
        if isinstance(operation_payload,dict):
            reader,diagnostic=self.content_reader(request,workspace_id)
            reader_identity={'member_id':reader,'local_diagnostic':diagnostic}
            identity=({'member_id':self.member_id(request,workspace_id),'local_diagnostic':False}
                if source is not None else reader_identity)
            if operation_type in {'ChangePersonSegment','RegeneratePersonRender','RegenerateNarrationSegment'}:
                self.task_open(request,workspace_id,task_id)
            actor=(self.request_actor(request,workspace_id) if self.auth_enabled()
                else CreationExecutionActor(workspace_id,identity['member_id']))
            try:
                outcome=self.exact_case_for().confirm(task,proposal,identity=identity,
                    reader_identity=reader_identity,actor=actor,source=source)
            except (EditPreviewRejected,AudioEditRejected) as exc:
                raise HTTPException(status_code=exc.status_code,detail=exc.detail) from exc
            except LegacyMontageReference as exc:
                raise self.topic_failure(**exc.details) from exc
            except InvalidMontage as exc:
                raise HTTPException(status_code=422,detail=str(exc)) from exc
            except PermissionError as exc:
                raise HTTPException(status_code=403,detail=str(exc)) from exc
            except (ValueError,OSError) as exc:
                raise HTTPException(status_code=409,detail=str(exc)) from exc
            if isinstance(outcome,ExactEditResult):
                return JSONResponse(status_code=outcome.status_code,content=outcome.value)
            return self.metadata_response(outcome)
        raise HTTPException(status_code=409, detail='unsupported creation task edit operation')


class MetadataEditConfirmationHttpAdapter:
    def __init__(self, *, linked_case_for, subtitle_case_for, compound_case_for,
                 linked_command, subtitle_command, compound_command,
                 metadata_response, save_receipt, repo_for, wake):
        self.linked_case_for = linked_case_for
        self.subtitle_case_for = subtitle_case_for
        self.compound_case_for = compound_case_for
        self.linked_command = linked_command
        self.subtitle_command = subtitle_command
        self.compound_command = compound_command
        self.metadata_response = metadata_response
        self.save_receipt = save_receipt
        self.repo_for = repo_for
        self.wake = wake

    def confirm_linked_timeline_response(self, task, proposal, request, payload=None, *, wake=False):
        try:
            outcome = self.linked_case_for().confirm(self.linked_command(task, proposal, request, payload))
        except InsertMaterialMissing as exc:
            raise HTTPException(status_code=404, detail=exc.args[0]) from exc
        except InsertSourceRangeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="编辑提案不存在") from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        response = self.metadata_response(outcome.outcome)
        if outcome.record_projection:
            if outcome.newly_applied:
                self.save_receipt(task, outcome.event_id, response)
            else:
                self.repo_for().execution_records.save_edit_request_result(task, outcome.event_id,
                    status_code=200, response=response.model_dump(mode="json"))
        if wake and outcome.newly_applied:
            self.wake()
        return response

    def confirm_subtitle_response(self, task, proposal, request):
        try:
            outcome = self.subtitle_case_for().confirm(self.subtitle_command(task, proposal, request), proposal.id)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (ValueError, KeyError, OSError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return self.metadata_response(outcome)

    def confirm_compound_response(self, task, proposal, request):
        try:
            outcome = self.compound_case_for().confirm(self.compound_command(task, proposal, request), proposal.id)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return self.metadata_response(outcome)
