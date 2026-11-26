"""Fixed catalog audio import through the existing ingress, journal and Job owners."""
from uuid import NAMESPACE_URL, UUID, uuid5

from app.creation.audio_ingress_use_case import AudioIngressRejected
from app.creation.contracts import TaskEvent
from app.creation_execution import CreationExecutionActor
from app.models import utc_now


def _resource_claims(events, actor):
    member_id = actor.user_id or actor.member_id
    return (event for event in events
            if event.event_type == 'edit_request_claimed'
            and event.payload.get('phase') == 'audio_resource'
            and event.payload.get('member_id') == member_id)


class ResourceAudioUseCase:
    def __init__(self, repository, ingress, media, *, catalog_for, path_for,
                 temporary_root, cpu_budget, receipt_for, conversations):
        self.repository, self.ingress, self.media = repository, ingress, media
        self.catalog_for, self.path_for = catalog_for, path_for
        self.temporary_root, self.cpu_budget = temporary_root, cpu_budget
        self.receipt_for, self.conversations = receipt_for, conversations

    def _task(self, actor, task_id):
        try:
            task = self.repository.task_records.get_creation_task(actor.workspace_id, task_id)
        except KeyError as exc:
            raise AudioIngressRejected(404, '作品不存在') from exc
        if task.owner_user_id and task.owner_user_id != (actor.user_id or actor.member_id):
            raise AudioIngressRejected(403, '当前成员不能导入此任务音频')
        if self.media.verify_identity(actor) != 'ready':
            raise AudioIngressRejected(403, '当前身份无法核验')
        return task

    def read(self, actor, task_id, request_id):
        task = self._task(actor, task_id)
        request_id = str(UUID(str(request_id)))
        events = self.repository.execution_records.list_task_events(actor.workspace_id, task_id)
        claims = [event for event in _resource_claims(events, actor) if event.payload.get('request_id') == request_id]
        if not claims:
            raise AudioIngressRejected(404, '原资源导入请求不存在；不能据此推断未提交')
        claim, = claims
        saved = self.repository.execution_records.read_edit_request_result(task, claim.id)
        receipt = {'task_id': task_id, 'request_id': request_id, 'status': 'unknown',
                   'submitted_input': claim.payload['inputs'], 'job_id': None}
        if saved and saved['status_code']>=400:
            receipt.update(status='rejected',submission_effect='not_submitted',status_code=saved['status_code'])
        elif saved:
            job, event = self.media.read(actor.workspace_id, saved['response']['job_id'])
            if job.task_id != task_id or CreationExecutionActor.from_event(actor.workspace_id, event) != actor:
                raise AudioIngressRejected(403, '原资源作业归属无法核验')
            receipt.update(status='recorded', job_id=job.id)
        return receipt

    def manual_request_id(self, actor, task_id, *, resource_id, version, retry_of=None):
        identity = actor.identity_payload()
        key = [actor.workspace_id, task_id, identity, resource_id, version]
        if retry_of is None:
            self._task(actor, task_id)
            # Keep the ID of the old HTTP caller that omits request_id.
            return str(uuid5(NAMESPACE_URL, str(key)))
        previous = self.read(actor, task_id, retry_of)
        inputs = previous['submitted_input']
        if (inputs['resource_id'], inputs['version'], inputs['actor_identity']) != (resource_id, version, identity):
            raise AudioIngressRejected(409, '原导入请求与当前资源、版本或成员不一致')
        if previous['status'] != 'rejected' or previous.get('submission_effect') != 'not_submitted':
            raise AudioIngressRejected(409, '原导入尚未确认未提交，请先回查原请求')
        return str(uuid5(NAMESPACE_URL, str(['audio_resource_retry', *key, previous['request_id']])))

    def locate_manual_request(self, actor, task_id, *, resource_id, version, retry_of=None):
        request_id = self.manual_request_id(actor, task_id, resource_id=resource_id, version=version,
                                            retry_of=retry_of)
        original_id = None
        if retry_of is None:
            # The request journal is complete independently of the production history projection.
            # Rank IDs here; the individual receipt still verifies job ownership.
            events = self.repository.execution_records.list_task_events(actor.workspace_id, task_id)
            statuses = {event.id: event.payload['status_code'] for event in events
                        if event.event_type == 'edit_request_result'}
            selection = (resource_id, version, actor.identity_payload())
            latest = {}
            for claim in _resource_claims(events, actor):
                inputs = claim.payload['inputs']
                if (inputs.get('resource_id'), inputs.get('version'), inputs.get('actor_identity')) != selection:
                    continue
                status = statuses.get(claim.id + '_result')
                outcome = 'unknown' if status is None else 'rejected' if status >= 400 else 'recorded'
                latest[outcome] = claim.payload['request_id']
            original_id = latest.get('unknown') or latest.get('recorded') or latest.get('rejected')
        return {'request_id': request_id, 'original_request_id': original_id}

    def submit(self, actor, task_id, *, resource_id, version, request_id, source=None,
               write_guard=None, runtime):
        task = self._task(actor, task_id)
        request_id = str(UUID(str(request_id)))
        if source is not None:
            original = self.conversations.task_turn_source(source['assistant_turn_id'], actor.workspace_id,
                actor.user_id or actor.member_id, task_id)
            if any(source.get(key) != original.get(key) for key in ('input_id', 'input_turn_id')):
                raise AudioIngressRejected(409, '音频导入不能切换原输入')
            snapshot = original['context'].get('target_snapshot') or {}
            if ('timeline_version_id' not in snapshot or 'timeline_sequence' not in snapshot
                    or type(snapshot['timeline_sequence']) is not int or snapshot['timeline_sequence'] < 0
                    or not (snapshot['timeline_version_id'] is None and snapshot['timeline_sequence'] == 0
                            or isinstance(snapshot['timeline_version_id'], str) and snapshot['timeline_version_id'])):
                raise AudioIngressRejected(409, '原音频导入指令缺少可核验的冻结时间线版本')
            source = {key: source[key] for key in ('assistant_turn_id', 'input_id', 'input_turn_id') if key in source}
            source.update(workspace_id=actor.workspace_id,task_id=task_id,
                member_id=actor.user_id or actor.member_id,conversation_id=original['conversation_id'],
                request_id=request_id,expected_timeline_version_id=snapshot['timeline_version_id'],
                expected_timeline_sequence=snapshot['timeline_sequence'])
        if not isinstance(resource_id,str) or not resource_id or type(version) is not int or version<1:
            raise AudioIngressRejected(422,'资源标识和固定版本无效')
        inputs = {'resource_id': resource_id, 'version': version, 'source': source,
                  'actor_identity': actor.identity_payload()}
        event_id, fresh = self.repository.execution_records.claim_edit_request(
            task, actor.user_id or actor.member_id, request_id, 'audio_resource', inputs,
            write_guard=write_guard)
        if not fresh:
            return self.read(actor, task_id, request_id)
        try:
            return self._submit_fresh(actor, task, resource_id, version, request_id, source, write_guard, runtime, event_id)
        except (AudioIngressRejected, FileNotFoundError) as exc:
            # A failure after Job commit is uncertain scheduling, never a
            # definite rejection. Preserve that original committed locator.
            if self.repository.execution_records.read_edit_request_result(task,event_id) is None:
                status = exc.status_code if isinstance(exc,AudioIngressRejected) else 404
                self.repository.execution_records.save_edit_request_result(task,event_id,status_code=status,
                    response={'status':'rejected','submission_effect':'not_submitted'})
            if isinstance(exc,FileNotFoundError):
                raise AudioIngressRejected(404,'固定音频文件不可用；请回查原请求') from exc
            raise

    def _submit_fresh(self, actor, task, resource_id, version, request_id, source, write_guard, runtime, event_id):
        task_id=task.id
        item = next((item for item in self.catalog_for().resources
                     if item.id == resource_id and item.version == version), None)
        if item is None:
            raise AudioIngressRejected(404, '固定音频资源版本不存在；请回查原请求')
        if item.preview.kind != 'audio':
            raise AudioIngressRejected(422, '此资源不是音频')
        if item.status != 'connected':
            raise AudioIngressRejected(409, '固定音频资源版本未接通；请回查原请求')
        path = self.path_for(item.id)
        if path is None:
            raise AudioIngressRejected(404, '固定音频文件不可用；请回查原请求')
        provenance = {'id': item.id, 'version': item.version,
                      'provenance': [entry.model_dump(mode='json') for entry in item.provenance]}
        with path.open('rb') as stream, self.ingress.prepare_submission(
                task, actor, 'audio_import', temporary_root=self.temporary_root,
                cpu_budget=self.cpu_budget, filename=f'{item.name}{path.suffix}', stream=stream,
                arguments={'catalog_resource': provenance, '_resource_request_id': request_id,
                           **({'_typed_source': source} if source else {})}) as values:
            run, payload = self.media.prepare_submission(actor, task_id, 'audio_import', values, 'audio_source')
            def commit(conn):
                if write_guard is not None:
                    write_guard(conn)
                self.repository.execution_records.append_task_event(TaskEvent(
                    id=event_id+'_result', workspace_id=task.workspace_id, task_id=task.id,
                    job_id=None, event_type='edit_request_result', created_at=utc_now(),
                    payload={'status_code':202, 'response':{'job_id':run.id}}), connection=conn)
            self.media.submit_prepared(runtime, actor, run, payload, write_guard=commit)
        return self.read(actor, task_id, request_id)
