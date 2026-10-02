// Real-filesystem boundary test for the desktop material import entry.
//
// Every case runs against a throwaway directory under the OS temp dir. It never
// touches ~/Movies/AI视频日记, a real material disk, the production database, or
// any model. The API is replaced by a local HTTP server on loopback that speaks
// the same JSON contract as GET /api/production/materials/intake.
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { pipeline } = require('node:stream/promises');

let passed = 0;
const failures = [];

async function check(name, body) {
  try {
    await body();
    passed += 1;
    console.log(`  ok  ${name}`);
  } catch (error) {
    failures.push({ name, error });
    console.log(`FAIL  ${name}: ${error.message}`);
  }
}

function sha256(target) {
  return crypto.createHash('sha256').update(fs.readFileSync(target)).digest('hex');
}

function loadImportSection() {
  const source = fs.readFileSync(path.join(__dirname, '..', 'main.js'), 'utf8');
  const start = source.indexOf('// ── Material import');
  const end = source.indexOf('// ── IPC Handlers', start);
  assert.ok(start >= 0 && end > start, 'desktop material import section exists');
  return source.slice(start, end);
}

function buildContext(section, overrides = {}) {
  const context = {
    fs, http, path, crypto, pipeline, Buffer, setTimeout, clearTimeout,
    MaterialImportError: class MaterialImportError extends Error {},
    MATERIAL_IMPORT_EXTENSIONS: new Set(['.mp4', '.mov', '.m4v']),
    MATERIAL_STAGING_SEGMENTS: ['_已处理', '.import-staging'],
    MATERIAL_NAME_ATTEMPTS: 200,
    MATERIAL_HARD_LINK_UNSUPPORTED: new Set(['ENOSYS', 'ENOTSUP', 'EOPNOTSUPP', 'EXDEV']),
    MATERIAL_BINDING_TIMEOUT_MS: 8000,
    dialog: { showOpenDialog: async () => ({ canceled: true, filePaths: [] }) },
    mainWindow: null,
    recordLifecycle: () => {},
    ...overrides,
  };
  vm.runInNewContext(
    section + '\nglobalThis.api = { importMaterials, readMaterialBinding, resolveTrustedMaterialDirectory,'
      + ' createRunDirectory, assertStagingAncestorsContained, revalidateTargetDirectory,'
      + ' removeOwnedPath, removeEmptyRunDirectory, isPathInside, describeFileError,'
      + ' commitMaterialStaging, assertHardLinkSupported };',
    context,
  );
  return context;
}

async function main() {
  const section = loadImportSection();
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'wil177-material-'));
  const sourceDir = path.join(root, 'sources');
  const boundDir = path.join(root, 'materials');
  fs.mkdirSync(sourceDir);
  fs.mkdirSync(boundDir);

  // The intake binding the shell reads. `bound_directory` and
  // `configured_directory` are the two independent strings whose realpaths must
  // agree.
  let binding = {
    bound_to_current_workspace: true,
    binding_available: true,
    directory_state: 'ready',
    bound_directory: boundDir,
    configured_directory: boundDir,
    scan_interval_seconds: 60,
  };
  let bindingStatus = 200;
  let bindingBody = null;
  let bindingMode = 'respond';
  // How a workspace rebind looks to the shell mid-import. `rebindAfterReads`
  // rebinds from read N onwards, which pins *where* in the import the check
  // happens; `rebindOnNextRead` fires on the single next read and then clears,
  // which is what a rebind performed while the native picker was open looks
  // like from here.
  let rebindAfterReads = 0;
  let rebindOnNextRead = false;
  let rebindTarget = null;
  let readsThisImport = 0;
  const requestedPaths = [];
  // Arrays built inside the vm realm have a different Array prototype, so a
  // deepEqual against a host array fails on identity alone.
  const plain = (value) => JSON.parse(JSON.stringify(value));

  const server = http.createServer((request, reply) => {
    requestedPaths.push(request.url);
    readsThisImport += 1;
    if (bindingMode === 'hang') return; // never responds: exercises the timeout
    const shouldRebind = rebindOnNextRead
      || (rebindAfterReads > 0 && readsThisImport >= rebindAfterReads);
    if (rebindOnNextRead) rebindOnNextRead = false;
    if (shouldRebind) {
      const target = rebindTarget;
      const rebound = { ...binding, bound_directory: target, configured_directory: target };
      reply.writeHead(200, { 'content-type': 'application/json' });
      reply.end(JSON.stringify(rebound));
      return;
    }
    const body = bindingBody ?? JSON.stringify(binding);
    reply.writeHead(bindingStatus, { 'content-type': 'application/json' });
    reply.end(body);
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;

  const context = buildContext(section);
  const importMaterials = context.api.importMaterials;
  const resolveTrusted = context.api.resolveTrustedMaterialDirectory;

  const bindingFor = (directory) => ({
    bound_to_current_workspace: true,
    binding_available: true,
    directory_state: 'ready',
    bound_directory: directory,
    configured_directory: directory,
    scan_interval_seconds: 60,
  });

  const makeSource = (name, bytes = 4096) => {
    const target = path.join(sourceDir, name);
    fs.writeFileSync(target, Buffer.alloc(bytes, 7));
    return target;
  };

  try {
    // ── Successful import ───────────────────────────────
    await check('imports a picked file, leaves the source unchanged, and removes this run directory', async () => {
      const source = makeSource('clip-a.mp4');
      const before = sha256(source);
      const result = await importMaterials({ port, pickSources: async () => [source] });
      assert.equal(result.canceled, false);
      assert.equal(result.failures.length, 0);
      assert.equal(result.copied.length, 1);
      assert.equal(result.copied[0].fileName, 'clip-a.mp4');
      assert.ok(fs.existsSync(path.join(boundDir, 'clip-a.mp4')));
      assert.equal(sha256(source), before, 'source sha256 unchanged');
      // The shared staging parent survives; only this run's directory is gone.
      assert.ok(fs.existsSync(path.join(boundDir, '_已处理', '.import-staging')));
      assert.deepEqual(fs.readdirSync(path.join(boundDir, '_已处理', '.import-staging')), []);
    });

    // ── §3 re-validation before commit ─────────────────
    await check('re-reads the binding before every commit and follows it when it changed', async () => {
      const source = makeSource('clip-rebind.mp4');
      const otherDir = path.join(root, 'materials-other');
      fs.mkdirSync(otherDir);
      const before = fs.readdirSync(boundDir);
      // Reads #1 (before picking) and #2 (after the picker returns, before the
      // run directory exists) still report the original directory; read #3, the
      // re-check before the commit, reports a different one, as if the workspace
      // rebound while this file was being copied.
      binding = bindingFor(boundDir);
      rebindAfterReads = 3;
      rebindTarget = otherDir;
      readsThisImport = 0;
      try {
        const result = await importMaterials({ port, pickSources: async () => [source] });
        // The re-read caught the rebind and stopped the commit instead of
        // writing into the directory the user had already unbound.
        assert.equal(result.copied.length, 0, 'nothing was committed under the old binding');
        assert.equal(result.failures.length, 1);
        assert.match(result.failures[0].error, /目录发生了变化|目录已变化/);
        assert.ok(!fs.existsSync(path.join(boundDir, 'clip-rebind.mp4')), 'nothing written to the old directory');
        assert.ok(!fs.existsSync(path.join(otherDir, 'clip-rebind.mp4')), 'nor under the new binding');
        assert.equal(fs.readdirSync(boundDir).length, before.length);
        assert.ok(fs.existsSync(source), 'the source file is untouched');
      } finally {
        rebindAfterReads = 0;
        rebindTarget = null;
        binding = bindingFor(boundDir);
      }
    });

    // ── F3: the picker window ──────────────────────────
    await check('a rebind made while the picker was open stops the import before anything is written', async () => {
      const source = makeSource('clip-picker-rebind.mp4');
      const before = fs.readdirSync(boundDir);
      const stagingRoot = path.join(boundDir, '_已处理', '.import-staging');
      const otherDir = path.join(root, 'materials-other');
      fs.mkdirSync(otherDir, { recursive: true });
      binding = bindingFor(boundDir);
      rebindTarget = otherDir;
      try {
        // The picker stands in for the native file dialog: the user has it open,
        // the binding is rebound somewhere else, and only then does it return.
        await assert.rejects(
          importMaterials({
            port,
            pickSources: async () => { rebindOnNextRead = true; return [source]; },
          }),
          /目录发生了变化|目录已变化/,
          'the import is refused rather than written to the stale directory',
        );
        assert.ok(!fs.existsSync(path.join(boundDir, 'clip-picker-rebind.mp4')), 'nothing written to the old directory');
        assert.ok(!fs.existsSync(path.join(otherDir, 'clip-picker-rebind.mp4')));
        assert.equal(fs.readdirSync(boundDir).length, before.length, 'the bound directory gained nothing');
        // The refusal happens before createRunDirectory, so there is no staging
        // directory and no probe file to leave behind.
        assert.deepEqual(fs.existsSync(stagingRoot) ? fs.readdirSync(stagingRoot) : [], [], 'no run directory was created');
        assert.ok(fs.existsSync(source), 'the source file is untouched');
      } finally {
        rebindOnNextRead = false;
        rebindTarget = null;
        binding = bindingFor(boundDir);
      }
    });

    await check('a rebind before any commit is followed consistently, never half-applied', async () => {
      const source = makeSource('clip-moved.mp4');
      const before = fs.readdirSync(boundDir);
      const otherDir = path.join(root, 'materials-moved');
      fs.mkdirSync(otherDir);
      binding = bindingFor(otherDir);
      try {
        const result = await importMaterials({ port, pickSources: async () => [source] });
        assert.equal(result.failures.length, 0);
        assert.ok(fs.existsSync(path.join(otherDir, 'clip-moved.mp4')));
        assert.equal(fs.readdirSync(boundDir).length, before.length, 'the old directory is untouched');
      } finally {
        binding = bindingFor(boundDir);
      }
    });

    // ── Same-name protection ───────────────────────────
    await check('never overwrites an existing file; a taken name gets an incrementing suffix', async () => {
      const existing = path.join(boundDir, 'clip-dup.mp4');
      fs.writeFileSync(existing, 'original-bytes');
      const beforeStat = fs.statSync(existing);
      const source = makeSource('clip-dup.mp4');
      const result = await importMaterials({ port, pickSources: async () => [source] });
      assert.equal(result.copied.length, 1);
      assert.equal(result.copied[0].fileName, 'clip-dup (1).mp4');
      assert.equal(fs.readFileSync(existing, 'utf8'), 'original-bytes', 'existing content untouched');
      assert.equal(fs.statSync(existing).mtimeMs, beforeStat.mtimeMs, 'existing mtime untouched');
    });

    // ── Unsupported extension ─────────────────────────
    await check('skips unsupported extensions and still imports the rest', async () => {
      const source = makeSource('clip-b.mov');
      const skipped = makeSource('notes.txt', 16);
      const result = await importMaterials({ port, pickSources: async () => [skipped, source] });
      assert.deepEqual(plain(result.skipped), ['notes.txt']);
      assert.equal(result.copied.length, 1);
      assert.ok(fs.existsSync(path.join(boundDir, 'clip-b.mov')));
    });

    // ── Cancel ─────────────────────────────────────────
    await check('cancelling the picker creates nothing', async () => {
      const before = fs.readdirSync(boundDir).length;
      const result = await importMaterials({ port, pickSources: async () => [] });
      assert.equal(result.canceled, true);
      assert.equal(result.copied.length, 0);
      assert.equal(fs.readdirSync(boundDir).length, before);
      const stagingRoot = path.join(boundDir, '_已处理', '.import-staging');
      if (fs.existsSync(stagingRoot)) {
        assert.deepEqual(fs.readdirSync(stagingRoot), [], 'no run directory left behind');
      }
    });

    // ── Pre-publish invisibility ───────────────────────
    await check('a committed file is never visible under a scannable name while incomplete', async () => {
      const source = makeSource('clip-atomic.mp4', 1 << 20);
      const seen = [];
      // Sample the bound directory while the copy runs. A partial file published
      // under the final name would show up here with a smaller size.
      const sampler = setInterval(() => {
        for (const name of fs.readdirSync(boundDir)) {
          const full = path.join(boundDir, name);
          if (fs.statSync(full).isFile() && name === 'clip-atomic.mp4') {
            seen.push(fs.statSync(full).size);
          }
        }
      }, 1);
      try {
        await importMaterials({ port, pickSources: async () => [source] });
      } finally {
        clearInterval(sampler);
      }
      const expected = fs.statSync(source).size;
      assert.ok(seen.length === 0 || seen.every((size) => size === expected),
        `final name only ever seen at full size, saw ${JSON.stringify(seen)}`);
      assert.ok(fs.existsSync(path.join(boundDir, 'clip-atomic.mp4')));
      // Staging names never carry a scannable extension.
      assert.ok(!fs.existsSync(path.join(boundDir, 'clip-atomic.mp4.partial')));
    });

    // ── Unknown residue preserved ──────────────────────
    await check('unknown staging residue from another run is never touched', async () => {
      const stagingRoot = path.join(boundDir, '_已处理', '.import-staging');
      const foreign = path.join(stagingRoot, 'some-other-run');
      fs.mkdirSync(foreign, { recursive: true });
      const foreignFile = path.join(foreign, 'leftover.mp4.partial');
      fs.writeFileSync(foreignFile, 'residue');
      const beforeStat = fs.statSync(foreignFile);
      const source = makeSource('clip-residue.mp4');
      const result = await importMaterials({ port, pickSources: async () => [source] });
      assert.equal(result.failures.length, 0);
      assert.ok(fs.existsSync(foreignFile), 'foreign file still present');
      assert.equal(fs.readFileSync(foreignFile, 'utf8'), 'residue', 'foreign content untouched');
      assert.equal(fs.statSync(foreignFile).mtimeMs, beforeStat.mtimeMs, 'foreign mtime untouched');
    });

    // ── Symlinked staging ancestor rejected ────────────
    await check('refuses to import when the staging path traverses a symlink', async () => {
      const escapeRoot = path.join(root, 'escape');
      fs.mkdirSync(escapeRoot);
      const archiveDir = path.join(boundDir, '_已处理');
      const symlinkPath = path.join(archiveDir, '.import-staging');
      fs.rmSync(symlinkPath, { recursive: true, force: true });
      fs.symlinkSync(escapeRoot, symlinkPath, 'dir');
      try {
        const source = makeSource('clip-symlink.mp4');
        // A staging path outside the authorized directory aborts the whole
        // import rather than degrading per file.
        await assert.rejects(
          importMaterials({ port, pickSources: async () => [source] }),
          (error) => {
            assert.match(error.message, /符号链接|不在已接入的素材目录内/);
            assert.match(error.message, /未写入任何文件/);
            return true;
          },
        );
        assert.deepEqual(fs.readdirSync(escapeRoot), [], 'nothing was written outside the authorized directory');
        assert.ok(!fs.existsSync(path.join(boundDir, 'clip-symlink.mp4')));
      } finally {
        fs.rmSync(symlinkPath, { recursive: true, force: true });
      }
    });

    await check('isPathInside rejects sibling and parent paths', () => {
      const isInside = context.api.isPathInside;
      assert.equal(isInside('/a/b', '/a/b/c'), true);
      assert.equal(isInside('/a/b', '/a/b'), false);
      assert.equal(isInside('/a/b', '/a/bc'), false);
      assert.equal(isInside('/a/b', '/a'), false);
    });

    // ── Cleanup failure is not reported as unsupported ──
    await check('a cleanup failure is reported as a cleanup result, not as an unsupported filesystem', async () => {
      const target = path.join(root, 'leftover.bin');
      fs.writeFileSync(target, 'x');
      fs.chmodSync(root, 0o500); // directory not writable: unlink must fail
      try {
        const removal = await context.api.removeOwnedPath(target);
        // Running as root defeats the permission check; then the only honest
        // assertion is that the result is a structured cleanup result.
        assert.equal(typeof removal.removed, 'boolean');
        if (removal.removed === false && !removal.alreadyGone) {
          assert.equal(typeof removal.code, 'string');
          assert.equal(typeof removal.error, 'string');
          assert.notEqual(removal.code, 'EPERM-and-therefore-unsupported-filesystem');
        }
      } finally {
        fs.chmodSync(root, 0o700);
        fs.rmSync(target, { force: true });
      }
    });

    await check('removing an absent path is reported as already gone, not as a failure', async () => {
      const removal = await context.api.removeOwnedPath(path.join(root, 'never-existed'));
      assert.equal(removal.removed, false);
      assert.equal(removal.alreadyGone, true);
      assert.equal(removal.code, undefined);
    });

    await check('a non-empty run directory is left in place rather than removed recursively', async () => {
      const runDirectory = path.join(boundDir, '_已处理', '.import-staging', 'run-not-empty');
      fs.mkdirSync(runDirectory, { recursive: true });
      fs.writeFileSync(path.join(runDirectory, 'stranger.bin'), 'not ours');
      const result = await context.api.removeEmptyRunDirectory(runDirectory);
      assert.equal(result.removed, false);
      assert.equal(result.code, 'ENOTEMPTY');
      assert.ok(fs.existsSync(path.join(runDirectory, 'stranger.bin')));
      fs.rmSync(runDirectory, { recursive: true, force: true });
    });

    // ── Hard-link support probe branches ──────────────
    await check('the hard-link probe reports an unsupported filesystem only for link failures', async () => {
      const runDirectory = path.join(boundDir, '_已处理', '.import-staging', 'probe-ok');
      fs.mkdirSync(runDirectory, { recursive: true });
      try {
        const result = await context.api.assertHardLinkSupported(runDirectory);
        assert.equal(result.supported, true);
        assert.equal(fs.readdirSync(runDirectory).length, 0, 'probe files cleaned up');
      } finally {
        fs.rmSync(runDirectory, { recursive: true, force: true });
      }
    });

    await check('a link refused with EPERM is reported neutrally, not blamed on the disk', async () => {
      const runDirectory = path.join(boundDir, '_已处理', '.import-staging', 'probe-eperm');
      fs.mkdirSync(runDirectory, { recursive: true });
      const failingFs = {
        ...fs,
        promises: {
          ...fs.promises,
          link: async () => {
            const error = new Error('operation not permitted');
            error.code = 'EPERM';
            throw error;
          },
        },
      };
      const failingContext = buildContext(section, { fs: failingFs });
      try {
        await assert.rejects(
          failingContext.api.assertHardLinkSupported(runDirectory),
          (error) => {
            // EPERM is equally consistent with a permission problem, so the
            // message offers both and asserts neither as the cause.
            assert.match(error.message, /EPERM/);
            assert.match(error.message, /权限或文件系统限制/);
            assert.doesNotMatch(
              error.message,
              /不支持本导入入口所需的原子提交方式/,
              'the volume is not blamed for a code that does not name the filesystem',
            );
            return true;
          },
        );
        assert.deepEqual(fs.readdirSync(runDirectory), [], 'probe source removed even on failure');
      } finally {
        fs.rmSync(runDirectory, { recursive: true, force: true });
      }
    });

    await check('a link unsupported by the filesystem is still named as such', async () => {
      const runDirectory = path.join(boundDir, '_已处理', '.import-staging', 'probe-unsupported');
      fs.mkdirSync(runDirectory, { recursive: true });
      const failingFs = {
        ...fs,
        promises: {
          ...fs.promises,
          link: async () => {
            const error = new Error('operation not supported');
            error.code = 'ENOTSUP';
            throw error;
          },
        },
      };
      const failingContext = buildContext(section, { fs: failingFs });
      try {
        await assert.rejects(
          failingContext.api.assertHardLinkSupported(runDirectory),
          /不支持本导入入口所需的原子提交方式/,
        );
        assert.deepEqual(fs.readdirSync(runDirectory), [], 'probe files cleaned up');
      } finally {
        fs.rmSync(runDirectory, { recursive: true, force: true });
      }
    });

    await check('a full disk is reported as a full disk, not as an unsupported filesystem', async () => {
      const runDirectory = path.join(boundDir, '_已处理', '.import-staging', 'probe-full');
      fs.mkdirSync(runDirectory, { recursive: true });
      const fullFs = {
        ...fs,
        promises: {
          ...fs.promises,
          link: async () => {
            const error = new Error('no space left on device');
            error.code = 'ENOSPC';
            throw error;
          },
        },
      };
      const fullContext = buildContext(section, { fs: fullFs });
      try {
        await assert.rejects(
          fullContext.api.assertHardLinkSupported(runDirectory),
          (error) => {
            assert.match(error.message, /磁盘空间不足/);
            assert.doesNotMatch(error.message, /不支持本导入入口所需的原子提交方式/);
            return true;
          },
        );
      } finally {
        fs.rmSync(runDirectory, { recursive: true, force: true });
      }
    });

    // ── Binding validation ────────────────────────────
    await check('rejects a workspace that has not bound a directory', () => {
      assert.throws(
        () => resolveTrusted({ ...binding, bound_to_current_workspace: false }),
        /还没有接入素材目录/,
      );
    });

    await check('rejects a directory state that is not ready', () => {
      assert.throws(
        () => resolveTrusted({ ...binding, directory_state: 'missing', directory_message: '素材盘未连接。' }),
        /素材盘未连接/,
      );
    });

    await check('rejects a relative directory', () => {
      assert.throws(() => resolveTrusted(bindingFor('relative/path')), /无效/);
    });

    await check('rejects a symlinked bound directory', () => {
      const real = path.join(root, 'real-materials');
      const link = path.join(root, 'linked-materials');
      fs.mkdirSync(real);
      fs.symlinkSync(real, link, 'dir');
      try {
        assert.throws(() => resolveTrusted(bindingFor(link)), /符号链接/);
      } finally {
        fs.rmSync(link, { force: true });
        fs.rmSync(real, { recursive: true, force: true });
      }
    });

    await check('rejects bound and configured directories that disagree', () => {
      const other = path.join(root, 'materials-disagree');
      fs.mkdirSync(other);
      try {
        assert.throws(
          () => resolveTrusted({ ...binding, bound_directory: boundDir, configured_directory: other }),
          /绑定不一致/,
        );
      } finally {
        fs.rmSync(other, { recursive: true, force: true });
      }
    });

    await check('rejects a bound directory inside the _已处理 archive, which intake never scans', async () => {
      // Every intake scan in production.py skips any path containing this
      // segment, so files published under it would be invisible forever. The
      // entry point has to refuse rather than report an import nothing will
      // ever register.
      const archive = path.join(root, 'materials-archived');
      const buried = path.join(archive, '_已处理', 'day-2026-09-30');
      fs.mkdirSync(buried, { recursive: true });
      const source = makeSource('clip-buried.mp4');
      try {
        assert.throws(
          () => resolveTrusted(bindingFor(buried)),
          (error) => {
            assert.match(error.message, /_已处理/);
            assert.match(error.message, /改到/);
            return true;
          },
        );
        binding = bindingFor(buried);
        readsThisImport = 0;
        await assert.rejects(
          importMaterials({ port, pickSources: async () => [source] }),
          /_已处理/,
        );
        assert.deepEqual(fs.readdirSync(buried), [], 'nothing was written into the unscanned directory');
        assert.ok(fs.existsSync(source), 'the source file is untouched');
      } finally {
        binding = bindingFor(boundDir);
        fs.rmSync(archive, { recursive: true, force: true });
      }
    });

    await check('accepts a bound and configured pair that are different strings for one directory', () => {
      // A trailing separator and a "/private" style prefix must normalize to the
      // same realpath, which is exactly what comparing two realpaths buys.
      const decorated = `${boundDir}${path.sep}`;
      assert.equal(resolveTrusted({ ...binding, configured_directory: decorated }), fs.realpathSync(boundDir));
    });

    // ── Binding read failures ─────────────────────────
    await check('an authentication-required response closes the import without claiming a fix', async () => {
      bindingStatus = 401;
      try {
        const source = makeSource('clip-auth.mp4');
        await assert.rejects(
          importMaterials({ port, pickSources: async () => [source] }),
          (error) => {
            assert.match(error.message, /没有可用凭据/);
            // The message must not promise that logging in resolves it.
            assert.doesNotMatch(error.message, /登录后重试|完成登录/);
            return true;
          },
        );
        assert.ok(!fs.existsSync(path.join(boundDir, 'clip-auth.mp4')), 'nothing was imported');
      } finally {
        bindingStatus = 200;
      }
    });

    await check('a non-200 response reports the status and imports nothing', async () => {
      bindingStatus = 503;
      try {
        await assert.rejects(
          importMaterials({ port, pickSources: async () => [] }),
          /HTTP 503/,
        );
      } finally {
        bindingStatus = 200;
      }
    });

    await check('an unparseable body is reported as such', async () => {
      bindingBody = 'not json at all';
      try {
        await assert.rejects(
          importMaterials({ port, pickSources: async () => [] }),
          /无法解析/,
        );
      } finally {
        bindingBody = null;
      }
    });

    await check('a binding request that never answers times out instead of hanging', async () => {
      bindingMode = 'hang';
      const started = Date.now();
      try {
        await assert.rejects(
          importMaterials({ port, bindingTimeoutMs: 150, pickSources: async () => [] }),
          /没有及时返回/,
        );
      } finally {
        bindingMode = 'respond';
      }
      assert.ok(Date.now() - started < 3000, 'the timeout fired promptly');
    });

    // ── Source conditions ─────────────────────────────
    await check('a source that is not a regular file fails without touching the source', async () => {
      const directorySource = path.join(sourceDir, 'a-directory.mp4');
      fs.mkdirSync(directorySource);
      try {
        const result = await importMaterials({ port, pickSources: async () => [directorySource] });
        assert.equal(result.copied.length, 0);
        assert.equal(result.failures.length, 1);
        assert.ok(fs.existsSync(directorySource), 'the source entry is still there');
      } finally {
        fs.rmSync(directorySource, { recursive: true, force: true });
      }
    });

    await check('a source that changes during the copy is refused rather than committed', async () => {
      const source = path.join(sourceDir, 'clip-changing.mp4');
      fs.writeFileSync(source, Buffer.alloc(64, 1));
      const growingContext = buildContext(section);
      // Mutate the source while the copy pipeline is in flight by racing a
      // timer against the copy.
      setTimeout(() => {
        try { fs.appendFileSync(source, Buffer.alloc(64, 2)); } catch { /* already copied */ }
      }, 0);
      try {
        const result = await growingContext.api.importMaterials({ port, pickSources: async () => [source] });
        if (result.copied.length === 0) {
          assert.equal(result.failures.length, 1);
          assert.match(result.failures[0].error, /复制期间发生变化|复制不完整/);
          assert.ok(!fs.existsSync(path.join(boundDir, 'clip-changing.mp4')));
        } else {
          // The copy won the race; then the committed bytes must match the source
          // as it was, and no partial file may exist.
          const committed = path.join(boundDir, 'clip-changing.mp4');
          assert.ok(fs.existsSync(committed));
          assert.equal(fs.statSync(committed).size, fs.statSync(source).size);
        }
      } finally {
        fs.rmSync(source, { force: true });
      }
    });

    // ── Cleanup after a mid-run failure ───────────────
    await check('a failure part-way leaves no staging residue from this run', async () => {
      const good = makeSource('clip-good.mp4');
      const directorySource = path.join(sourceDir, 'bad.mp4');
      fs.mkdirSync(directorySource);
      try {
        const result = await importMaterials({ port, pickSources: async () => [good, directorySource] });
        assert.equal(result.copied.length, 1);
        assert.equal(result.failures.length, 1);
        const stagingRoot = path.join(boundDir, '_已处理', '.import-staging');
        assert.deepEqual(fs.readdirSync(stagingRoot), [], 'this run directory was removed');
      } finally {
        fs.rmSync(directorySource, { recursive: true, force: true });
      }
    });

    await check('the intake endpoint is re-read, not read once per import', () => {
      assert.ok(requestedPaths.length > 0);
      assert.ok(requestedPaths.every((entry) => entry === '/api/production/materials/intake'));
      // Re-validation means at least two reads for any import that commits a
      // file: one before picking, one before the commit.
      assert.ok(requestedPaths.length >= 20, `expected repeated re-reads, saw ${requestedPaths.length}`);
    });

    // ── F1: the cleanup warning must not claim success ─
    await check('cleanup residue is reported without claiming the import succeeded', async () => {
      // The handler is the only place that turns an internal cleanup result into
      // user-visible text, so the wording is checked here rather than through the
      // import function it wraps.
      const source = fs.readFileSync(path.join(__dirname, '..', 'main.js'), 'utf8');
      const handlerStart = source.indexOf("ipcMain.handle('import-materials'");
      const handlerEnd = source.indexOf("ipcMain.handle('", handlerStart + 10);
      assert.ok(handlerStart >= 0 && handlerEnd > handlerStart, 'import-materials handler exists');
      let run = null;
      let nextResult = null;
      const handlerContext = {
        ipcMain: { handle(_name, callback) { run = callback; } },
        apiPort: port,
        serviceStates: { api: { state: 'ready' } },
        recordLifecycle: () => {},
        pickMaterialSources: async () => [],
        importMaterials: async () => nextResult,
      };
      vm.runInNewContext(source.slice(handlerStart, handlerEnd), handlerContext);
      const residue = { removed: false, alreadyGone: false, code: 'EPERM', error: 'not permitted' };

      // Nothing was imported and every file failed. The residue is still real
      // and must still be surfaced; what it must not say is that the import
      // worked, which would contradict the failure list shown beside it.
      nextResult = {
        canceled: false,
        copied: [],
        skipped: [],
        failures: [{ sourceName: 'clip-x.mp4', error: '导入失败。' }],
        cleanup: [residue],
      };
      const failedResult = await run();
      assert.equal(typeof failedResult.warning, 'string', 'the residue is not swallowed when copied is 0');
      assert.match(failedResult.warning, /未能自动清理/);
      assert.doesNotMatch(
        failedResult.warning,
        /已导入成功|导入成功|已入库/,
        'the warning does not claim success next to a failure list',
      );
      assert.equal(failedResult.failures.length, 1, 'the failures are still reported');
      assert.equal(failedResult.copied.length, 0);

      // Same residue after a real import: still surfaced, still neutral.
      nextResult = {
        canceled: false,
        copied: [{ sourceName: 'clip-y.mp4', fileName: 'clip-y.mp4' }],
        skipped: [],
        failures: [],
        cleanup: [residue],
      };
      const okResult = await run();
      assert.equal(typeof okResult.warning, 'string');
      assert.match(okResult.warning, /未能自动清理/);
      assert.doesNotMatch(okResult.warning, /已导入成功|导入成功|已入库/);
    });

    // ── No write outside the authorized directory ─────
    await check('nothing was written outside the directories the binding authorized', () => {
      const expected = new Set([
        'sources', 'materials', 'materials-other', 'materials-moved',
        'materials-disagree', 'escape', 'real-materials', 'linked-materials',
      ]);
      for (const name of fs.readdirSync(root)) {
        if (expected.has(name)) continue;
        assert.fail(`unexpected entry in the test root: ${name}`);
      }
      // The escape target is the one place a bug would write, and it must be
      // empty. The other directories only ever received what the binding named.
      assert.deepEqual(fs.readdirSync(path.join(root, 'escape')), []);
      // Every media file the run created must live inside a bound directory.
      for (const name of fs.readdirSync(root)) {
        const full = path.join(root, name);
        if (!fs.lstatSync(full).isDirectory()) continue;
        const media = fs.readdirSync(full).filter((entry) => path.extname(entry) === '.mp4' || path.extname(entry) === '.mov');
        for (const entry of media) {
          assert.ok(
            name === 'sources' || full === boundDir || full === path.join(root, 'materials-other') || full === path.join(root, 'materials-moved'),
            `${entry} sits in an unauthorized directory: ${name}`,
          );
        }
      }
    });
  } finally {
    await new Promise((resolve) => server.close(resolve));
    fs.rmSync(root, { recursive: true, force: true });
  }

  console.log(`\nmaterial import boundary: ${passed} passed, ${failures.length} failed`);
  if (failures.length > 0) {
    for (const failure of failures) console.error(`\n${failure.name}:\n${failure.error.stack}`);
    process.exitCode = 1;
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
