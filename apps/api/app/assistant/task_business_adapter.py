"""Pi host envelopes for the task business application and native owner calls."""
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError

from app.assistant.task_business_errors import TaskBusinessRejected, TaskOperationRejected, TaskInvocation
from app.assistant.business_failure import _safe_business_failure
from app.assistant_goal_continuation import GoalProjectionRejected
from app.creation.script_draft_use_case import ScriptDraftRejected
from app.creation.execution_control import ExecutionControlRejected
from app.creation.output_observation_use_case import OutputObservationRejected
from app.creation.editing.metadata_use_case import EditPreviewRejected

_STATUS={'forbidden':403,'invalid_input':422,'not_found':404,'conflict':409,'unavailable':503}


def _status(error):
    return _STATUS[error.kind] if isinstance(error,(TaskBusinessRejected,TaskOperationRejected)) else error.status_code


class TaskBusinessAdapter:
    def __init__(self, business):
        self.business=business

    def __call__(self, operation, arguments, **options):
        invocation=TaskInvocation()
        try:
            value=self.business.invoke(operation,arguments,invocation=invocation,**options)
            return {'ok':True,'value':jsonable_encoder(value)}
        except (TaskBusinessRejected,TaskOperationRejected,ScriptDraftRejected,ExecutionControlRejected,
                OutputObservationRejected,EditPreviewRejected,GoalProjectionRejected,HTTPException) as exc:
            failure={'status_code':_status(exc),**(_safe_business_failure(exc.detail) if isinstance(exc.detail,dict)
                else {'reason':str(exc.detail)})}
            if not invocation.owner_entered and 'submission_effect' not in failure:
                failure['submission_effect']='not_submitted'
            return {'ok':False,'failure':failure}
        except (KeyError,ValueError,PermissionError,ValidationError) as exc:
            return {'ok':False,'failure':{'status_code':403 if isinstance(exc,PermissionError) else 409,'reason':str(exc)}}

    def native_call(self, operation, arguments, **options):
        invocation=TaskInvocation()
        try:
            value=self.business.invoke_native(operation,arguments,invocation=invocation,**options)
            return {'ok':True,'value':jsonable_encoder(value)}
        except (TaskBusinessRejected,TaskOperationRejected,HTTPException) as exc:
            return {'ok':False,'failure':{'status_code':_status(exc),'reason':str(exc.detail),
                'submission_effect':'unknown' if invocation.owner_entered else 'not_submitted'}}
        except (KeyError,ValueError,PermissionError) as exc:
            return {'ok':False,'failure':{'status_code':403 if isinstance(exc,PermissionError) else 409,'reason':str(exc),
                'submission_effect':'unknown' if invocation.owner_entered else 'not_submitted'}}
