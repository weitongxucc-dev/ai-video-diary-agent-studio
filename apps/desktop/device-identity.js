"use strict";

/*
 * Local device identity for the native runtime.
 *
 * The Electron main process owns the only device identity: on first launch it
 * generates a ``device_id`` (random UUID) and a ``proof`` (64 lowercase hex),
 * persists them to a file under userData, and later injects them into the API
 * subprocess environment. The API's ``DeviceCredential`` accepts exactly a
 * UUID and a 64-lowercase-hex proof, so this module keeps to that shape.
 *
 * A missing file is a first launch and is generated. A file that exists but is
 * malformed is an operational fault — it is never silently regenerated, because
 * replacing a corrupted identity would orphan whatever the device was already
 * registered as. The main process lets that fault fail startup (503 downstream),
 * matching the trust model where the renderer can never observe the credential.
 */

const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

const DEVICE_IDENTITY_FILENAME = 'device-identity.generated.json';

// crypto.randomUUID() yields a lowercase hyphenated UUID; keep the check to the
// canonical form the runtime itself writes, so a hand-edited file fails closed.
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
// Matches DeviceCredential.__post_init__: 64 lowercase hex = 32 bytes.
const PROOF_PATTERN = /^[0-9a-f]{64}$/;

function isValidIdentity(value) {
  return value !== null && typeof value === 'object'
    && typeof value.device_id === 'string' && UUID_PATTERN.test(value.device_id)
    && typeof value.proof === 'string' && PROOF_PATTERN.test(value.proof);
}

function generateIdentity() {
  return {
    device_id: crypto.randomUUID(),
    proof: crypto.createHash('sha256').update(crypto.randomBytes(32)).digest('hex'),
  };
}

function loadOrCreateIdentity(userDataPath) {
  const filePath = path.join(userDataPath, DEVICE_IDENTITY_FILENAME);

  let raw;
  try {
    raw = fs.readFileSync(filePath, 'utf8');
  } catch (error) {
    if (error && error.code === 'ENOENT') {
      const identity = generateIdentity();
      fs.mkdirSync(userDataPath, { recursive: true });
      fs.writeFileSync(filePath, `${JSON.stringify(identity, null, 2)}\n`, 'utf8');
      return identity;
    }
    throw error;
  }

  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch (error) {
    throw new Error('device identity file is malformed');
  }
  if (!isValidIdentity(parsed)) {
    throw new Error('device identity file is malformed');
  }
  return { device_id: parsed.device_id, proof: parsed.proof };
}

function identityEnv(identity) {
  return {
    DEVICE_ID: identity.device_id,
    DEVICE_PROOF: identity.proof,
  };
}

module.exports = {
  DEVICE_IDENTITY_FILENAME,
  generateIdentity,
  identityEnv,
  isValidIdentity,
  loadOrCreateIdentity,
};
