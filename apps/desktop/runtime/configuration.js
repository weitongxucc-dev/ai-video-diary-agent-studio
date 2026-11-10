const fs = require('fs');
const path = require('path');
const { loadOrCreateIdentity } = require('../device-identity');

const authWebPort = 3000;
function resolveWebPort({ hasLocalWebPort, localWebPort, explicitUserDataPath, isDev, desktopDirectory }) {
  if (!hasLocalWebPort) return authWebPort;
  const value = localWebPort;
  if (!/^\d+$/.test(value) || Number(value) < 1 || Number(value) > 65535) {
    throw new Error('--local-web-port must be an integer from 1 to 65535.');
  }
  if (!explicitUserDataPath) {
    throw new Error('--local-web-port requires an explicit absolute --user-data-dir.');
  }
  if (isDev) {
    throw new Error('--local-web-port requires a local auth build that starts its own Web service, not development mode.');
  }
  const configPath = path.join(desktopDirectory, 'assets', 'distribution-config.generated.json');
  let config;
  try {
    config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
  } catch (error) {
    throw new Error('--local-web-port requires readable local auth build configuration.');
  }
  if (config?.authMode !== 'local') {
    throw new Error('--local-web-port is only allowed for local auth builds; Supabase callbacks must remain on port 3000.');
  }
  return Number(value);
}

function createServiceConfiguration({ userDataPath, resourcesPath, desktopDirectory, environment }) {
  function userDotenvPath() {
    return path.join(userDataPath, '.env');
  }

  function ensureUserDotenv() {
    const p = userDotenvPath();
    fs.mkdirSync(path.join(userDataPath, 'temporary'), { recursive: true });
    if (!fs.existsSync(p)) {
      fs.mkdirSync(path.dirname(p), { recursive: true });
      fs.writeFileSync(p, '', 'utf-8');
      console.log('[main] Created user config file');
    }
  }

  function localModelEnv() {
    const resourcePath = resourcesPath || path.join(desktopDirectory, '..');
    const temporary = path.join(userDataPath, 'temporary');
    return {
      PATH: process.platform === 'win32' ? (environment.PATH || '')
        : ['/opt/homebrew/bin', '/usr/local/bin', environment.PATH || '/usr/bin:/bin:/usr/sbin:/sbin'].join(path.delimiter),
      TMPDIR: temporary,
      ...(process.platform === 'win32' ? { TEMP: temporary, TMP: temporary } : {}),
      VISUAL_MODEL_WORKER_PATH: path.join(resourcePath, 'model-workers', 'chinese_clip_worker.py'),
      VOICE_MODEL_WORKER_PATH: path.join(resourcePath, 'model-workers', 'qwen3_tts_worker.py'),
      UNDERSTANDING_WORKER_PATH: path.join(resourcePath, 'model-workers', 'qwen3_vl_worker.py'),
      AVATAR_MODEL_WORKER_PATH: path.join(resourcePath, 'model-workers', 'musetalk_worker.py'),
      LATENTSYNC_WORKER_PATH: path.join(resourcePath, 'model-workers', 'latentsync_worker.py'),
      AI_VIDEO_DIARY_PI_EXTENSION: path.join(resourcePath, 'agent-tools', 'diary_tools.ts'),
    };
  }

  function distributionAuthEnv() {
    const configPath = path.join(desktopDirectory, 'assets', 'distribution-config.generated.json');
    try {
      const config = JSON.parse(fs.readFileSync(configPath, 'utf-8'));
      if (config.authMode !== 'supabase') return {};
      if (!config.supabaseUrl || !config.supabasePublishableKey) {
        throw new Error('Supabase public distribution config is incomplete');
      }
      return {
        SUPABASE_URL: config.supabaseUrl,
        SUPABASE_PUBLISHABLE_KEY: config.supabasePublishableKey,
      };
    } catch (error) {
      if (error && error.code === 'ENOENT') return {};
      throw error;
    }
  }

  // First launch generates and persists the device identity; a malformed file is
  // an operational fault (throws, never regenerated) and will surface as the API's
  // 503 unavailable downstream.
  function deviceIdentityEnv() {
    const identity = loadOrCreateIdentity(userDataPath);
    return {
      DEVICE_ID: identity.device_id,
      DEVICE_PROOF: identity.proof,
    };
  }


  function apiEnvironment(port) {
    return {
      ...environment, ...distributionAuthEnv(), ...deviceIdentityEnv(), ...localModelEnv(),
      DEVICE_RESOURCE_PROFILE_PATH: path.join(userDataPath, 'device-resource-profile.json'),
      DEVICE_RESOURCE_MODE: environment.DEVICE_RESOURCE_MODE || 'normal',
      YAOHE_TEMPLATE_ELECTRON: process.execPath,
      YAOHE_TEMPLATE_RENDERER: resourcesPath
        ? path.join(resourcesPath, 'template-renderer', 'template-renderer.js')
        : path.join(__dirname, 'template-renderer.js'),
      API_PORT: String(port), API_HOST: '127.0.0.1', AI_VIDEO_DIARY_DOTENV: userDotenvPath(),
    };
  }
  function webEnvironment(port, apiPort) {
    return {
      ...environment, ...distributionAuthEnv(), PORT: String(port), HOSTNAME: '127.0.0.1',
      API_PORT: String(apiPort),
    };
  }

  return { ensureUserDotenv, apiEnvironment, webEnvironment };
}

module.exports = { resolveWebPort, createServiceConfiguration };
