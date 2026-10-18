const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const https = require('node:https');
const { createCompanionGateway, allowedRequest } = require('../runtime/companion-gateway');

(async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'yaohe-companion-'));
  let calls = 0;
  let apiReady = true;
  const api = http.createServer((request, response) => {
    calls++;
    assert.equal(request.headers['x-local-member-token'], undefined);
    response.setHeader('Content-Type', 'application/json');
    response.setHeader('X-Timeline-Version-Id', 'test-version');
    response.setHeader('X-Timeline-Sequence', '2');
    if (request.url === '/api/companion/session') response.end(JSON.stringify({ device_id: 'test-device', workspace_id: 'test-space', role: 'owner', member_id: 'test-member' }));
    else { response.statusCode = request.headers.authorization === 'Bearer unit-test-token' ? 200 : 401; response.end(JSON.stringify({ version: 'test-version' })); }
  });
  await new Promise((resolve) => api.listen(0, '127.0.0.1', resolve));
  const create = () => createCompanionGateway({ userDataPath: root, getDeviceId: () => 'test-device',
    getApiEndpoint: () => ({ state: apiReady ? 'ready' : 'stopped', port: api.address().port }),
    getAuthConfig: () => ({ supabase_url: 'https://unit-test.invalid', publishable_key: 'public-test' }), addresses: () => ['127.0.0.1'] });
  let gateway = create();
  const get = (url, ca, headers = {}) => new Promise((resolve, reject) => {
    const request = https.get(url, { ca, headers }, (response) => { let body = ''; response.on('data', (data) => body += data); response.on('end', () => resolve({ status: response.statusCode, body, headers: response.headers })); });
    request.on('error', reject);
  });
  try {
    assert.equal(allowedRequest('GET', '/api/runtime/settings'), false);
    assert.equal(allowedRequest('POST', '/api/creation-tasks/task/render'), false);
    await assert.rejects(gateway.configure({ address: '192.168.250.250', accessToken: 'unit-test-token', workspaceId: 'test-space' }), /地址/);
    const state = await gateway.configure({ address: '127.0.0.1', accessToken: 'unit-test-token', workspaceId: 'test-space' });
    const invitation = await gateway.invitation({ purpose: 'pair', token: 'short-lived-fixture', expires_at: new Date(Date.now() + 60_000).toISOString(), workspace_id: 'test-space' });
    assert.ok(invitation.qr_data_url.startsWith('data:image/png;base64,'));
    assert.ok(!JSON.stringify(invitation.payload).includes('PRIVATE KEY'));
    assert.ok(!JSON.stringify(invitation.payload).includes('unit-test-token'));
    const ca = invitation.payload.tls_ca_pem;
    const before = calls;
    assert.equal((await get(`${state.endpoint}/api/creation-tasks`, ca)).status, 403);
    assert.equal((await get(`${state.endpoint}/api/runtime/settings`, ca, { Authorization: 'Bearer unit-test-token' })).status, 403);
    assert.equal(calls, before);
    assert.equal((await get(`${state.endpoint}/api/creation-tasks`, ca, { Authorization: 'Bearer unit-test-token' })).status, 403);
    assert.equal(calls, before);
    assert.equal((await get(`${state.endpoint}/api/creation-tasks`, ca, { Authorization: 'Bearer unit-test-token', 'X-Workspace-Id': 'test-space' })).status, 200);
    const output = await get(`${state.endpoint}/api/creation-tasks/test-task/output?expected_version_id=test-version`, ca, { Authorization: 'Bearer unit-test-token', 'X-Workspace-Id': 'test-space' });
    assert.equal(output.status, 200);
    assert.equal(output.headers['x-timeline-version-id'], 'test-version');
    assert.equal(output.headers['x-timeline-sequence'], '2');
    apiReady = false;
    assert.equal((await get(`${state.endpoint}/api/creation-tasks`, ca, { Authorization: 'Bearer unit-test-token', 'X-Workspace-Id': 'test-space' })).status, 503);
    apiReady = true;
    await assert.rejects(get(`${state.endpoint}/api/creation-tasks`, undefined), /certificate|issuer|verify/i);
    await gateway.stop();
    gateway = create();
    const restored = await gateway.restore();
    assert.equal(restored.endpoint, state.endpoint);
    assert.equal((await get(`${restored.endpoint}/api/creation-tasks`, ca, { Authorization: 'Bearer unit-test-token', 'X-Workspace-Id': 'test-space' })).status, 200);
    await gateway.disable();
    assert.equal((await gateway.restore()).enabled, false);
    const pending = gateway.configure({ address: '127.0.0.1', accessToken: 'unit-test-token', workspaceId: 'test-space' });
    const quitting = gateway.stop();
    await assert.rejects(pending, /退出/);
    await quitting;
    assert.equal(gateway.status().enabled, false);
    console.log('PASS: real TLS pinning, restricted forwarding, persistent certificate/port and disable; API identity fixture only.');
  } finally {
    await gateway.stop();
    await new Promise((resolve) => api.close(resolve));
    await fs.rm(root, { recursive: true, force: true });
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
