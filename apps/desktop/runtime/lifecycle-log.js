const fs = require('fs');
const path = require('path');

function createLifecycleLog({ getLogsDirectory, buildIdentity, startupCheckpoint, onFailure, timeoutMs = 60_000 }) {
  let lifecycleLogFile = null;
  let lifecycleLogStream = null;
  let lifecycleLogFailure = null;
  let lifecycleLogClosePromise = null;
  function runtimeLogPath() {
    if (lifecycleLogFile) return lifecycleLogFile;
    startupCheckpoint('lifecycle-log-path-start');
    const directory = getLogsDirectory();
    startupCheckpoint('lifecycle-log-path-end');
    startupCheckpoint('lifecycle-log-directory-start');
    fs.mkdirSync(directory, { recursive: true });
    startupCheckpoint('lifecycle-log-directory-end');
    lifecycleLogFile = path.join(directory, 'desktop-runtime.jsonl');
    return lifecycleLogFile;
  }

  function failLifecycleLog(error) {
    if (lifecycleLogFailure) return;
    lifecycleLogFailure = error;
    startupCheckpoint('lifecycle-log-failed');
    lifecycleLogStream?.destroy();
    onFailure(error);
  }

  function recordLifecycle(event, fields = {}) {
    const line = `${JSON.stringify({
      timestamp: new Date().toISOString(), event, buildIdentity, ...fields,
    })}\n`;
    const write = new Promise((resolve, reject) => {
      let settled = false;
      let firstLogWrite = false;
      const finish = (error) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        const failure = error || lifecycleLogFailure;
        if (failure) reject(failure);
        else {
          if (firstLogWrite) startupCheckpoint('lifecycle-log-append-end');
          resolve();
        }
      };
      const timer = setTimeout(() => finish(Object.assign(new Error('Lifecycle log write timed out'), { code: 'ETIMEDOUT' })), timeoutMs);
      if (lifecycleLogFailure) { finish(lifecycleLogFailure); return; }
      try {
        if (!lifecycleLogStream) {
          const logFile = runtimeLogPath();
          firstLogWrite = true;
          startupCheckpoint('lifecycle-log-append-start');
          lifecycleLogStream = fs.createWriteStream(logFile, { flags: 'a', encoding: 'utf8' });
          lifecycleLogStream.on('error', failLifecycleLog);
          lifecycleLogStream.on('close', () => {
            if (!lifecycleLogStream.writableFinished && !lifecycleLogFailure) {
              failLifecycleLog(Object.assign(new Error('Lifecycle log closed before finishing'), { code: 'ERR_STREAM_PREMATURE_CLOSE' }));
            }
          });
        }
        lifecycleLogStream.write(line, finish);
      } catch (error) { finish(error); }
    });
    // Observe every failure even for existing callers which do not await log I/O.
    // The returned promise still rejects, so the startup gate cannot claim success.
    void write.catch(failLifecycleLog);
    return write;
  }

  function closeLifecycleLog() {
    if (lifecycleLogClosePromise) return lifecycleLogClosePromise;
    if (!lifecycleLogStream) return Promise.resolve();
    lifecycleLogClosePromise = new Promise((resolve, reject) => {
      let settled = false;
      const finish = (error) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        if (error) reject(error);
        else resolve();
      };
      const timer = setTimeout(() => finish(Object.assign(new Error('Lifecycle log close timed out'), { code: 'ETIMEDOUT' })), timeoutMs);
      if (lifecycleLogFailure) { finish(lifecycleLogFailure); return; }
      lifecycleLogStream.once('close', () => finish(lifecycleLogFailure));
      lifecycleLogStream.end();
    });
    void lifecycleLogClosePromise.catch(failLifecycleLog);
    return lifecycleLogClosePromise;
  }


  return { recordLifecycle, closeLifecycleLog };
}

module.exports = { createLifecycleLog };
