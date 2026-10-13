const fs = require('fs');
const crypto = require('crypto');

function metadataManifestDigest(inputs) {
  return crypto.createHash('sha256').update(JSON.stringify(inputs)).digest('hex');
}

// Keep this locale-independent order identical to generate-build-identity.js.
function compareIdentityPaths(left, right) {
  if (left === right) return 0;
  return left < right ? -1 : 1;
}

function loadBuildIdentity({ metadataFile, isDev, isPackaged }) {
  let metadata;
  try {
    metadata = JSON.parse(fs.readFileSync(metadataFile, 'utf8'));
  } catch (error) {
    if (isDev && !isPackaged && error && error.code === 'ENOENT') return null;
    throw new Error('Desktop package build identity metadata is missing or unreadable; rebuild the app before launching.');
  }
  const inputs = metadata && metadata.inputs;
  const validInputs = Array.isArray(inputs)
    && inputs.length > 0
    && inputs.every((input, index) => (
      input
      && typeof input.path === 'string'
      && typeof input.sha256 === 'string' && /^[a-f0-9]{64}$/.test(input.sha256)
      && Number.isSafeInteger(input.bytes) && input.bytes >= 0
      && (index === 0 || compareIdentityPaths(inputs[index - 1].path, input.path) < 0)
    ));
  if (
    !metadata
    || metadata.format !== 'ai-video-diary-candidate-inputs/v1'
    || typeof metadata.candidateInputDigest !== 'string' || !/^[a-f0-9]{64}$/.test(metadata.candidateInputDigest)
    || typeof metadata.manifestDigest !== 'string' || !/^[a-f0-9]{64}$/.test(metadata.manifestDigest)
    || !validInputs
    || metadata.manifestDigest !== metadataManifestDigest(inputs)
  ) {
    throw new Error('Desktop package build identity metadata is invalid; rebuild the app before launching.');
  }
  return metadata.candidateInputDigest;
}


module.exports = { loadBuildIdentity };
