import {readAgentContext, verifyTaskVersion as verifyVersion} from './trusted-context.ts';
import {createBusinessRequest} from './transport.ts';
import {typedRequestId as requestIdentity} from './request-identity.ts';
import {createToolCallScope} from './registration.ts';
import {createPreviewBudget, createExecutionGuards} from './turn-actions.ts';
import {createCapabilities} from './capabilities.ts';
import {createTaskContext} from './task-context.ts';
import {createPreferenceTools} from './preference-tools.ts';
import {registerRecipeTools} from './recipe-tools.ts';
import {registerEditControls} from './edit-controls.ts';
import {registerWorkspaceTools} from './workspace-tools.ts';
import {registerMaterialCandidates} from './material-candidates.ts';
import {registerCreationPlans} from './creation-plans.ts';
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

export function createDiaryTools(environment: Record<string, string | undefined>, declarations = readAgentContext(environment)) {
  const {apiBase, taskId, stage, selectedRefs, selectedReferences, playheadMs, selectedProfile, selectedKnowledge,
    selectedTaskVersion, readCredentials, readActionSource, readMaterialSelection,
    initialTaskRead, initialScriptDraftUpdatedAt,
    initialEditCapabilities, initialMediaScriptCapabilities, initialScriptToolCapabilities} = readAgentContext(environment);
  const verifyTaskVersion = (workspace: any) => verifyVersion(workspace, selectedTaskVersion);
  const typedRequestId = (turn: string, call: string) => requestIdentity(taskId, turn, call);
  const callScope = createToolCallScope(readActionSource);
  const {getSignal} = callScope;
  const request = createBusinessRequest({apiBase, taskId, selectedTaskVersion, verifyTaskVersion,
    readCredentials, readActionSource, getSignal, fetchRequest: (input, init) => fetch(input, init)});
  const {readTaskEditCapabilities, readMediaScriptCapabilities, readMediaScriptToolCapabilities,
    getEditCapabilities, getMediaScriptCapabilities, getScriptToolCapabilities} = createCapabilities({
      request, taskId, verifyTaskVersion, initialEditCapabilities, initialMediaScriptCapabilities, initialScriptToolCapabilities});
  const {readCurrentTaskFacts, isTaskRead, markTaskRead, getScriptDraftUpdatedAt, setScriptDraftUpdatedAt} = createTaskContext({
    request, taskId, stage, selectedRefs, playheadMs, verifyTaskVersion, initialTaskRead, initialScriptDraftUpdatedAt});
  const preferences = createPreferenceTools({request, readActionSource});
  const {hasPreview, markPreview} = createPreviewBudget();

  function register(agent: ToolRegistrar) {
    const pi = callScope.bind(agent);
    registerReferenceRead(pi,request,selectedReferences);
    const {hasCreationAction, markCreationAction, hasEditControlWrite, markEditControlWrite} = createExecutionGuards();
    registerRecipeTools(pi, {request, taskId, selectedTaskVersion, isTaskRead, readActionSource,
      typedRequestId, getScriptToolCapabilities, getDeclaredCapabilities: () => declarations.initialScriptToolCapabilities, readMediaScriptToolCapabilities, markPreview});
    registerEditControls(pi, {request, taskId, selectedTaskVersion, isTaskRead, verifyTaskVersion, readActionSource,
      hasPreview, markPreview, hasCreationAction, hasEditControlWrite, markEditControlWrite});
    registerWorkspaceTools(pi, {request, taskId, stage, selectedRefs, selectedProfile, selectedKnowledge,
      isTaskRead, verifyTaskVersion, getEditCapabilities, readMaterialSelection});
    preferences.register(pi);
    registerMaterialCandidates(pi, {request});
    registerCreationPlans(pi, {request, readActionSource, hasPreview, markPreview});
    registerTaskReads(pi, {request, taskId, readCurrentTaskFacts, readTaskEditCapabilities, markTaskRead, verifyTaskVersion});
    if (taskId) {
      registerDirectEdit(pi,{request,taskId,selectedTaskVersion,isTaskRead,readActionSource,typedRequestId,
        getEditCapabilities,getDeclaredCapabilities:()=>declarations.initialEditCapabilities,
        hasCreationAction,hasEditControlWrite,markEditControlWrite});
      const typedEdits = createTypedEditTools({request, taskId, selectedTaskVersion, isTaskRead, readActionSource,
        typedRequestId, getEditCapabilities, getDeclaredCapabilities: () => declarations.initialEditCapabilities, hasCreationAction, hasEditControlWrite, markPreview});
      registerObservationTools(pi, {request, taskId, verifyTaskVersion, readActionSource, typedRequestId});
      registerPreviewJobReader(pi, {request, taskId, getSignal, onPreviewJobObserved: typedEdits.onPreviewJobObserved});
      typedEdits.registerRequestReader(pi);
      registerMediaScriptTools(pi, {request, taskId, selectedTaskVersion, readMediaScriptCapabilities,
        getMediaScriptCapabilities, getDeclaredCapabilities: () => declarations.initialMediaScriptCapabilities, isTaskRead, readActionSource, typedRequestId, hasCreationAction, hasEditControlWrite, markPreview});
      typedEdits.registerPreview(pi);
    }
    registerTopicTools(pi, {request, taskId, stage, isTaskRead, hasPreview, markPreview});
    registerScriptTools(pi, {request, taskId, stage, isTaskRead, hasPreview, markPreview, getScriptDraftUpdatedAt, setScriptDraftUpdatedAt});
    registerExecutionTools(pi, {request, taskId, stage, isTaskRead, hasCreationAction, markCreationAction,
      hasEditControlWrite, hasPreview, readActionSource, getSignal});
    return registerControlledCodemode(pi);
  }
  return {register, typedRequestId, readTaskEditCapabilities, readMediaScriptCapabilities, readMediaScriptToolCapabilities,
    readCurrentTaskFacts, readCreatorMemoryFacts: preferences.readCreatorMemoryFacts};
}
