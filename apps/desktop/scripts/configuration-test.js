// Real temporary profile/config files; no Electron or user credentials loaded.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createServiceConfiguration } = require('../runtime/configuration');
const { loadBuildIdentity } = require('../runtime/build-identity');
const { createMetadata } = require('./generate-build-identity');

const root = fs.mkdtempSync(path.join(os.tmpdir(), 'yaohe-r7-configuration-'));
try {
  const userDataPath = path.join(root, 'profile');
  const desktopDirectory = path.join(root, 'app');
  const resourcesPath = path.join(root, 'resources');
  const environment = { PATH: '/fixture/bin', FIXTURE_ENV: 'preserved' };
  const configuration = createServiceConfiguration({ userDataPath, desktopDirectory, resourcesPath, environment });
  assert.equal(fs.existsSync(userDataPath), false, 'assembly alone does not create a profile or identity');
  configuration.ensureUserDotenv();
  assert.equal(fs.readFileSync(path.join(userDataPath, '.env'), 'utf8'), '');
  assert.equal(fs.statSync(path.join(userDataPath, 'temporary')).isDirectory(), true);
  fs.writeFileSync(path.join(userDataPath, '.env'), 'FIXTURE_PRIVATE_CONFIG=keep-existing');
  configuration.ensureUserDotenv();
  assert.equal(fs.readFileSync(path.join(userDataPath, '.env'), 'utf8'), 'FIXTURE_PRIVATE_CONFIG=keep-existing');

  const api = configuration.apiEnvironment(32101);
  const identityPath = path.join(userDataPath, 'device-identity.generated.json');
  const identity = JSON.parse(fs.readFileSync(identityPath, 'utf8'));
  assert.equal(api.DEVICE_ID, identity.device_id);
  assert.equal(api.DEVICE_PROOF, identity.proof);
  assert.equal(configuration.apiEnvironment(32102).DEVICE_PROOF, identity.proof, 'API restarts reuse persisted identity');
  assert.equal(api.AI_VIDEO_DIARY_DOTENV, path.join(userDataPath, '.env'));
  assert.equal(api.API_HOST, '127.0.0.1');
  assert.equal(api.API_PORT, '32101');
  assert.equal(api.TMPDIR, path.join(userDataPath, 'temporary'));
  assert.equal(api.AI_VIDEO_DIARY_PI_EXTENSION, path.join(resourcesPath, 'agent-tools', 'diary_tools.ts'));
  assert.ok(api.PATH.endsWith('/fixture/bin'));

  const web = configuration.webEnvironment(32103, 32101);
  assert.equal(web.DEVICE_ID, undefined);
  assert.equal(web.DEVICE_PROOF, undefined, 'generated device proof is never injected into Web');
  assert.equal(web.AI_VIDEO_DIARY_DOTENV, undefined);
  assert.equal(web.PORT, '32103');
  assert.equal(web.API_PORT, '32101');
  assert.equal(web.FIXTURE_ENV, 'preserved');
  assert.deepEqual(environment, { PATH: '/fixture/bin', FIXTURE_ENV: 'preserved' }, 'child assembly does not mutate the inherited environment');

  fs.mkdirSync(path.join(desktopDirectory, 'assets'), { recursive: true });
  const distributionPath = path.join(desktopDirectory, 'assets', 'distribution-config.generated.json');
  fs.writeFileSync(distributionPath, JSON.stringify({ authMode: 'supabase', supabaseUrl: 'https://fixture.invalid', supabasePublishableKey: 'fixture-public-key' }));
  assert.equal(configuration.apiEnvironment(32101).SUPABASE_PUBLISHABLE_KEY, 'fixture-public-key');
  assert.equal(configuration.webEnvironment(32103, 32101).SUPABASE_PUBLISHABLE_KEY, 'fixture-public-key');
  fs.writeFileSync(distributionPath, JSON.stringify({ authMode: 'supabase' }));
  assert.throws(() => configuration.apiEnvironment(32101), /public distribution config is incomplete/);
  fs.unlinkSync(distributionPath);
  fs.writeFileSync(identityPath, '{malformed');
  assert.throws(() => configuration.apiEnvironment(32101), /malformed/);
  assert.equal(fs.readFileSync(identityPath, 'utf8'), '{malformed', 'a corrupt existing identity is preserved and fails closed');

  const metadataFile = path.join(desktopDirectory, 'build-identity.generated.json');
  assert.equal(loadBuildIdentity({ metadataFile, isDev: true, isPackaged: false }), null);
  assert.throws(() => loadBuildIdentity({ metadataFile, isDev: true, isPackaged: true }), /missing or unreadable/);
  const metadata = createMetadata([{ path: 'desktop/runtime/example.js', content: 'fixture-input' }]);
  fs.writeFileSync(metadataFile, JSON.stringify(metadata));
  assert.equal(loadBuildIdentity({ metadataFile, isDev: false, isPackaged: true }), metadata.candidateInputDigest);
  metadata.inputs[0].bytes += 1;
  fs.writeFileSync(metadataFile, JSON.stringify(metadata));
  assert.throws(() => loadBuildIdentity({ metadataFile, isDev: true, isPackaged: false }), /metadata is invalid/);
  console.log('desktop configuration: PASS (profile preservation, API-only device identity, explicit child env, corrupt identity/metadata refusal)');
} finally {
  fs.rmSync(root, { recursive: true, force: true });
}
