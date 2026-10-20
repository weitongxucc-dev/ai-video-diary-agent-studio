const fs = require('fs');
const path = require('path');
const http = require('http');
const { randomBytes, randomUUID } = require('crypto');
const { performance } = require('perf_hooks');

function createServiceSupervisor({ isDev, webPort, resourcesPath, apiEnvironment, webEnvironment, spawnApi, forkWeb, recordLifecycle, attachChildOutput, onFailure }) {
  let apiProcess = null;
  let nextProcess = null;
  let nextInstance = null;
  let apiPort = 0;
  let nextPort = 0;
  let apiStopping = false;
  let apiControlToken = null;
  let stopPromise = null;
  const serviceStates = {
    api: { label: '本地 API 服务', state: 'not-started', error: null },
    web: { label: '本地 Web 服务', state: 'not-started', error: null },
  };
  function setServiceState(name, state, error = null) {
    serviceStates[name].state = state;
    serviceStates[name].error = error ? error.message : null;
    recordLifecycle('service-state', {
      service: name,
      state,
      errorCode: error && (error.code || error.name) ? (error.code || error.name) : null,
    });
    console.log(`[main] ${serviceStates[name].label}: ${state}${error ? ` (${error.message})` : ''}`);
  }

  function requiredPackagedFile(label, candidates, mode) {
    for (const candidate of candidates) {
      try {
        fs.accessSync(candidate, mode);
        return candidate;
      } catch (_) { /* try the next package location */ }
    }
    throw new Error(`${label} 缺失或不可用。请重新安装应用；不要从未完整解压的安装包中直接运行。`);
  }

  function reportServiceFailure(name, error) {
    setServiceState(name, 'failed', error);
    recordLifecycle('service-failure', { service: name, errorCode: error.code || error.name || 'unknown' });
    onFailure(name, error, serviceStates[name].label);
  }

  function findApiBinary() {
    if (isDev) return null;
    return requiredPackagedFile('打包的 API 服务', [
      path.join(resourcesPath, 'api-server'),
    ], fs.constants.X_OK);
  }

  function getFreePort() {
    return new Promise((resolve, reject) => {
      const server = require('net').createServer();
      server.listen(0, '127.0.0.1', () => {
        const port = server.address().port;
        server.close(() => resolve(port));
      });
      server.on('error', reject);
    });
  }

  function ensurePortAvailable(port) {
    return new Promise((resolve, reject) => {
      const server = require('net').createServer();
      server.once('error', (error) => {
        reject(new Error(error.code === 'EADDRINUSE'
          ? `本地 Web 端口 ${port} 已被占用，本次未启动 Web 服务。`
          : `本地 Web 端口 ${port} 无法绑定（${error.code || error.message}）。`));
      });
      server.listen(port, '127.0.0.1', () => {
        server.close(resolve);
      });
    });
  }

  async function startApiServer() {
    if (apiProcess) return apiPort;

    setServiceState('api', 'starting');

    if (isDev) {
      apiPort = 8000;
      await waitForApi(apiPort);
      setServiceState('api', 'ready');
      return apiPort;
    }

    const binary = findApiBinary();
    apiPort = await getFreePort();
    console.log(`[main] Starting API server on port ${apiPort}...`);

    apiControlToken = randomBytes(32).toString('hex');
    stopPromise = null;
    apiStopping = false;
    const env = { ...apiEnvironment(apiPort), YAOHE_DESKTOP_CONTROL_TOKEN: apiControlToken };

    apiProcess = spawnApi(binary, [], {
      env,
      stdio: ['ignore', 'pipe', 'pipe'],
      detached: process.platform !== 'win32',
    });

    apiProcess.on('error', (err) => {
      console.error(`[main] Failed to spawn API server: ${err.message}`);
      apiProcess = null;
      reportServiceFailure('api', new Error(`无法启动：${err.message}`));
    });

    attachChildOutput('api', apiProcess.stdout, 'api', (message) => console.log(message));
    attachChildOutput('api', apiProcess.stderr, 'api:err', (message) => console.error(message));

    apiProcess.on('exit', (code, signal) => {
      console.log(`[main] API server exited (code=${code}, signal=${signal})`);
      const pid = apiProcess ? apiProcess.pid : null;
      recordLifecycle('service-exit', { service: 'api', code, signal, expected: apiStopping });
      apiProcess = null;
      if (!apiStopping) {
        if (pid) terminateExitedServiceGroup(pid);
        reportServiceFailure('api', new Error(`进程已退出（code=${code}, signal=${signal}）`));
      }
    });

    await waitForApi(apiPort);
    setServiceState('api', 'ready');
    return apiPort;
  }

  function waitForApi(port, maxRetries = 60) {
    return waitForHttpService({
      service: 'api',
      port,
      // Process readiness must work before a user/workspace can be selected.
      // Capability diagnostics remain behind the authenticated runtime API.
      path: '/api/version',
      maxRetries,
      ready: (statusCode) => statusCode === 200,
      label: 'API server',
    });
  }

  function waitForNext(port, maxRetries = 30) {
    return waitForHttpService({
      service: 'web',
      port,
      path: '/',
      maxRetries,
      ready: (statusCode) => statusCode < 500,
      label: 'Next.js server',
    });
  }

  function waitForHttpService({ service, port, path: requestPath, maxRetries, ready, label }) {
    return new Promise((resolve, reject) => {
      let attempts = 0;
      let settled = false;
      let retryTimer = null;
      let activeRequest = null;

      const finish = (error) => {
        if (settled) return;
        settled = true;
        if (retryTimer) clearTimeout(retryTimer);
        if (activeRequest) activeRequest.destroy();
        if (error) reject(error);
        else resolve();
      };

      const scheduleRetry = () => {
        if (settled || retryTimer) return;
        if (attempts >= maxRetries) {
          finish(new Error(`${label} did not become ready`));
          return;
        }
        retryTimer = setTimeout(() => {
          retryTimer = null;
          check();
        }, 1000);
      };

      const check = () => {
        if (settled) return;
        if (serviceStates[service].state === 'failed') {
          finish(new Error(serviceStates[service].error || `${label} 进程提前退出`));
          return;
        }
        attempts++;
        const req = http.get(`http://127.0.0.1:${port}${requestPath}`, (res) => {
          activeRequest = null;
          res.resume();
          if (settled) return;
          if (ready(res.statusCode)) {
            console.log(`[main] ${label} ready on port ${port}`);
            finish();
            return;
          }
          scheduleRetry();
        });
        activeRequest = req;
        req.once('error', () => {
          activeRequest = null;
          scheduleRetry();
        });
        req.setTimeout(2000, () => {
          req.destroy();
        });
      };
      check();
    });
  }

  // ── Next.js Server Management ───────────────────────────
  function findNextServer() {
    if (isDev) return null;
    return requiredPackagedFile('打包的 Web 服务', [
      path.join(resourcesPath, 'web', 'server.js'),
    ], fs.constants.R_OK);
  }

  async function startNextServer() {
    if (nextProcess) return nextPort;

    setServiceState('web', 'starting');

    if (isDev) {
      nextPort = webPort;
      await waitForNext(nextPort);
      setServiceState('web', 'ready');
      return nextPort;
    }

    const serverJs = findNextServer();
    await ensurePortAvailable(webPort);
    nextPort = webPort;
    console.log(`[main] Starting Next.js server on port ${nextPort}...`);

    const env = { ...webEnvironment(nextPort, apiPort) };
    delete env.YAOHE_DESKTOP_CONTROL_TOKEN;

    nextProcess = forkWeb(serverJs, [], {
      env,
      stdio: 'pipe',
      serviceName: 'Yaohe Web',
    });
    const child = nextProcess;
    const instance = { child, exited: false, code: null, failed: false, stopRequested: false, stopSent: false, expected: false };
    nextInstance = instance;

    child.on('error', (err) => {
      instance.failed = true;
      console.error(`[main] Web utility process failed: ${err.type}`);
      if (nextProcess === child) nextProcess = null;
      reportServiceFailure('web', new Error(`后台网页服务异常（${err.type}）`));
    });

    attachChildOutput('web', child.stdout, 'next', (message) => console.log(message));
    attachChildOutput('web', child.stderr, 'next:err', (message) => console.error(message));

    child.on('exit', (code) => {
      instance.exited = true;
      instance.code = code;
      instance.expected = instance.stopRequested;
      console.log(`[main] Web utility process exited (code=${code})`);
      recordLifecycle('service-exit', { service: 'web', code, signal: null, expected: instance.expected });
      if (nextProcess === child) nextProcess = null;
      if (!instance.expected) {
        reportServiceFailure('web', new Error(`进程已退出（code=${code}）`));
      }
    });

    await waitForNext(nextPort);
    setServiceState('web', 'ready');
    return nextPort;
  }

  async function waitUntil(promise, deadline) {
    let timer;
    try {
      return await Promise.race([
        promise.then(value => ({ completed: performance.now() < deadline, value })),
        new Promise(resolve => { timer = setTimeout(() => resolve({ completed: false }), Math.max(0, deadline - performance.now())); }),
      ]);
    } finally { clearTimeout(timer); }
  }

  async function requestApiShutdown(child, exitRequestId, deadlineAtUtc, deadline) {
    if (!child) return { confirmed: isDev || serviceStates.api.state === 'not-started', reason: 'not-owned' };
    apiStopping = true;
    const body = JSON.stringify({ exit_request_id: exitRequestId, deadline_at_utc: deadlineAtUtc });
    let request;
    const response = new Promise(resolve => {
      request = http.request({ hostname: '127.0.0.1', port: apiPort, path: '/internal/desktop-shutdown', method: 'POST',
        headers: { 'X-Yaohe-Desktop-Control': apiControlToken, 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) } }, res => {
        res.on('error', () => resolve({ confirmed: false, reason: 'transport-failed' }));
        if (res.statusCode !== 200) {
          res.resume();
          resolve({ confirmed: false, reason: 'http-failed', httpStatus: res.statusCode });
          return;
        }
        const chunks = [];
        let size = 0;
        res.on('data', chunk => {
          size += chunk.length;
          if (size > 4096) {
            resolve({ confirmed: false, reason: 'invalid-receipt' });
            request.destroy();
          } else chunks.push(chunk);
        });
        res.on('end', () => {
          let value;
          try { value = JSON.parse(Buffer.concat(chunks).toString('utf8')); }
          catch (_) { resolve({ confirmed: false, reason: 'invalid-receipt' }); return; }
          const checks = ['dispatch_stopped', 'executors_notified', 'processes_released', 'checkpoints_saved'];
          if (!value || value.exit_request_id !== exitRequestId || !['ready_to_exit', 'unconfirmed'].includes(value.status)
              || !value.checks || Object.keys(value.checks).length !== checks.length || checks.some(key => typeof value.checks[key] !== 'boolean')
              || !Array.isArray(value.pending) || value.pending.some(item => typeof item !== 'string')) {
            resolve({ confirmed: false, reason: 'invalid-receipt' });
            return;
          }
          const confirmed = value.status === 'ready_to_exit' && checks.every(key => value.checks[key]) && value.pending.length === 0;
          resolve({ confirmed, reason: confirmed ? 'ready_to_exit' : 'unconfirmed',
            pendingChecks: checks.filter(key => !value.checks[key]), pendingCount: value.pending.length });
        });
      });
      request.on('error', () => resolve({ confirmed: false, reason: 'transport-failed' }));
      request.end(body);
    });
    const outcome = await waitUntil(response, deadline);
    request.destroy();
    return outcome.completed ? outcome.value : { confirmed: false, reason: 'deadline-exhausted' };
  }

  async function stopService(name, child, setStopping, deadline, utilityExit = null) {
    if (!child) return { exited: true, forced: false, normal: true, owned: false };
    const utility = utilityExit !== null;
    const pid = child.pid;
    let observedExit = utility ? utilityExit.exited : false;
    let code = utility ? utilityExit.code : child.exitCode ?? null;
    let signal = child.signalCode ?? null;
    const isRunning = () => !observedExit && (utility ? child.pid !== undefined : child.exitCode === null && child.signalCode === null);
    if (!isRunning()) {
      const normal = !utility && performance.now() < deadline && code === 0;
      return { exited: utility ? observedExit : true, forced: false, normal, owned: true, code, signal };
    }
    setStopping(true);
    console.log(`[main] Stopping ${serviceStates[name].label} (pid=${child.pid})...`);

    const exited = new Promise(resolve => child.once('exit', (exitCode, exitSignal) => {
      observedExit = true; code = exitCode; signal = exitSignal ?? null; resolve();
    }));
    let forced = false;
    try {
      const sent = utility ? child.kill() : child.kill('SIGTERM');
      if (utility) utilityExit.stopSent = sent === true;
      recordLifecycle('service-stop-signal', { service: name, pid, signal: utility ? 'utility-kill' : 'SIGTERM', sent });
    } catch (error) {
      recordLifecycle('service-stop-signal-failed', { service: name, pid, errorCode: error.code || 'unknown' });
    }
    const exitWait = await waitUntil(exited, deadline);
    if (isRunning()) {
      forced = true;
      try {
        const sent = utility ? process.kill(pid, 'SIGKILL') : signalService(child, 'SIGKILL');
        recordLifecycle('service-stop-escalated', { service: name, pid, signal: 'SIGKILL', sent });
      } catch (error) {
        recordLifecycle('service-stop-signal-failed', { service: name, pid, signal: 'SIGKILL', errorCode: error.code || 'unknown' });
      }
      // This is only the existing bounded kill-observation window, not more API cleanup time.
      await waitUntil(exited, performance.now() + 1000);
    }
    // Electron normalizes a successful utility kill's expected POSIX termination to code 0.
    const expectedExit = utility ? utilityExit.expected && utilityExit.stopSent && !utilityExit.failed && code === 0
      : code === 0 || signal === 'SIGTERM';
    const normal = observedExit && exitWait.completed && !forced && expectedExit;
    const result = { exited: observedExit, forced, normal, timedOut: !exitWait.completed, owned: true, code, signal };
    recordLifecycle('service-stop-result', { service: name, pid, ...result });
    if (observedExit) setStopping(false);
    return result;
  }

  function signalService(child, signal) {
    if (process.platform === 'win32') return child.kill(signal);
    return signalServiceGroup(child.pid, signal);
  }

  function terminateExitedServiceGroup(pid) {
    if (process.platform === 'win32') return;
    signalServiceGroup(pid, 'SIGTERM');
  }

  function signalServiceGroup(pid, signal) {
    try {
      return process.kill(-pid, signal);
    } catch (error) {
      if (error.code === 'ESRCH') return false;
      throw error;
    }
  }

  function stopAllServices({ stopCompanion } = {}) {
    if (stopPromise) return stopPromise;
    stopPromise = (async () => {
      const deadline = performance.now() + 30_000;
      const exitRequestId = randomUUID();
      const deadlineAtUtc = new Date(Date.now() + 30_000).toISOString();
      const api = apiProcess;
      const web = nextInstance;
      recordLifecycle('desktop-shutdown-requested', { exitRequestId, deadlineAtUtc });
      // Start API notification before waiting on any gateway configuration already in flight.
      const controlRequest = requestApiShutdown(api, exitRequestId, deadlineAtUtc, deadline);
      const companionStop = stopCompanion ? waitUntil(Promise.resolve().then(stopCompanion).then(() => true, () => false), deadline)
        : Promise.resolve({ completed: true, value: true });
      const control = await controlRequest;
      recordLifecycle('desktop-shutdown-receipt', { exitRequestId, ...control });
      const [apiExit, webExit, companionExit] = await Promise.all([
        stopService('api', api, value => { apiStopping = value; }, deadline),
        stopService('web', web ? web.child : null, value => { web.stopRequested = value; }, deadline, web),
        companionStop,
      ]);
      const clean = control.confirmed && apiExit.normal && webExit.normal
        && companionExit.completed && companionExit.value;
      if (apiExit.owned) {
        if (control.confirmed && apiExit.normal) setServiceState('api', 'stopped');
        else setServiceState('api', 'failed', Object.assign(new Error('任务保存暂停、进程释放或正常退出未全部确认。'), { code: 'API_SHUTDOWN_UNCONFIRMED' }));
      }
      if (webExit.owned) {
        if (webExit.normal) setServiceState('web', 'stopped');
        else setServiceState('web', 'failed', Object.assign(new Error('Web服务退出未确认。'), { code: 'SERVICE_STOP_UNCONFIRMED' }));
      }
      recordLifecycle('desktop-shutdown-result', { exitRequestId, clean: Boolean(clean),
        apiExited: apiExit.exited, webExited: webExit.exited, companionStopped: Boolean(companionExit.completed && companionExit.value) });
      return { clean: Boolean(clean), api: apiExit, web: webExit };
    })();
    return stopPromise;
  }


  function getApiEndpoint() {
    return { port: apiPort, state: serviceStates.api.state };
  }
  function getServiceStatus() {
    return {
      api: { ...serviceStates.api, pid: apiProcess ? apiProcess.pid : null, port: apiPort },
      web: { ...serviceStates.web, pid: nextProcess ? nextProcess.pid : null, port: nextPort },
    };
  }

  return { startApiServer, startNextServer, stopAllServices, getApiEndpoint, getServiceStatus, setServiceState, reportServiceFailure };
}

module.exports = { createServiceSupervisor };
