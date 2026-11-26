"""HTTP confirmation of generic and metadata edit proposals."""
from datetime import timedelta
from hmac import compare_digest, digest as hmac_digest
from ipaddress import ip_address
import json
import re
from secrets import token_hex
from threading import Lock

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from app.creation_execution import CreationExecutionActor
from app.creation.cover_use_case import COVER_OPERATION
from app.creation.editing.audio_edit_use_case import AudioEditRejected
from app.creation.editing.exact_preview_use_case import ExactEditResult
from app.creation.editing.linked_timeline_use_case import LINKED_EDIT_OPERATIONS, InsertMaterialMissing, InsertSourceRangeError
from app.creation.editing.metadata_use_case import EditPreviewRejected, LegacyMontageReference, InvalidMontage
from app.creation.editing.subtitle_use_case import is_subtitle_edit
from app.models import utc_now
from app.schemas import CreationTaskEditConfirmResponse, CreationTaskExactEditRequest


DESKTOP_SECTION_CONTROL_HEADER = "X-Yaohe-Desktop-Control"


def has_desktop_section_control(request, token):
    supplied = request.headers.get(DESKTOP_SECTION_CONTROL_HEADER, "")
    if not re.fullmatch(r"[0-9a-f]{64}", token or "") or not re.fullmatch(r"[0-9a-f]{64}", supplied):
        return False
    try:
        return request.client is not None and ip_address(request.client.host).is_loopback and compare_digest(supplied, token)
    except ValueError:
        return False


class DesktopSectionConfirmation:
    """Keep signed selections in the API process. The read surface sees only a random locator."""

    def __init__(self):
        self._guard = Lock()
        self._selections = {}

    @staticmethod
    def _signature(token, purpose, values):
        payload = json.dumps([purpose, values], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hmac_digest(bytes.fromhex(token), payload.encode(), "sha256").hex()

    def _expire(self, now):
        for key, (expires, _selection, _signature) in list(self._selections.items()):
            if expires <= now:
                del self._selections[key]

    def issue(self, token, selection):
        selection_id = token_hex(32)
        now = utc_now()
        expires = now + timedelta(seconds=120)
        signature = self._signature(token, "section-confirmation", [selection_id, expires.isoformat(), selection])
        with self._guard:
            self._expire(now)
            if len(self._selections) >= 256:
                raise HTTPException(429, "待确认项过多，请稍后重新读取原候选")
            self._selections[selection_id] = (expires, selection, signature)
        return selection_id, expires

    def read(self, token, selection_id):
        now = utc_now()
        with self._guard:
            self._expire(now)
            saved = self._selections.get(selection_id)
        if saved is None:
            raise HTTPException(409, "原桌面确认已过期或不存在，请重新读取候选")
        expires, selection, signature = saved
        expected = self._signature(token, "section-confirmation", [selection_id, expires.isoformat(), selection])
        if not compare_digest(signature, expected):
            raise HTTPException(403, "原桌面确认来源无效")
        return selection

    def summary(self, task_id, proposal_id, request_id, request, *, token, actor_for, case, owner, metadata_response):
        if not re.fullmatch(r"[0-9a-f]{64}", token or ""):
            raise HTTPException(403, "此采用需要受信桌面")
        actor, credential = actor_for(request)
        try:
            facts = case.knowledge_candidate(actor.workspace_id, task_id, actor, proposal_id, owner)
            saved = case.knowledge_candidate_receipt(actor.workspace_id, task_id, proposal_id, request_id)
        except KeyError as exc:
            raise HTTPException(404, "原资料候选不存在") from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(409, str(exc)) from exc
        selection = facts.proposal.operation["payload"]
        session = self._signature(token, "section-session", [actor.workspace_id, actor.identity_payload(), credential])
        binding = {key: selection[key] for key in (
            "job_id", "publication_id", "section_id", "base_version_id", "selection_digest")}
        binding.update(proposal_id=proposal_id, request_id=request_id, session_fingerprint=session)
        if saved is not None:
            response = {"status": "applied", **binding, "result": metadata_response(saved).model_dump(mode="json")}
        else:
            selection_id, expires = self.issue(token, {**binding, "workspace_id": actor.workspace_id,
                "task_id": task_id, "actor": actor.identity_payload()})
            response = {"status": "proposed", **binding, "selection_id": selection_id, "expires_at": expires.isoformat(),
                "before_text": facts.before_text, "after_text": facts.segment.text,
                "knowledge_evidence": facts.publication.payload["knowledge_evidence"],
                "impact": ["采用目标段的正文、配音、字幕及镜头，保留其他段落。",
                           "原整片输出保留在历史中，当前作品需重新合成。"]}
        return JSONResponse(response, headers={"Cache-Control": "no-store"})

    def confirm(self, task_id, proposal_id, request, payload, *, token, verify_identity, task_open,
                case, owner, metadata_response):
        if not has_desktop_section_control(request, token) or payload is None or payload.desktop_selection_id is None:
            raise HTTPException(403, "资料候选必须在受信桌面明确确认")
        saved = self.read(token, payload.desktop_selection_id)
        if (saved["task_id"], saved["proposal_id"]) != (task_id, proposal_id):
            raise HTTPException(404, "原资料候选不存在")
        if (saved["request_id"], saved["base_version_id"], saved["selection_digest"]) != (
                str(payload.request_id), payload.expected_base_version_id, payload.expected_input_digest):
            raise HTTPException(409, "桌面确认与所见候选、基础版本或原请求不一致")
        actor = CreationExecutionActor.from_event(saved["workspace_id"], saved["actor"])
        access = verify_identity(actor)
        if access != "ready":
            raise HTTPException(503 if access == "unknown" else 403, "原桌面成员许可已失效或无法核验")
        task_open(actor, task_id)

        def verify_confirmation(conn, facts):
            if self.read(token, payload.desktop_selection_id) != saved:
                raise PermissionError("原桌面选择已变化")
            if actor.device_id is None:
                member = conn.execute("SELECT token FROM local_members WHERE member_id=?", (actor.member_id,)).fetchone()
                if member is None or self._signature(token, "section-session",
                        [actor.workspace_id, actor.identity_payload(), member["token"]]) != saved["session_fingerprint"]:
                    raise PermissionError("原桌面成员会话已变化")
            selection = facts.proposal.operation["payload"]
            if any(selection[key] != saved[key] for key in (
                    "job_id", "publication_id", "section_id", "base_version_id", "selection_digest")):
                raise ValueError("原桌面选择与已登记候选不一致")

        try:
            outcome = case.adopt_knowledge_candidate(actor.workspace_id, task_id, actor, proposal_id, owner,
                request_id=saved["request_id"], base_version_id=saved["base_version_id"],
                selection_digest=saved["selection_digest"], verify_confirmation=verify_confirmation)
        except KeyError as exc:
            raise HTTPException(404, "原资料候选不存在") from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(409, str(exc)) from exc
        return metadata_response(outcome)


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
