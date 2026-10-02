#!/usr/bin/env node

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {
  DEVICE_IDENTITY_FILENAME,
  generateIdentity,
  identityEnv,
  isValidIdentity,
  loadOrCreateIdentity,
} = require('../device-identity');

function main() {
  const userData = fs.mkdtempSync(path.join(os.tmpdir(), 'ai-video-diary-device-identity-'));
  const filePath = path.join(userData, DEVICE_IDENTITY_FILENAME);
  try {
    // generate: device_id is a canonical UUID, proof is 64 lowercase hex.
    const generated = generateIdentity();
    assert.equal(isValidIdentity(generated), true, 'generated identity must be valid');
    assert.match(generated.device_id, /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);
    assert.match(generated.proof, /^[0-9a-f]{64}$/);

    // missing (first launch): generated, persisted, stable across calls.
    assert.equal(fs.existsSync(filePath), false, 'no identity file before first launch');
    const first = loadOrCreateIdentity(userData);
    assert.equal(isValidIdentity(first), true, 'first-launch identity must be valid');
    assert.equal(fs.existsSync(filePath), true, 'first launch must persist the identity');
    const second = loadOrCreateIdentity(userData);
    assert.deepEqual(second, first, 'identity must be stable across launches');
    assert.deepEqual(JSON.parse(fs.readFileSync(filePath, 'utf8')), first, 'persisted file must match the returned identity');

    // malformed: operational fault, fail closed, never silently regenerated.
    for (const bad of ['{not json', JSON.stringify({ device_id: 'not-a-uuid', proof: 'ab'.repeat(32) }), JSON.stringify({ device_id: first.device_id, proof: 'BAD' })]) {
      fs.writeFileSync(filePath, bad, 'utf8');
      assert.throws(() => loadOrCreateIdentity(userData), /malformed/, 'a malformed identity must fail closed');
      assert.equal(fs.readFileSync(filePath, 'utf8'), bad, 'a malformed identity must not be regenerated');
    }

    // injection mapping: DEVICE_ID / DEVICE_PROOF env keys.
    assert.deepEqual(identityEnv(first), { DEVICE_ID: first.device_id, DEVICE_PROOF: first.proof });

    console.log('desktop device identity: PASS');
  } finally {
    fs.rmSync(userData, { recursive: true, force: true });
  }
}

main();
