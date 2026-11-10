#!/usr/bin/env node
/*
 * Generate the identity for the candidate inputs that electron-builder will
 * package. This deliberately hashes the materialised trees, not Git state:
 * an untracked file that is packaged changes the identity too.
 */

const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

const REPO_ROOT = path.resolve(__dirname, '..', '..', '..');
const DEFAULT_OUTPUT = path.join(REPO_ROOT, 'apps', 'desktop', 'build-identity.generated.json');
const FORMAT = 'ai-video-diary-candidate-inputs/v1';

const INPUT_SOURCES = [
  { identityPath: 'desktop/main.js', relativePath: 'apps/desktop/main.js' },
  { identityPath: 'desktop/device-identity.js', relativePath: 'apps/desktop/device-identity.js' },
  { identityPath: 'desktop/runtime', relativePath: 'apps/desktop/runtime', include: (relativePath) => relativePath.endsWith('.js') },
  { identityPath: 'desktop/preload.js', relativePath: 'apps/desktop/preload.js' },
  { identityPath: 'desktop/package.json', relativePath: 'apps/desktop/package.json' },
  { identityPath: 'desktop/assets', relativePath: 'apps/desktop/assets' },
  { identityPath: process.platform === 'win32' ? 'api-service' : 'api-server',
    relativePath: 'apps/desktop/dist/api-server' },
  { identityPath: 'programmatic', relativePath: 'apps/desktop/dist/programmatic' },
  { identityPath: 'model-workers', relativePath: 'apps/api/model_workers', include: (relativePath) => relativePath.endsWith('.py') },
  { identityPath: 'agent-tools', relativePath: 'apps/api/agent_tools', include: (relativePath) => relativePath.endsWith('.ts') },
  { identityPath: 'agent_runtime', relativePath: 'apps/api/agent_runtime', include: (relativePath) => !relativePath.startsWith('tests/') && /\.(mjs|md)$/.test(relativePath) },
  { identityPath: 'web', relativePath: 'apps/web/.next/standalone-bundled/apps/web' },
];

// String relational comparison is locale-independent and matches runtime/build-identity.js.
function compareIdentityPaths(left, right) {
  if (left === right) return 0;
  return left < right ? -1 : 1;
}

function sha256Buffer(buffer) {
  return crypto.createHash('sha256').update(buffer).digest('hex');
}

function relativePosix(root, filePath) {
  return path.relative(root, filePath).split(path.sep).join('/');
}

function listFiles(root, include = () => true) {
  if (!fs.existsSync(root)) {
    throw new Error(`Packaged identity input is missing: ${root}`);
  }
  const files = [];
  function walk(directory) {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true }).sort((a, b) => compareIdentityPaths(a.name, b.name))) {
      const filePath = path.join(directory, entry.name);
      if (entry.isSymbolicLink()) {
        throw new Error(`Packaged identity input must not contain a symlink: ${filePath}`);
      }
      if (entry.isDirectory()) walk(filePath);
      else if (entry.isFile() && include(relativePosix(root, filePath))) files.push(filePath);
    }
  }
  const stat = fs.lstatSync(root);
  if (stat.isSymbolicLink()) throw new Error(`Packaged identity input must not be a symlink: ${root}`);
  if (stat.isFile()) return include(path.basename(root)) ? [root] : [];
  walk(root);
  return files;
}

function collectInputFiles(repositoryRoot) {
  const records = [];
  const sources = process.platform === 'win32'
    ? [...INPUT_SOURCES, { identityPath: 'desktop/electron-builder.windows.cjs', relativePath: 'apps/desktop/electron-builder.windows.cjs' }]
    : INPUT_SOURCES;
  for (const source of sources) {
    const root = path.join(repositoryRoot, source.relativePath);
    const files = listFiles(root, source.include);
    const rootIsFile = fs.lstatSync(root).isFile();
    for (const filePath of files) {
      const relativePath = relativePosix(root, filePath);
      records.push({
        path: rootIsFile ? source.identityPath : `${source.identityPath}/${relativePath}`,
        filePath,
      });
    }
  }
  const ordered = records.sort((left, right) => compareIdentityPaths(left.path, right.path));
  if (new Set(ordered.map((record) => record.path)).size !== ordered.length) {
    throw new Error('Packaged identity input paths must be unique');
  }
  return ordered;
}

function createMetadata(records) {
  const ordered = [...records].sort((left, right) => compareIdentityPaths(left.path, right.path));
  const candidate = crypto.createHash('sha256');
  const buffer = Buffer.allocUnsafe(256 * 1024);
  const inputs = ordered.map((record) => {
    const fileHash = crypto.createHash('sha256');
    let bytes = 0;
    const consume = (content) => {
      candidate.update(content);
      fileHash.update(content);
      bytes += content.length;
    };
    candidate.update(record.path, 'utf8');
    candidate.update('\0');
    if (record.filePath !== undefined) {
      const descriptor = fs.openSync(record.filePath, 'r');
      try {
        let length;
        while ((length = fs.readSync(descriptor, buffer, 0, buffer.length, null)) !== 0) {
          consume(buffer.subarray(0, length));
        }
      } finally {
        fs.closeSync(descriptor);
      }
    } else {
      consume(Buffer.isBuffer(record.content) ? record.content : Buffer.from(record.content));
    }
    candidate.update('\0');
    return { path: record.path, sha256: fileHash.digest('hex'), bytes };
  });
  const manifestDigest = sha256Buffer(Buffer.from(JSON.stringify(inputs)));
  return {
    format: FORMAT,
    candidateInputDigest: candidate.digest('hex'),
    manifestDigest,
    inputs,
  };
}

function generateBuildIdentity({ repositoryRoot = REPO_ROOT, outputPath = DEFAULT_OUTPUT } = {}) {
  const metadata = createMetadata(collectInputFiles(repositoryRoot));
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, `${JSON.stringify(metadata, null, 2)}\n`, { mode: 0o644 });
  return metadata;
}

async function beforePack() {
  const metadata = generateBuildIdentity();
  console.log(`Generated candidate input identity ${metadata.candidateInputDigest}`);
}

module.exports = {
  FORMAT,
  INPUT_SOURCES,
  compareIdentityPaths,
  collectInputFiles,
  createMetadata,
  generateBuildIdentity,
  default: beforePack,
};

if (require.main === module) {
  const rootFlag = process.argv.indexOf('--root');
  const outputFlag = process.argv.indexOf('--output');
  const metadata = generateBuildIdentity({
    repositoryRoot: rootFlag === -1 ? REPO_ROOT : path.resolve(process.argv[rootFlag + 1]),
    outputPath: outputFlag === -1 ? DEFAULT_OUTPUT : path.resolve(process.argv[outputFlag + 1]),
  });
  console.log(metadata.candidateInputDigest);
}
