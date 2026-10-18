// A narrowly scoped TLS boundary for the phone; application authorization stays in the API.
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const https = require('node:https');
const http = require('node:http');
const { X509Certificate, createPrivateKey } = require('node:crypto');
const selfsigned = require('selfsigned');
const QRCode = require('qrcode');

function lanAddresses() {
  return Object.values(os.networkInterfaces()).flat().filter((entry) => entry && entry.family === 'IPv4' && !entry.internal
    && /^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(entry.address)).map((entry) => entry.address);
}
function allowedRequest(method, pathname) {
  if (method === 'POST' && ['/api/device-invites/accept', '/api/companion/pairings/accept'].includes(pathname)) return true;
  if (method === 'GET' && ['/api/companion/session', '/api/creation-tasks'].includes(pathname)) return true;
  if (method === 'GET' && /^\/api\/creation-tasks\/[\w-]+(?:\/(execution|job-runs|output))?$/.test(pathname)) return true;
  if (['GET', 'POST'].includes(method) && pathname === '/api/media/voice-sampling-tasks') return true;
  if (method === 'GET' && /^\/api\/media\/voice-sampling-tasks\/[\w-]+$/.test(pathname)) return true;
  if (method === 'POST' && /^\/api\/media\/voice-sampling-tasks\/[\w-]+\/sample$/.test(pathname)) return true;
  if (['GET', 'PUT'].includes(method) && /^\/api\/mobile-transfers\/[0-9a-f-]{36}$/.test(pathname)) return true;
  if (method === 'PUT' && /^\/api\/mobile-transfers\/[0-9a-f-]{36}\/content$/.test(pathname)) return true;
  return method === 'POST' && /^\/api\/mobile-transfers\/[0-9a-f-]{36}\/(complete|cancel)$/.test(pathname);
}
async function atomicJson(file, value) {
  const temporary = `${file}.pending`;
  await fs.writeFile(temporary, JSON.stringify(value), { mode: 0o600 });
  await fs.rename(temporary, file);
}
function checkCertificate(pem, privateKey, allowExpired = false) {
  const cert = new X509Certificate(pem);
  if (Date.parse(cert.validFrom) > Date.now() || (!allowExpired && Date.parse(cert.validTo) <= Date.now())
      || !cert.checkPrivateKey(createPrivateKey(privateKey))) throw new Error('手机连接证书已失效或与密钥不匹配，请修复后重新配对。');
  return cert;
}
async function tlsIdentity(directory, deviceId, address) {
  await fs.mkdir(directory, { recursive: true, mode: 0o700 });
  const caPath = path.join(directory, 'ca.json');
  let ca;
  try { ca = JSON.parse(await fs.readFile(caPath, 'utf8')); }
  catch (error) {
    if (error.code !== 'ENOENT') throw error;
    const generated = await selfsigned.generate([{ name: 'commonName', value: `Yaohe ${deviceId}` }], {
      keyType: 'ec', curve: 'P-256', algorithm: 'sha256',
      notAfterDate: new Date(Date.now() + 3 * 365 * 86400_000),
      extensions: [{ name: 'basicConstraints', cA: true, critical: true, pathLenConstraint: 0 },
        { name: 'keyUsage', keyCertSign: true, cRLSign: true, critical: true }],
    });
    ca = { deviceId, privateKey: generated.private, cert: generated.cert };
    await atomicJson(caPath, ca);
  }
  if (ca.deviceId !== deviceId || !checkCertificate(ca.cert, ca.privateKey).ca) throw new Error('手机连接身份与当前主机不一致。');
  const leafPath = path.join(directory, 'server.json');
  let leaf;
  try { leaf = JSON.parse(await fs.readFile(leafPath, 'utf8')); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  if (leaf) {
    const cert = checkCertificate(leaf.cert, leaf.privateKey, true);
    if (!cert.verify(new X509Certificate(ca.cert).publicKey)) throw new Error('手机连接证书签发者不匹配。');
    if (!cert.checkIP(address) || Date.parse(cert.validTo) - Date.now() < 86400_000) leaf = null;
  }
  if (!leaf) {
    const generated = await selfsigned.generate([{ name: 'commonName', value: address }], {
      keyType: 'ec', curve: 'P-256', algorithm: 'sha256',
      notAfterDate: new Date(Date.now() + 30 * 86400_000), ca: { key: ca.privateKey, cert: ca.cert },
      extensions: [{ name: 'basicConstraints', cA: false, critical: true },
        { name: 'keyUsage', digitalSignature: true, critical: true },
        { name: 'extKeyUsage', serverAuth: true },
        { name: 'subjectAltName', altNames: [{ type: 7, ip: address }] }],
    });
    leaf = { privateKey: generated.private, cert: generated.cert };
    await atomicJson(leafPath, leaf);
  }
  return { ca: ca.cert, key: leaf.privateKey, cert: leaf.cert };
}
function apiOrigin(getApiEndpoint) {
  const endpoint = getApiEndpoint();
  if (endpoint.state !== 'ready' || !Number.isInteger(endpoint.port) || endpoint.port < 1) throw new Error('本地主机 API 尚未就绪。');
  const url = new URL(`http://127.0.0.1:${endpoint.port}`);
  if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || url.username || url.password) throw new Error('手机网关只连接本机受管理的 API。');
  return url;
}
function createCompanionGateway({ userDataPath, getApiEndpoint, getAuthConfig, getDeviceId, addresses = lanAddresses }) {
  const directory = path.join(userDataPath, 'companion');
  const configPath = path.join(directory, 'connection.json');
  let server = null, current = null, identity = null, activeChange = null, shuttingDown = false;
  const sockets = new Set();
  const readAuth = () => {
    const auth = getAuthConfig();
    if (!auth?.supabase_url || !auth.publishable_key || new URL(auth.supabase_url).protocol !== 'https:') throw new Error('请先完成正式账号与主机登记，再启用手机连接。');
    return auth;
  };
  async function stop() {
    if (!server) return;
    const closing = server; server = null; current = null;
    const done = new Promise((resolve) => closing.close(resolve));
    for (const socket of sockets) socket.destroy();
    sockets.clear(); await done;
  }
  async function start(config) {
    if (shuttingDown) throw new Error('桌面正在退出，未开启手机连接。');
    readAuth();
    if (!addresses().includes(config.address)) throw new Error('原局域网地址已变化，请在桌面重新选择手机连接地址。');
    apiOrigin(getApiEndpoint);
    identity = await tlsIdentity(directory, getDeviceId(), config.address);
    if (shuttingDown) throw new Error('桌面正在退出，未开启手机连接。');
    const candidate = https.createServer({ key: identity.key, cert: identity.cert, minVersion: 'TLSv1.2' }, (request, response) => {
      const pathname = request.url?.split('?')[0];
      const accepting = request.method === 'POST'
        && ['/api/device-invites/accept', '/api/companion/pairings/accept'].includes(pathname);
      if (!request.url?.startsWith('/') || !allowedRequest(request.method, pathname)
          || !/^Bearer [^\s]+$/.test(request.headers.authorization || '')
          || (!accepting && !request.headers['x-workspace-id'])
          || request.headers['x-local-member-token'] || request.headers.cookie) {
        response.writeHead(403); response.end('Phone request is not authorized.'); return;
      }
      let target;
      try { target = apiOrigin(getApiEndpoint); }
      catch { response.writeHead(503); response.end('Host API unavailable.'); return; }
      const headers = {};
      for (const key of ['authorization', 'x-workspace-id', 'content-type', 'content-length', 'range', 'if-range', 'accept']) {
        if (request.headers[key] !== undefined) headers[key] = request.headers[key];
      }
      const upstream = http.request({ hostname: target.hostname, port: target.port, method: request.method, path: request.url, headers }, (incoming) => {
        if (incoming.statusCode >= 300 && incoming.statusCode < 400) {
          incoming.resume(); response.writeHead(502); response.end('Phone endpoint redirects are not supported.'); return;
        }
        const outputHeaders = {};
        for (const key of ['content-type', 'content-length', 'content-range', 'accept-ranges', 'etag', 'last-modified']) {
          if (incoming.headers[key] !== undefined) outputHeaders[key] = incoming.headers[key];
        }
        outputHeaders['cache-control'] = 'no-store';
        response.writeHead(incoming.statusCode, outputHeaders);
        incoming.pipe(response);
        incoming.on('error', () => response.destroy());
      });
      upstream.on('error', () => { if (!response.headersSent) response.writeHead(502); response.end('Host API unavailable.'); });
      request.on('aborted', () => upstream.destroy());
      response.on('close', () => { if (!response.writableFinished) upstream.destroy(); });
      request.pipe(upstream);
    });
    candidate.on('connection', (socket) => { sockets.add(socket); socket.on('close', () => sockets.delete(socket)); });
    await new Promise((resolve, reject) => { candidate.once('error', reject); candidate.listen(config.port || 0, config.address, resolve); });
    server = candidate; current = { address: config.address, port: candidate.address().port };
    return status();
  }
  function status() { return { enabled: !!server, endpoint: current ? `https://${current.address}:${current.port}` : null, addresses: addresses(), device_id: getDeviceId() }; }
  async function configure({ address, accessToken, workspaceId }) {
    readAuth();
    if (server) throw new Error('手机连接已开启；请先关闭再更换地址。');
    const target = apiOrigin(getApiEndpoint);
    const result = await fetch(new URL('/api/companion/session', target), { redirect: 'error', signal: AbortSignal.timeout(10_000),
      headers: { Authorization: `Bearer ${accessToken}`, 'X-Workspace-Id': workspaceId } });
    if (!result.ok) throw new Error('当前账号尚未完成主机绑定，无法开启手机连接。');
    const session = await result.json();
    if (session.device_id !== getDeviceId() || session.workspace_id !== workspaceId || session.role !== 'owner') throw new Error('仅已核验的主账号可开启手机连接。');
    await start({ address, port: 0 });
    try { await atomicJson(configPath, { ...current, deviceId: getDeviceId(), enabled: true }); }
    catch (error) { await stop(); throw error; }
    return status();
  }
  async function disable() {
    await stop();
    await fs.mkdir(directory, { recursive: true, mode: 0o700 });
    await atomicJson(configPath, { enabled: false, deviceId: getDeviceId() });
    return status();
  }
  async function restore() {
    let config;
    try { config = JSON.parse(await fs.readFile(configPath, 'utf8')); }
    catch (error) { if (error.code === 'ENOENT') return status(); throw error; }
    if (!config.enabled) return status();
    if (config.deviceId !== getDeviceId()) throw new Error('保存的手机连接不属于本机。');
    return start(config);
  }
  async function invitation({ purpose, token, expires_at, workspace_id }) {
    if (!server) throw new Error('请先开启手机连接。');
    if (!['pair', 'invite'].includes(purpose) || !token || !workspace_id || !Number.isFinite(Date.parse(expires_at)) || Date.parse(expires_at) <= Date.now()) throw new Error('需要有效的短期邀请。');
    const payload = { type: 'yaohe-companion', version: 1, endpoint: status().endpoint, device_id: getDeviceId(),
      purpose, token, expires_at, workspace_id, tls_ca_pem: identity.ca, auth: readAuth() };
    return { payload, qr_data_url: await QRCode.toDataURL(JSON.stringify(payload), { errorCorrectionLevel: 'M', margin: 2, width: 640 }) };
  }
  async function change(action) {
    if (shuttingDown) throw new Error('桌面正在退出。');
    if (activeChange) throw new Error('手机连接正在更新，请稍后再试。');
    activeChange = action();
    try { return await activeChange; } finally { activeChange = null; }
  }
  async function shutdown() {
    shuttingDown = true;
    // Quit must join a configuration already in flight before closing its listener.
    if (activeChange) await activeChange.catch(() => {});
    await stop();
  }
  return { status, configure: (input) => change(() => configure(input)),
    disable: () => change(disable), restore: () => change(restore), stop: shutdown, invitation };
}
module.exports = { createCompanionGateway, tlsIdentity, allowedRequest, lanAddresses };
