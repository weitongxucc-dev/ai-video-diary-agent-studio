import {readAgentContext, verifyTaskVersion as verifyVersion} from './trusted-context.ts';
import {AsyncLocalStorage} from 'node:async_hooks';
import {createBusinessRequest, BusinessRequestError, BusinessResultUnknown, RequestNotSubmitted, safeBusinessFailure, safeFailureText, credentialSecrets} from './transport.ts';
import type {BusinessFailure, HostBusinessCall} from './transport.ts';
import {typedRequestId as requestIdentity} from './request-identity.ts';
import {createToolCallScope} from './registration.ts';
import {createPreviewBudget, createExecutionGuards} from './turn-actions.ts';
import {createCapabilities} from './capabilities.ts';
import {createTaskContext} from './task-context.ts';
import {createPreferenceTools} from './preference-tools.ts';
import {registerRecipeTools} from './recipe-tools.ts';
import {registerEditControls} from './edit-controls.ts';
import {registerWorkspaceTools} from './workspace-tools.ts';
import {registerVisualTemplateTools} from './visual-template-tools.ts';
import {registerMaterialCandidates} from './material-candidates.ts';
import {registerCreationPlans} from './creation-plans.ts';
import {registerSourceTasks} from './source-tasks.ts';
import {registerTaskReads} from './task-reads.ts';
import {registerObservationTools} from './observation-tools.ts';
import {registerPreviewJobReader} from './preview-job-reader.ts';
import {registerMediaScriptTools} from './media-script-tools.ts';
import {createTypedEditTools} from './typed-edit-tools.ts';
import {registerDirectEdit} from './direct-edit-tools.ts';
import {registerReferenceRead} from './reference-reads.ts';
import {registerTopicTools} from './topic-tools.ts';
import {registerScriptTools} from './script-tools.ts';
import {registerExecutionTools} from './execution-tools.ts';
import {registerControlledCodemode} from '../controlled_codemode.ts';
import type {ToolRegistrar} from './contracts.ts';

export function createDiaryTools(environment: Record<string, string | undefined>, declarations = readAgentContext(environment), businessCall?: HostBusinessCall) {
  const {apiBase, taskId, stage, selectedRefs, selectedReferences, playheadMs, selectedProfile, selectedKnowledge,
    selectedTaskVersion, readCredentials, readActionSource, readMaterialSelection,
    initialTaskRead, initialScriptDraftUpdatedAt,
    initialEditCapabilities, initialMediaScriptCapabilities, initialScriptToolCapabilities} = readAgentContext(environment);
  const verifyTaskVersion = (workspace: any) => verifyVersion(workspace, selectedTaskVersion, Boolean(businessCall));
  const typedRequestId = (turn: string, call: string) => requestIdentity(taskId, turn, call);
  const callScope = createToolCallScope(readActionSource);
  const failures = new AsyncLocalStorage<{first?: BusinessFailure}>();
  const {getSignal, getToolCallId} = callScope;
  const request = createBusinessRequest({apiBase, taskId, selectedTaskVersion, verifyTaskVersion,
    readCredentials, readActionSource, getSignal,
    getToolCallId: environment.AI_VIDEO_DIARY_AGENT_HOST_PROTOCOL === "phase-v1" ? getToolCallId : undefined,
    fetchRequest: (input, init) => fetch(input, init), businessCall,
    onFailure: failure => {
      const call = failures.getStore();
      if (call && !call.first) call.first = failure;
    }});
  const {readTaskEditCapabilities, readMediaScriptCapabilities, readMediaScriptToolCapabilities,
    getEditCapabilities, getMediaScriptCapabilities, getScriptToolCapabilities} = createCapabilities({
      request, taskId, verifyTaskVersion, initialEditCapabilities, initialMediaScriptCapabilities, initialScriptToolCapabilities});
  const {readCurrentTaskFacts, isTaskRead, markTaskRead, getScriptDraftUpdatedAt, setScriptDraftUpdatedAt} = createTaskContext({
    request, taskId, stage, selectedRefs, playheadMs, verifyTaskVersion, initialTaskRead, initialScriptDraftUpdatedAt});
  const preferences = createPreferenceTools({request, readActionSource});
  const {hasPreview, markPreview} = createPreviewBudget();

  function register(agent: ToolRegistrar) {
    const scoped = callScope.bind(agent);
    const pi = new Proxy(scoped, {get(target, property) {
      if (property !== "registerTool") return Reflect.get(target, property);
      return (tool: any) => target.registerTool({...tool, execute: (...args: any[]) => failures.run({}, async () => {
        let result;
        try { result = await tool.execute(...args); }
        catch (error) {
          const notSubmitted = error instanceof RequestNotSubmitted;
          const secrets = credentialSecrets(readCredentials());
          const original = error instanceof BusinessRequestError || error instanceof BusinessResultUnknown || error instanceof RequestNotSubmitted
            ? safeBusinessFailure(error.businessFailure,secrets) : {};
          const failure = safeBusinessFailure({task_id: taskId, request_id: readActionSource().creationRequestId,
            error: notSubmitted ? "request_not_submitted" : "business_tool_failed", stage: "tool",
            submission_effect: notSubmitted ? "not_submitted" : error instanceof BusinessRequestError ? "rejected" : "unknown",
            reason: error instanceof Error ? safeFailureText(error.message, secrets) : "业务工具未完成，尚未确认原请求结果。",
            repair_action: "先读取原请求及当前对象状态，核实结果后再决定后续动作；不要盲重试。",...original}, secrets);
          result = {isError: true, content: [{type: "text", text: JSON.stringify({failure})}], details: {failure}};
        }
        const receipt = failures.getStore()?.first;
        if (!result.isError) return result;
        const original = result.details?.failure;
        const scriptFailure = receipt && ["script_review_invalid", "script_fact_unsupported", "script_goal_conflict"].includes(receipt.error);
        if (!receipt && !original) return result;
        // Some existing tools catch HTTP errors themselves. Preserve the transport
        // receipt on that same failure. Safe existing failures keep their contract.
        const secrets = credentialSecrets(readCredentials());
        const failure = receipt && !scriptFailure
          ? {...safeBusinessFailure(result.details, secrets), ...safeBusinessFailure(original, secrets), ...receipt}
          : safeBusinessFailure(original, secrets);
        // A failed follow-up read cannot prove that the preceding write was not
        // submitted. Keep the conservative operation effect and original locator.
        if (original?.submission_effect === 'unknown' && receipt
            && ['not_submitted','rejected'].includes(receipt.submission_effect)) {
          const operationFailure = safeBusinessFailure(original, secrets);
          Object.assign(failure, Object.fromEntries((['submission_effect','request_id','query_url','repair_action','impact'] as const)
            .flatMap(key => operationFailure[key] === undefined ? [] : [[key, operationFailure[key]]])));
        }
        if (original && Object.keys(original).length === Object.keys(failure).length
            && Object.entries(failure).every(([key, value]) => original[key] === value)) return result;
        const details = {...result.details, failure};
        return {...result, details, content: [{type: "text", text: JSON.stringify(details)}]};
      })});
    }});
    registerReferenceRead(pi,request,selectedReferences);
    const {hasCreationAction, markCreationAction, hasEditControlWrite, markEditControlWrite, releaseNotSubmittedEditControl, hasHistoryControlWrite, markDirectEditAttempt, verifyDirectEdit, directEditUnverified} = createExecutionGuards();
    registerRecipeTools(pi, {request, taskId, selectedTaskVersion, isTaskRead, readActionSource,
      typedRequestId, getScriptToolCapabilities, getDeclaredCapabilities: () => declarations.initialScriptToolCapabilities, readMediaScriptToolCapabilities, markPreview});
    registerEditControls(pi, {request, taskId, selectedTaskVersion, isTaskRead, verifyTaskVersion, readActionSource,
      hasPreview, markPreview, hasCreationAction, hasEditControlWrite, markEditControlWrite, releaseNotSubmittedEditControl, markTaskRead, typedRequestId});
    registerWorkspaceTools(pi, {request, taskId, stage, selectedRefs, selectedProfile, selectedKnowledge,
      isTaskRead, verifyTaskVersion, getEditCapabilities, readMaterialSelection, readActionSource});
    registerVisualTemplateTools(pi,{request,readActionSource,typedRequestId});
    preferences.register(pi);
    registerMaterialCandidates(pi, {request});
    registerCreationPlans(pi, {request, readActionSource, hasPreview, markPreview});
    registerSourceTasks(pi, {request, readActionSource, taskId, hasPreview, markPreview});
    registerTaskReads(pi, {request, taskId, readCurrentTaskFacts, readTaskEditCapabilities, markTaskRead, verifyTaskVersion});
    if (taskId) {
      registerDirectEdit(pi,{request,taskId,selectedTaskVersion,isTaskRead,readActionSource,typedRequestId,
        getEditCapabilities,getDeclaredCapabilities:()=>declarations.initialEditCapabilities,
        hasCreationAction,hasEditControlWrite,markDirectEditAttempt,verifyDirectEdit});
      const typedEdits = createTypedEditTools({request, taskId, selectedTaskVersion, isTaskRead, readActionSource,
        typedRequestId, getEditCapabilities, getDeclaredCapabilities: () => declarations.initialEditCapabilities, hasCreationAction, hasEditControlWrite, markPreview});
      registerObservationTools(pi, {request, taskId, verifyTaskVersion, readActionSource, typedRequestId});
      registerPreviewJobReader(pi, {request, taskId, getSignal, onPreviewJobObserved: typedEdits.onPreviewJobObserved});
      typedEdits.registerRequestReader(pi);
      typedEdits.registerNarrationRevision(pi);
      registerMediaScriptTools(pi, {request, taskId, selectedTaskVersion, readMediaScriptCapabilities,
        getMediaScriptCapabilities, getDeclaredCapabilities: () => declarations.initialMediaScriptCapabilities, isTaskRead, readActionSource, typedRequestId, hasCreationAction, hasEditControlWrite, markPreview});
      typedEdits.registerPreview(pi);
    }
    registerTopicTools(pi, {request, taskId, readActionSource, verifyTaskVersion, isTaskRead, hasPreview, markPreview});
    registerScriptTools(pi, {request, taskId, readActionSource, readCurrentTaskFacts, isTaskRead, hasPreview, markPreview, getScriptDraftUpdatedAt, setScriptDraftUpdatedAt});
    registerExecutionTools(pi, {request, taskId, selectedTaskVersion, isTaskRead, hasCreationAction, markCreationAction,
      hasEditControlWrite: hasHistoryControlWrite, directEditUnverified, hasPreview, readActionSource, getSignal,
      readCurrentTaskFacts, typedRequestId});
    return registerControlledCodemode(pi);
  }
  return {register, typedRequestId, readTaskEditCapabilities, readMediaScriptCapabilities, readMediaScriptToolCapabilities,
    readCurrentTaskFacts, readCreatorMemoryFacts: preferences.readCreatorMemoryFacts};
}
