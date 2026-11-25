const { randomUUID } = require('node:crypto');
const { createTrustedWindowContext } = require('./trusted-window-context');

const sourceError = '确认来源已失效，请返回当前作品后重新确认。';
const changedError = '成员会话或候选内容已变化，请重新打开确认。';
const bindingFields = [
  'proposal_id', 'request_id', 'job_id', 'publication_id', 'section_id',
  'base_version_id', 'selection_digest', 'session_fingerprint',
];
const digestPattern = /^[a-f0-9]{64}$/;

function sameBinding(left, right) {
  return bindingFields.every((field) => left[field] === right[field]);
}

function nonemptyText(value) {
  return typeof value === 'string' && value.trim().length > 0;
}

function httpError(status) {
  if (status === 401 || status === 403) return '当前成员无法确认该候选，请核对登录和作品权限。';
  if (status === 404) return '当前作品中未找到该候选。';
  if (status === 409) return changedError;
  return '暂时无法读取候选，请在当前作品重试。';
}

function dialogDetail(candidate) {
  const evidence = candidate.knowledge_evidence.map((reference) =>
    `${reference.object_id} · ${reference.version_or_identity} · ${reference.locator}`);
  return [
    `当前段落\n${candidate.before_text}`,
    `候选段落\n${candidate.after_text}`,
    ...(evidence.length ? [`固定资料\n${evidence.join('\n')}`] : []),
    `采用后的影响\n${candidate.impact.join('\n')}`,
  ].join('\n\n');
}

function receiptFor(result, binding, taskId) {
  const operation = result?.operation;
  if (operation?.status !== 'applied' || operation.task_id !== taskId
      || operation.proposal_id !== binding.proposal_id || operation.base_version_id !== binding.base_version_id
      || !nonemptyText(operation.id) || !nonemptyText(operation.result_version_id)) return null;
  return { status: 'applied', requestId: binding.request_id,
    operationId: operation.id, resultVersionId: operation.result_version_id };
}

function createSectionCandidateConfirmation({ getWindow, webOrigin, getApiEndpoint, getTransport, showMessageBox }) {
  const trustedWindow = createTrustedWindowContext({ getWindow, webOrigin, getApiEndpoint });
  const origin = new URL(webOrigin);
  const localCookieName = `ai-video-diary-local-member-token-port-${origin.port || (origin.protocol === 'https:' ? '443' : '80')}`;
  // Keep original adopted and uncertain requests for this desktop process.
  const submitted = new Map();
  let active = false;

  function watchSession(context) {
    const cookies = context.window.webContents.session.cookies;
    const change = new AbortController();
    const onCookie = (_event, cookie) => {
      const domain = cookie.domain?.replace(/^\./, '');
      if (domain !== origin.hostname
          && !(domain && !cookie.hostOnly && origin.hostname.endsWith(`.${domain}`))) return;
      if (cookie.name === localCookieName || /^sb-.+-auth-token(?:\.\d+)?$/.test(cookie.name)) change.abort();
    };
    cookies.on('changed', onCookie);
    return { signal: change.signal, dispose: () => cookies.removeListener('changed', onCookie) };
  }

  async function readCandidate(context, requestPath, proposalId, requestId, signal) {
    const response = await context.fetchPath(requestPath, {
      accept: 'application/json', signal: AbortSignal.any([signal, AbortSignal.timeout(15_000)]),
    });
    if (response.status !== 200) return { error: httpError(response.status) };
    const value = await response.json();
    if (!value || value.proposal_id !== proposalId || value.request_id !== requestId
        || !bindingFields.every((field) => nonemptyText(value[field]))
        || !digestPattern.test(value.selection_digest) || !digestPattern.test(value.session_fingerprint)) {
      return { error: changedError };
    }
    if (value.status === 'applied') return value;
    if (value.status !== 'proposed' || !nonemptyText(value.selection_id)
        || !(Date.parse(value.expires_at) > Date.now())
        || typeof value.before_text !== 'string' || !nonemptyText(value.after_text)
        || !Array.isArray(value.impact) || !value.impact.length || !value.impact.every(nonemptyText)
        || !Array.isArray(value.knowledge_evidence) || !value.knowledge_evidence.every((reference) =>
          reference?.kind === 'knowledge' && reference.binding_status === 'fixed'
          && [reference.object_id, reference.version_or_identity, reference.locator].every(nonemptyText))) {
      return { error: '无法核对当前候选的确认摘要，请返回作品重新读取。' };
    }
    return value;
  }

  async function open(queryOnly, event, location) {
    const [taskId, proposalId] = location;
    if (location.length !== 2 || typeof taskId !== 'string' || !/^task_[a-zA-Z0-9_-]{1,80}$/.test(taskId)
        || typeof proposalId !== 'string' || !/^proposal_[a-zA-Z0-9_-]{1,120}$/.test(proposalId)) {
      return { status: 'rejected', error: '作品或候选标识无效。' };
    }
    if (!event) return { status: 'rejected', error: sourceError };
    const context = trustedWindow.capture({ event, errorMessage: sourceError, invalidateOnSameDocument: true });
    if (!context) return { status: 'rejected', error: sourceError };
    if (active) {
      context.dispose();
      return { status: 'rejected', error: '已有一项候选正在确认，请先处理当前确认。' };
    }
    active = true;
    const key = `${taskId}/${proposalId}`;
    const previous = submitted.get(key);
    const requestId = previous?.request_id ?? randomUUID();
    const requestPath = `/api/creation-tasks/${taskId}/edits/${proposalId}/section-candidate-confirmation?request_id=${requestId}`;
    let session;
    let writeDispatched = false;
    const reject = (error) => ({ status: 'rejected', error });
    const unknown = () => ({ status: 'unknown', requestId, error: '原确认结果尚未核实，请从当前候选查询原请求。' });

    const applied = (result, binding) => receiptFor(result, binding, taskId);

    try {
      if (queryOnly && !previous) return reject('未找到本次桌面确认，请查询原修改回执。');
      const transport = getTransport();
      if (!transport) return reject('本地确认服务尚未就绪，请从正式桌面应用重试。');
      session = watchSession(context);
      const signal = AbortSignal.any([context.signal, session.signal]);
      const candidate = await readCandidate(context, requestPath, proposalId, requestId, signal);
      context.assertCurrent();
      if (session.signal.aborted) return reject(changedError);
      if (candidate.error) return reject(candidate.error);
      if (previous && !sameBinding(previous, candidate)) return reject(changedError);
      if (candidate.status === 'applied') return applied(candidate.result, candidate) ?? unknown();
      if (queryOnly) return { status: 'proposed' };

      const detail = dialogDetail(candidate);
      const choice = await showMessageBox(context.window, {
        type: 'question', title: '确认采用段落候选', message: `采用段落 ${candidate.section_id} 的候选？`,
        detail, buttons: ['取消', '采用'], defaultId: 0, cancelId: 0, noLink: true, signal,
      });
      context.assertCurrent();
      if (session.signal.aborted) return reject(changedError);
      if (choice.response !== 1) return { status: 'canceled' };

      const current = await readCandidate(context, requestPath, proposalId, requestId, signal);
      context.assertCurrent();
      if (session.signal.aborted) return reject(changedError);
      if (current.error) return reject(current.error);
      if (!sameBinding(candidate, current)) return reject(changedError);
      if (current.status === 'applied') return applied(current.result, current) ?? unknown();
      if (detail !== dialogDetail(current) || !transport.isCurrent()) return reject(changedError);

      const payload = {
        request_id: requestId, expected_base_version_id: current.base_version_id,
        expected_input_digest: current.selection_digest, desktop_selection_id: current.selection_id,
      };
      submitted.set(key, Object.fromEntries(bindingFields.map((field) => [field, current[field]])));
      writeDispatched = true;
      try {
        const response = await transport.submit(taskId, proposalId, payload, signal);
        context.assertCurrent();
        if (session.signal.aborted) return unknown();
        if (response.status === 200) {
          const receipt = applied(response.value, current);
          if (receipt) return receipt;
        } else if ([403, 404, 409].includes(response.status)) {
          if (!previous && response.status !== 409) submitted.delete(key);
          return reject(httpError(response.status));
        }
      } catch {
        // Transport loss cannot distinguish an unreceived POST from a committed one.
      }

      context.assertCurrent();
      if (session.signal.aborted) return unknown();
      const recovered = await readCandidate(context, requestPath, proposalId, requestId, signal);
      context.assertCurrent();
      return !session.signal.aborted && recovered.status === 'applied' && sameBinding(current, recovered)
        ? applied(recovered.result, current) ?? unknown() : unknown();
    } catch {
      if (writeDispatched) return unknown();
      if (context.signal.aborted) return reject(sourceError);
      return reject(session?.signal.aborted ? changedError : '暂时无法核对候选，请在当前作品重试。');
    } finally {
      session?.dispose();
      context.dispose();
      active = false;
    }
  }

  return {
    confirm: (event, ...location) => open(false, event, location),
    query: (event, ...location) => open(true, event, location),
  };
}

module.exports = { createSectionCandidateConfirmation };
