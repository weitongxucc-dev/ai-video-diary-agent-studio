function createTrustedWindowContext({ getWindow, webOrigin, getApiEndpoint }) {
  function trustedFrameIdentity(frame) {
    // Electron frames can detach between property reads during navigation.
    try {
      if (!frame || frame.isDestroyed() || frame.detached
          || frame.origin !== webOrigin || new URL(frame.url).origin !== webOrigin) return null;
      const identity = { processId: frame.processId, routingId: frame.routingId, frameTreeNodeId: frame.frameTreeNodeId };
      return Object.values(identity).every(Number.isInteger) ? identity : null;
    } catch {
      return null;
    }
  }

  function sameFrame(left, right) {
    return left.processId === right.processId && left.routingId === right.routingId
      && left.frameTreeNodeId === right.frameTreeNodeId;
  }

  function currentWindow() {
    const window = getWindow();
    if (!window || window.isDestroyed()) return null;
    const contents = window.webContents;
    if (contents.isDestroyed()) return null;
    const frame = trustedFrameIdentity(contents.mainFrame);
    return frame ? { window, contents, frame } : null;
  }

  function matchesSender(event, current) {
    if (!event || !current || event.sender !== current.contents) return false;
    const senderFrame = trustedFrameIdentity(event.senderFrame);
    return Boolean(senderFrame && sameFrame(senderFrame, current.frame));
  }

  function capture({ event, errorMessage, invalidateOnSameDocument = false }) {
    const initial = currentWindow();
    if (!initial || (event !== undefined && !matchesSender(event, initial))) return null;
    const session = initial.contents.session;
    const invalidation = new AbortController();
    const invalidate = () => invalidation.abort();
    const onNavigation = (details) => {
      if (details.isMainFrame && (invalidateOnSameDocument || !details.isSameDocument)) invalidate();
    };
    initial.contents.on('did-start-navigation', onNavigation);
    initial.contents.on('render-process-gone', invalidate);
    initial.contents.on('destroyed', invalidate);

    function assertCurrent() {
      const current = currentWindow();
      if (invalidation.signal.aborted || !current || current.window !== initial.window
          || current.contents !== initial.contents || current.contents.session !== session
          || !sameFrame(current.frame, initial.frame)) {
        throw new Error(errorMessage);
      }
    }

    return {
      window: initial.window,
      signal: invalidation.signal,
      assertCurrent,
      fetchPath(requestPath, { accept, signal }) {
        assertCurrent();
        const { port, state } = getApiEndpoint();
        if (!Number.isInteger(port) || port <= 0 || state !== 'ready') {
          throw new Error('本地视频服务尚未就绪，请等待服务恢复后重试。');
        }
        const url = new URL(requestPath, webOrigin);
        if (url.origin !== webOrigin) throw new Error(errorMessage);
        return session.fetch(url.href, {
          method: 'GET', credentials: 'include', redirect: 'error', cache: 'no-store',
          headers: { Accept: accept }, ...(signal ? { signal } : {}),
        });
      },
      dispose() {
        initial.contents.removeListener('did-start-navigation', onNavigation);
        initial.contents.removeListener('render-process-gone', invalidate);
        initial.contents.removeListener('destroyed', invalidate);
      },
    };
  }

  return { checkSender: (event) => matchesSender(event, currentWindow()), capture };
}

module.exports = { createTrustedWindowContext };
