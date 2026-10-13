
function createOutputLogging({ stdout, stderr, recordLifecycle, onMainFailure, onServiceFailure, handleStartupStderrError }) {
  function isBrokenPipe(error) {
    return error && error.code === 'EPIPE';
  }

  function handleMainLogStreamError(stream, error) {
    if (isBrokenPipe(error)) {
      recordLifecycle('main-log-sink-closed', { stream, code: error.code });
      return;
    }
    onMainFailure(stream, error);
  }

  function installMainLogStreamGuards() {
    stderr.removeListener('error', handleStartupStderrError);
    stdout.on('error', (error) => handleMainLogStreamError('stdout', error));
    stderr.on('error', (error) => handleMainLogStreamError('stderr', error));
  }

  function attachChildOutput(service, stream, prefix, write) {
    stream.on('data', (data) => {
      write(`[${prefix}] ${data.toString().trim()}`);
    });
    stream.on('error', (error) => {
      if (isBrokenPipe(error)) {
        recordLifecycle('child-log-sink-closed', { service, stream: prefix, code: error.code });
        return;
      }
      onServiceFailure(service, new Error(`${prefix} 日志管道失败（${error.code || error.name || 'unknown'}）`));
    });
  }


  return { installMainLogStreamGuards, attachChildOutput };
}

module.exports = { createOutputLogging };
