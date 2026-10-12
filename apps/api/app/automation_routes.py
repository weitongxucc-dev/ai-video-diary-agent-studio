"""Authenticated automation endpoints wired with explicit canonical callbacks."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from app.automation_service import AutomationBlocked, AutomationCreate


class RevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)


class AutomationRevise(AutomationCreate):
    expected_revision: int = Field(ge=1)


class PauseRequest(RevisionRequest):
    paused: bool


class PlanConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preflight_fingerprint: str = Field(min_length=64,max_length=64)


def build_automation_router(runtime_for, *, actor_for):
    router = APIRouter(prefix="/api/automations",tags=["automations"])

    def invoke(operation):
        try: return operation()
        except AutomationBlocked as exc:
            raise HTTPException(409,detail={"error":exc.code,"message":exc.reason,"repair_action":exc.repair}) from exc
        except KeyError as exc: raise HTTPException(404,detail="当前成员下未找到该自动化对象") from exc
        except ValueError as exc: raise HTTPException(409,detail=str(exc)) from exc

    def context(request):
        return runtime_for(),actor_for(request)

    @router.get("/options")
    def options(request:Request):
        runtime,actor=context(request)
        return invoke(lambda:runtime.options(actor))

    @router.get("")
    def list_automations(request:Request):
        runtime,actor=context(request)
        items=invoke(lambda:runtime.service.list(actor))
        return {"items":items,"total_count":len(items)}

    @router.post("")
    def create(request:Request,payload:AutomationCreate):
        runtime,actor=context(request)
        return invoke(lambda:runtime.service.create(actor,payload))

    @router.put("/{automation_id}")
    def revise(automation_id:str,request:Request,payload:AutomationRevise):
        runtime,actor=context(request)
        body=AutomationCreate.model_validate(payload.model_dump(exclude={"expected_revision"}))
        return invoke(lambda:runtime.service.revise(actor,automation_id,body,payload.expected_revision))

    @router.patch("/{automation_id}/pause")
    def pause(automation_id:str,request:Request,payload:PauseRequest):
        runtime,actor=context(request)
        return invoke(lambda:runtime.service.pause(actor,automation_id,payload.paused,payload.expected_revision))

    @router.get("/{automation_id}/runs")
    def runs(automation_id:str,request:Request):
        runtime,actor=context(request)
        items=invoke(lambda:runtime.service.runs(actor,automation_id))
        return {"items":items,"total_count":len(items)}

    @router.post("/runs/{run_id}/retry")
    def retry(run_id:str,request:Request):
        runtime,actor=context(request)
        return invoke(lambda:runtime.service.retry(actor,run_id))

    @router.post("/runs/{run_id}/sync-sources")
    def sync_sources(run_id:str,request:Request):
        runtime,actor=context(request)
        return invoke(lambda:runtime.sync_sources(actor,run_id))

    @router.post("/runs/{run_id}/preview")
    def preview(run_id:str,request:Request):
        runtime,actor=context(request)
        return invoke(lambda:runtime.preview(request,actor,run_id))

    @router.post("/runs/{run_id}/confirm")
    def confirm(run_id:str,request:Request,payload:PlanConfirmRequest):
        runtime,actor=context(request)
        return invoke(lambda:runtime.confirm(request,actor,run_id,payload.preflight_fingerprint))

    return router


def guard_automation_task(runtime, workspace_id, task_id, actor_for):
    try: runtime.require_task_open(workspace_id,task_id,actor_for)
    except AutomationBlocked as exc:
        raise HTTPException(409,detail={"error":exc.code,"message":exc.reason,"repair_action":exc.repair}) from exc
    except KeyError as exc: raise HTTPException(403,detail="当前成员不是本次自动化运行的原成员") from exc


def build_automation_guarded_publish_router(router, *, guard):
    def before_execution(request:Request):
        if request.method == "POST" and "task_id" in request.path_params:
            guard(request,request.path_params["task_id"])
    wrapped = APIRouter(dependencies=[Depends(before_execution)])
    wrapped.include_router(router)
    return wrapped
