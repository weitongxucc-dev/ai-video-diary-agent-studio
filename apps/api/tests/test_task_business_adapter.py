from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.assistant.task_business import TaskBusiness
from app.assistant.task_business_adapter import TaskBusinessAdapter
from app.assistant.task_business_errors import TaskBusinessRejected, TaskInvocation, TaskOperationRejected


OPTIONS=dict(call_id='call',tool_call_id='tool',source_binding={'turn_id':'turn','session_id':'sdk','input_id':'input'},
    cancel_event=Event(),deadline_monotonic=float('inf'))


def application():
    business=object.__new__(TaskBusiness)
    business.check=lambda *args,**kwargs:(None,SimpleNamespace(id='task'),{},{'input_id':'input'},None)
    business.runtime=lambda:None
    return business


def test_application_returns_domain_value_while_only_host_encodes_the_sdk_result():
    @dataclass
    class Result:
        id: object
        updated_at: datetime
    value=Result(uuid4(),datetime(2026,10,25,tzinfo=timezone.utc))
    business=application()
    business.execute=lambda *args:value
    invocation=TaskInvocation()
    assert business.invoke('read_task_workspace',{},invocation=invocation,**OPTIONS) is value
    assert invocation.owner_entered
    encoded=TaskBusinessAdapter(business)('read_task_workspace',{},**OPTIONS)
    assert encoded=={'ok':True,'value':{'id':str(value.id),'updated_at':value.updated_at.isoformat()}}
    assert not callable(business) and not hasattr(business,'native_call')


@pytest.mark.parametrize('native',[False,True])
@pytest.mark.parametrize('when',['before','commit'])
def test_host_preserves_revocation_guard_and_does_not_claim_unsubmitted_after_owner_entry(native,when):
    business=application()
    revoked=when=='before'
    writes=[]
    def check(*args,**kwargs):
        if revoked: raise TaskBusinessRejected('source changed',kind='conflict')
        return None,SimpleNamespace(id='task'),{},{'input_id':'input'},None
    def owner(guard):
        nonlocal revoked
        revoked=True
        guard(None)
        writes.append('must not commit')
    business.check=check
    business.execute=lambda *args:owner(args[-1])
    business.native_case=SimpleNamespace(dispatch=lambda *args,write_guard,**kwargs:owner(write_guard))
    host=TaskBusinessAdapter(business)
    result=(host.native_call if native else host)('read_task_workspace',{},**OPTIONS)
    assert result['ok'] is False and result['failure']['status_code']==409
    assert not writes
    if when=='before': assert result['failure']['submission_effect']=='not_submitted'
    else: assert result['failure'].get('submission_effect')!='not_submitted'


@pytest.mark.parametrize('kind,status',[('forbidden',403),('invalid_input',422),('not_found',404),('conflict',409),('unavailable',503)])
def test_semantic_failure_has_no_http_status_until_host_mapping(kind,status):
    business=application()
    error=TaskOperationRejected('rejected',kind=kind)
    def execute(*args): raise error
    business.execute=execute
    with pytest.raises(TaskOperationRejected) as raised:
        business.invoke('read_task_workspace',{},invocation=TaskInvocation(),**OPTIONS)
    assert raised.value is error and not hasattr(error,'status_code')
    result=TaskBusinessAdapter(business)('read_task_workspace',{},**OPTIONS)
    assert result['failure']['status_code']==status
