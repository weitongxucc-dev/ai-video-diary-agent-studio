const fs = require('fs');
const path = require('path');
const http = require('http');

function createServiceSupervisor({ isDev, webPort, resourcesPath, apiEnvironment, webEnvironment, spawnApi, forkWeb, recordLifecycle, attachChildOutput, onFailure }) {
  let apiProcess = null;
  let nextProcess = null;
  let apiPort = 0;
  let nextPort = 0;
  let apiStopping = false;
  let nextStopping = false;
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

    const env = apiEnvironment(apiPort);

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

    const env = webEnvironment(nextPort, apiPort);

    nextProcess = forkWeb(serverJs, [], {
      env,
      stdio: 'pipe',
      serviceName: 'Yaohe Web',
    });

    nextProcess.on('error', (err) => {
      console.error(`[main] Web utility process failed: ${err.type}`);
      nextProcess = null;
      reportServiceFailure('web', new Error(`后台网页服务异常（${err.type}）`));
    });

    attachChildOutput('web', nextProcess.stdout, 'next', (message) => console.log(message));
    attachChildOutput('web', nextProcess.stderr, 'next:err', (message) => console.error(message));

    nextProcess.on('exit', (code) => {
      console.log(`[main] Web utility process exited (code=${code})`);
      recordLifecycle('service-exit', { service: 'web', code, signal: null, expected: nextStopping });
      nextProcess = null;
      if (!nextStopping) {
        reportServiceFailure('web', new Error(`进程已退出（code=${code}）`));
      }
    });

    await waitForNext(nextPort);
    setServiceState('web', 'ready');
    return nextPort;
  }

  async function stopService(name, child, setStopping, utility = false) {
    if (!child) return;
    const isRunning = () => utility ? child.pid !== undefined : child.exitCode === null;
    if (!isRunning()) {
      setServiceState(name, 'stopped');
      return;
    }
    setStopping(true);
    console.log(`[main] Stopping ${serviceStates[name].label} (pid=${child.pid})...`);

    const exited = new Promise((resolve) => child.once('exit', resolve));
    if (utility) child.kill();
    else signalService(child, 'SIGTERM');
    let timeoutId;
    await Promise.race([
      exited,
      new Promise((resolve) => { timeoutId = setTimeout(resolve, 5000); }),
    ]);
    clearTimeout(timeoutId);
    if (isRunning()) {
      if (utility) process.kill(child.pid, 'SIGKILL');
      else signalService(child, 'SIGKILL');
    }
    let killTimeoutId;
    await Promise.race([
      exited,
      new Promise((resolve) => { killTimeoutId = setTimeout(resolve, 1000); }),
    ]);
    clearTimeout(killTimeoutId);
    setServiceState(name, 'stopped');
    setStopping(false);
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

  async function stopAllServices() {
    await Promise.all([
      stopService('web', nextProcess, (value) => { nextStopping = value; }, true),
      stopService('api', apiProcess, (value) => { apiStopping = value; }),
    ]);
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
