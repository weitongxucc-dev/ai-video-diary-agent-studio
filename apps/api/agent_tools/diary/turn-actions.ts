// Only the existing cross-tool turn restrictions live here. Individual tool
// attempts, receipts, CAS versions and capabilities belong to their own modules.
export function createPreviewBudget() {
  let previewCreated = false;
  return {hasPreview: () => previewCreated, markPreview: () => {previewCreated = true;}};
}
export function createExecutionGuards() {
  let creationActionApplied = false;
  let editControlWriteAttempted = false;
  let directEditAttempted = false;
  let directEditVerified = false;
  return {hasCreationAction: () => creationActionApplied, markCreationAction: () => {creationActionApplied = true;},
    hasEditControlWrite: () => editControlWriteAttempted || directEditAttempted,
    hasHistoryControlWrite: () => editControlWriteAttempted,
    markEditControlWrite: () => {editControlWriteAttempted = true;},
    markDirectEditAttempt: () => {directEditAttempted = true;},
    verifyDirectEdit: () => {directEditVerified = true;},
    directEditUnverified: () => directEditAttempted && !directEditVerified};
}
