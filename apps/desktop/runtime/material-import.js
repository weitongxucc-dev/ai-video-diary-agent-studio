const defaultFileSystem = require('fs');
const http = require('http');
const path = require('path');
const crypto = require('crypto');
const { pipeline } = require('stream/promises');

// Reading the intake binding is a loopback metadata call; a slow answer is
// treated as a closed import rather than an open-ended wait.
const MATERIAL_BINDING_TIMEOUT_MS = 8000;
// Mirrors apps/api/app/video.py SUPPORTED_VIDEO_EXTENSIONS. Anything else the
// user picks cannot be registered by intake, so it is reported as skipped
// instead of being copied into the monitored directory.
const MATERIAL_IMPORT_EXTENSIONS = new Set(['.mp4', '.mov', '.m4v']);
// `_已处理` is the archive segment apps/api/app/production.py already skips in
// all three scan paths, so staged copies are invisible to intake.
const MATERIAL_STAGING_SEGMENTS = ['_已处理', '.import-staging'];
const MATERIAL_NAME_ATTEMPTS = 200;


function createMaterialImporter({ recordLifecycle, fileSystem = defaultFileSystem }) {
  const fs = fileSystem;
  // ── Material import ─────────────────────────────────────
  // The renderer never supplies a destination. The shell reads the same
  // controlled binding intake already uses, so an arbitrary path from the web
  // layer can never become a write target (PRD §12, architecture §2.1/§7).

  class MaterialImportError extends Error {}

  function materialImportError(message) {
    return new MaterialImportError(message);
  }

  function httpMaterialJson(port, requestPath, timeoutMs, payload, identity = {}) {
    return new Promise((resolve, reject) => {
      let settled = false;
      const finish = (error, value) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        if (error) reject(error); else resolve(value);
      };
      const timer = setTimeout(() => {
        const error = materialImportError('本地服务没有及时返回素材接收结果，请重试。');
        request.destroy(error);
        finish(error);
      }, timeoutMs);
      const body = payload === undefined ? undefined : JSON.stringify(payload);
      const request = http.request({
        hostname: '127.0.0.1',
        port,
        path: requestPath,
        method: body === undefined ? 'GET' : 'POST',
        headers: {
          Accept: 'application/json',
          ...(identity.localMemberToken ? { 'X-Local-Member-Token': identity.localMemberToken } : {}),
          ...(identity.authorization ? { Authorization: identity.authorization } : {}),
          ...(body === undefined ? {} : { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) }),
        },
      }, (response) => {
        const chunks = [];
        response.on('data', (chunk) => chunks.push(chunk));
        response.on('end', () => finish(null, {
          status: response.statusCode || 0,
          body: Buffer.concat(chunks).toString('utf8'),
        }));
        response.on('error', (error) => finish(error));
      });
      request.on('error', (error) => finish(error));
      request.end(body);
    });
  }

  async function readMaterialBinding(port, timeoutMs = MATERIAL_BINDING_TIMEOUT_MS, identity = {}) {
    const response = await httpMaterialJson(port, '/api/production/materials/intake', timeoutMs, undefined, identity);
    if (response.status === 401 || response.status === 403) {
      throw materialImportError('本地服务要求登录，桌面端当前没有可用凭据，无法确认素材接入目录。本次未导入，源文件保持不变。');
    }
    if (response.status !== 200) {
      throw materialImportError(`无法确认素材接入目录（HTTP ${response.status}），已取消本次导入。`);
    }
    let binding;
    try {
      binding = JSON.parse(response.body);
    } catch {
      throw materialImportError('本地服务返回的素材接入目录状态无法解析，已取消本次导入。');
    }
    return binding;
  }

  async function confirmImportedMaterial(port, committedPath, timeoutMs, identity) {
    const stat = await fs.promises.stat(committedPath, { bigint: true });
    const payload = {
      relative_path: path.basename(committedPath),
      expected_revision: {
        size_bytes: stat.size.toString(), mtime_ns: stat.mtimeNs.toString(),
        ctime_ns: stat.ctimeNs.toString(), inode: stat.ino.toString(),
      },
      confirmation_source: 'app_import',
    };
    const response = await httpMaterialJson(
      port, '/api/production/materials/intake/copy-complete', timeoutMs, payload, identity,
    );
    if (response.status !== 200) {
      throw materialImportError(`接收确认失败（HTTP ${response.status}）。请在素材页确认接收。`);
    }
    let result;
    try {
      result = JSON.parse(response.body);
    } catch {
      throw materialImportError('接收确认结果无法解析，请在素材页确认接收。');
    }
    if (result.confirmed !== true) {
      throw materialImportError('服务端未确认接收，请在素材页确认接收。');
    }
  }

  // The API already resolves disk availability (`directory_state`), including the
  // "a leftover /Volumes/<name> directory is not evidence that its disk is
  // mounted" rule. The shell re-checks identity and containment so a stale answer
  // cannot turn into a write outside the bound directory.
  function resolveTrustedMaterialDirectory(binding) {
    if (!binding || binding.bound_to_current_workspace !== true) {
      throw materialImportError('当前工作区还没有接入素材目录，请先接入后再导入。');
    }
    if (binding.directory_state !== 'ready') {
      throw materialImportError(binding.directory_message || '素材目录当前不可用，已取消本次导入。');
    }
    const bound = typeof binding.bound_directory === 'string' ? binding.bound_directory : '';
    const configured = typeof binding.configured_directory === 'string' ? binding.configured_directory : '';
    const directory = bound || configured;
    if (typeof directory !== 'string' || directory.length === 0 || !path.isAbsolute(directory)) {
      throw materialImportError('素材接入目录无效，已取消本次导入。');
    }
    let real;
    try {
      if (fs.lstatSync(directory).isSymbolicLink()) {
        throw materialImportError('素材接入目录是符号链接，已取消本次导入。');
      }
      real = fs.realpathSync(directory);
      if (!fs.statSync(real).isDirectory()) {
        throw materialImportError('素材接入目录已不可用，已取消本次导入。');
      }
      // Every intake scan path in apps/api/app/production.py (scan_library at
      // :2611, discover_material_hash_candidates at :2719, scan_material_ingestion
      // at :3719) skips any path containing the `_已处理` segment. A directory
      // under such a segment is therefore never picked up: files published there
      // would be invisible to intake forever. Refuse at the entry point instead
      // of reporting a successful import that nothing will ever register.
      if (real.split(path.sep).includes('_已处理')) {
        throw materialImportError('素材接入目录位于「_已处理」归档目录内，本地服务不会扫描该目录，导入的素材不会被登记。请把接入目录改到「_已处理」之外再导入。');
      }
      // The API reports the bound directory and the configured directory as two
      // independent strings. Resolving both and comparing the two realpaths is a
      // genuine consistency check that they name one directory. Taking the
      // realpath of a single path and comparing it to itself would be an identity
      // and would prove nothing, so no such comparison is made here.
      if (bound.length > 0 && configured.length > 0) {
        const boundReal = fs.realpathSync(bound);
        const configuredReal = fs.realpathSync(configured);
        if (boundReal !== configuredReal) {
          throw materialImportError('素材接入目录与本地服务的绑定不一致，已取消本次导入。');
        }
      }
    } catch (error) {
      if (error instanceof MaterialImportError) throw error;
      throw materialImportError('素材接入目录已不可用，已取消本次导入。');
    }
    return real;
  }

  // Only a filesystem that genuinely cannot create hard links may be reported as
  // "this disk does not support the required atomic commit". Every other failure
  // is a different problem and must be described as such, otherwise a full disk or
  // a permission error would be misreported as an unsupported filesystem.
  // Codes that name the filesystem as the reason. EPERM is deliberately absent:
  // it is what a permission problem looks like too, so it is reported separately
  // rather than blamed on the volume.
  const MATERIAL_HARD_LINK_UNSUPPORTED = new Set([
    'ENOSYS', 'ENOTSUP', 'EOPNOTSUPP', 'EXDEV',
  ]);

  function isMissingPath(error) {
    return error && error.code === 'ENOENT';
  }

  // Removing a path this run created must never hide a real failure: ENOENT means
  // the path is already gone, anything else is returned to the caller.
  async function removeOwnedPath(target) {
    try {
      await fs.promises.unlink(target);
      return { removed: true };
    } catch (error) {
      if (isMissingPath(error)) return { removed: false, alreadyGone: true };
      return {
        removed: false,
        alreadyGone: false,
        code: error.code || 'unknown',
        error: error.message || '临时文件清理失败。',
      };
    }
  }

  async function assertHardLinkSupported(runDirectory) {
    const probeSource = path.join(runDirectory, '.probe-source');
    const probeTarget = path.join(runDirectory, '.probe-target');
    const cleanup = [];
    let failure = null;
    try {
      try {
        // 'wx' refuses to open an existing path, so the probe can never append to
        // or overwrite a file it does not own.
        await fs.promises.writeFile(probeSource, 'probe', { flag: 'wx' });
      } catch (error) {
        // The run directory was just created, so a failure to write here is a
        // volume or permission problem, not a hard-link limitation.
        throw materialImportError(
          `无法在已接入的素材目录中创建临时文件（${describeFileError(error)}），本次未导入，源文件保持不变。`,
        );
      }
      try {
        await fs.promises.link(probeSource, probeTarget);
      } catch (error) {
        if (MATERIAL_HARD_LINK_UNSUPPORTED.has(error.code)) {
          throw materialImportError('该素材盘的文件系统不支持本导入入口所需的原子提交方式，本次未导入，源文件保持不变。');
        }
        if (error.code === 'EPERM') {
          // EPERM is not evidence about the filesystem. It is what a permission
          // problem looks like just as often as what a link-hostile volume looks
          // like, and the two need different fixes. The message states what was
          // observed and what it might mean, and stops there.
          throw materialImportError('当前目录无法创建硬链接（EPERM，可能是权限或文件系统限制），本次未导入，源文件保持不变。');
        }
        if (error.code === 'ENOENT') {
          throw materialImportError('已接入的素材目录在导入过程中消失，本次未导入，源文件保持不变。');
        }
        throw materialImportError(
          `无法在已接入的素材目录中完成原子提交（${describeFileError(error)}），本次未导入，源文件保持不变。`,
        );
      }
    } catch (error) {
      failure = error;
    } finally {
      cleanup.push(await removeOwnedPath(probeTarget));
      cleanup.push(await removeOwnedPath(probeSource));
    }
    if (failure) throw failure;
    const cleanupFailed = cleanup.filter((entry) => entry.removed === false && !entry.alreadyGone);
    if (cleanupFailed.length > 0) {
      recordLifecycle('material-import-probe-cleanup-failed', {
        codes: cleanupFailed.map((entry) => entry.code).join(','),
      });
    }
    return { supported: true, cleanup };
  }

  function describeFileError(error) {
    if (!error) return '未知错误';
    const code = error.code || '';
    if (code === 'ENOSPC' || code === 'EDQUOT') return '磁盘空间不足';
    if (code === 'EACCES' || code === 'EPERM') return '没有写入权限';
    if (code === 'EROFS') return '磁盘是只读的';
    if (code === 'ENOENT') return '目录已不存在';
    if (code === 'EIO') return '磁盘读写出错';
    if (code === 'EBUSY') return '磁盘正忙';
    if (code === 'ENAMETOOLONG') return '文件名过长';
    if (code) return `${code}`;
    return error.message || '未知错误';
  }

  // Reading EOF only means the copy reached the source's current end. It says
  // nothing about whether an external writer is still appending, so the source
  // must be unchanged across the copy before anything is committed.
  async function copyMaterialSourceToStaging(sourcePath, stagingPath) {
    let before;
    try {
      before = await fs.promises.stat(sourcePath, { bigint: true });
    } catch (error) {
      throw materialImportError(`源文件不可读（${describeFileError(error)}），本次未导入。`);
    }
    if (!before.isFile()) {
      throw materialImportError('源文件已不可读，本次未导入。');
    }
    try {
      await pipeline(
        fs.createReadStream(sourcePath, { flags: 'r' }),
        fs.createWriteStream(stagingPath, { flags: 'wx' }),
      );
    } catch (error) {
      throw materialImportError(`复制失败（${describeFileError(error)}），本次未导入，源文件保持不变。`);
    }
    const after = await fs.promises.stat(sourcePath, { bigint: true });
    if (before.size !== after.size || before.mtimeNs !== after.mtimeNs || before.ino !== after.ino) {
      throw materialImportError('源文件在复制期间发生变化，无法确认复制完成，本次未导入，源文件保持不变。');
    }
    const written = (await fs.promises.stat(stagingPath, { bigint: true })).size;
    if (written !== before.size) {
      throw materialImportError('源文件复制不完整，本次未导入，源文件保持不变。');
    }
  }

  // A hard link publishes the finished inode under the final name in one step,
  // so the monitored directory never contains a partially written file under a
  // scannable extension. Existence and creation are the same atomic call, so a
  // name that is already taken fails instead of overwriting.
  async function commitMaterialStaging(stagingPath, targetDirectory, fileName) {
    const extension = path.extname(fileName);
    const stem = fileName.slice(0, fileName.length - extension.length);
    for (let attempt = 0; attempt < MATERIAL_NAME_ATTEMPTS; attempt += 1) {
      const candidate = attempt === 0 ? fileName : `${stem} (${attempt})${extension}`;
      const candidatePath = path.join(targetDirectory, candidate);
      try {
        await fs.promises.link(stagingPath, candidatePath);
      } catch (error) {
        if (error.code === 'EEXIST') continue;
        if (MATERIAL_HARD_LINK_UNSUPPORTED.has(error.code)) {
          throw materialImportError('该素材盘的文件系统不支持本导入入口所需的原子提交方式，本次未导入，源文件保持不变。');
        }
        if (isMissingPath(error)) {
          throw materialImportError('已接入的素材目录在导入过程中消失，本次未导入，源文件保持不变。');
        }
        throw materialImportError(
          `无法在已接入的素材目录中提交文件（${describeFileError(error)}），本次未导入，源文件保持不变。`,
        );
      }
      // The file is already published under its final name. If the staging name
      // cannot be removed the import still succeeded, so the leftover is reported
      // rather than silently dropped and never turned into a failure message.
      const removal = await removeOwnedPath(stagingPath);
      if (removal.removed === false && !removal.alreadyGone) {
        recordLifecycle('material-import-staging-left-behind', { code: removal.code });
      }
      return candidatePath;
    }
    throw materialImportError('目标目录中同名文件过多，本次未导入，源文件保持不变。');
  }

  // Every segment between the trusted root and the staging root must already be a
  // real directory inside the root. A pre-existing `_已处理/.import-staging`
  // symlink would otherwise redirect both the write and the cleanup outside the
  // authorized directory, so this is checked before any subpath is created, not
  // only for the root itself.
  async function assertStagingAncestorsContained(trustedRoot, stagingRoot) {
    const relative = path.relative(trustedRoot, stagingRoot);
    if (relative.length === 0 || relative.startsWith('..') || path.isAbsolute(relative)) {
      throw materialImportError('暂存位置不在已接入的素材目录内，已取消本次导入。');
    }
    let current = trustedRoot;
    for (const segment of relative.split(path.sep)) {
      current = path.join(current, segment);
      let entry;
      try {
        entry = await fs.promises.lstat(current);
      } catch (error) {
        // Anything past the first missing segment does not exist yet, so there is
        // nothing left to contain.
        if (isMissingPath(error)) return;
        throw materialImportError(`无法确认暂存位置（${describeFileError(error)}），已取消本次导入。`);
      }
      if (entry.isSymbolicLink()) {
        throw materialImportError('暂存位置存在符号链接，已取消本次导入，未写入任何文件。');
      }
      if (!entry.isDirectory()) {
        throw materialImportError('暂存位置不是目录，已取消本次导入，未写入任何文件。');
      }
      const resolved = await fs.promises.realpath(current);
      if (!isPathInside(trustedRoot, resolved)) {
        throw materialImportError('暂存位置不在已接入的素材目录内，已取消本次导入，未写入任何文件。');
      }
    }
  }

  function isPathInside(rootPath, candidatePath) {
    const relative = path.relative(rootPath, candidatePath);
    return relative.length > 0
      && !relative.startsWith('..')
      && !path.isAbsolute(relative);
  }

  async function createRunDirectory(trustedRoot, runId) {
    const stagingRoot = path.join(trustedRoot, ...MATERIAL_STAGING_SEGMENTS);
    await assertStagingAncestorsContained(trustedRoot, stagingRoot);
    const runDirectory = path.join(stagingRoot, runId);
    try {
      await fs.promises.mkdir(runDirectory, { recursive: true });
    } catch (error) {
      throw materialImportError(
        `无法在已接入的素材目录中创建暂存位置（${describeFileError(error)}），本次未导入，源文件保持不变。`,
      );
    }
    // Containment is confirmed against the real path after creation, so a
    // symlinked ancestor that appeared between the check and the mkdir is still
    // caught before any bytes are written into it.
    let resolvedRun;
    try {
      resolvedRun = await fs.promises.realpath(runDirectory);
    } catch (error) {
      throw materialImportError('暂存位置无法确认，已取消本次导入。');
    }
    if (!isPathInside(trustedRoot, resolvedRun)) {
      throw materialImportError('暂存位置不在已接入的素材目录内，已取消本次导入。');
    }
    return runDirectory;
  }

  // The binding is re-read and re-validated immediately before every commit. A
  // long copy or a slow native picker can span a rebind, a volume swap, or a
  // directory rename, and writing to the directory chosen at the start would then
  // be a write the current binding never authorized.
  async function revalidateTargetDirectory(port, bindingTimeoutMs, expected, identity) {
    let current;
    try {
      current = await readMaterialBinding(port, bindingTimeoutMs, identity);
    } catch (error) {
      throw materialImportError(
        `导入过程中无法再次确认素材接入目录（${error.message || '本地服务没有响应'}），已停止导入，源文件保持不变。`,
      );
    }
    let resolved;
    try {
      resolved = resolveTrustedMaterialDirectory(current);
    } catch (error) {
      throw materialImportError(
        `导入过程中素材接入目录已变化（${error.message || '目录状态异常'}），已停止导入，源文件保持不变。`,
      );
    }
    if (resolved !== expected) {
      throw materialImportError('导入过程中素材接入目录发生了变化，已停止导入。请确认目录后再试，源文件保持不变。');
    }
    return resolved;
  }

  // Only this run's own directory is removed. The shared `.import-staging` parent
  // is not owned by this run and is always left in place, so removing it here
  // would delete a directory other runs and earlier residue live in.
  async function removeEmptyRunDirectory(runDirectory) {
    try {
      await fs.promises.rmdir(runDirectory);
      return { path: runDirectory, removed: true };
    } catch (error) {
      if (isMissingPath(error)) return { path: runDirectory, removed: false, alreadyGone: true };
      if (error.code === 'ENOTEMPTY') {
        // Another path landed in this run's directory; it is left for the next run
        // to find as unknown residue rather than deleted recursively.
        return { path: runDirectory, removed: false, code: 'ENOTEMPTY' };
      }
      return { path: runDirectory, removed: false, code: error.code || 'unknown' };
    }
  }

  async function importMaterials(options) {
    const { port, pickSources, bindingTimeoutMs = MATERIAL_BINDING_TIMEOUT_MS, identity = {} } = options;
    const binding = await readMaterialBinding(port, bindingTimeoutMs, identity);
    const targetDirectory = resolveTrustedMaterialDirectory(binding);
    const picked = await pickSources();
    if (!Array.isArray(picked) || picked.length === 0) {
      return { canceled: true, directory: targetDirectory, copied: [], skipped: [], failures: [], cleanup: [] };
    }
    const runId = crypto.randomUUID();
    const createdFiles = [];
    let runDirectory = null;
    const copied = [];
    const skipped = [];
    const failures = [];
    const cleanup = [];
    try {
      // The native picker is a modal the user may leave open for minutes, and it
      // is the longest uncontrolled window in this flow: the binding can be
      // rebound or the disk can be replaced while it is up. The directory read at
      // the top of this function is therefore stale the moment the picker returns.
      // Re-read and re-resolve it before anything is written — the run directory,
      // the hard-link probe, and the first staged copy all come after this point.
      // A changed target aborts the import with zero writes; the re-check before
      // each commit covers the rest of the run, not this window.
      await revalidateTargetDirectory(port, bindingTimeoutMs, targetDirectory, identity);
      runDirectory = await createRunDirectory(targetDirectory, runId);
      await assertHardLinkSupported(runDirectory);
      for (const sourcePath of picked) {
        const fileName = path.basename(sourcePath);
        if (!MATERIAL_IMPORT_EXTENSIONS.has(path.extname(fileName).toLowerCase())) {
          skipped.push(fileName);
          continue;
        }
        const stagingPath = path.join(runDirectory, `${runId}-${createdFiles.length}-${fileName}.partial`);
        createdFiles.push(stagingPath);
        let committed;
        try {
          await copyMaterialSourceToStaging(sourcePath, stagingPath);
          await revalidateTargetDirectory(port, bindingTimeoutMs, targetDirectory, identity);
          committed = await commitMaterialStaging(stagingPath, targetDirectory, fileName);
          await confirmImportedMaterial(port, committed, bindingTimeoutMs, identity);
          copied.push({ sourceName: fileName, fileName: path.basename(committed) });
        } catch (error) {
          failures.push({ sourceName: fileName, error: committed
            ? `文件已复制为“${path.basename(committed)}”，但接收确认未保存：${error.message}`
            : error.message || '导入失败。' });
        }
      }
    } finally {
      // Only this run's own paths are removed. Residue from an earlier run or
      // another process is never traversed, matched, or deleted. Removal failures
      // are collected as results instead of being swallowed.
      for (const owned of createdFiles) {
        cleanup.push(await removeOwnedPath(owned));
      }
      if (runDirectory) {
        cleanup.push(await removeEmptyRunDirectory(runDirectory));
      }
      const cleanupFailed = cleanup.filter((entry) => entry.removed === false && !entry.alreadyGone);
      if (cleanupFailed.length > 0) {
        recordLifecycle('material-import-cleanup-incomplete', {
          codes: cleanupFailed.map((entry) => entry.code || 'unknown').join(','),
          count: cleanupFailed.length,
        });
      }
    }
    return {
      canceled: false,
      directory: targetDirectory,
      copied,
      skipped,
      failures,
      cleanup: cleanup.filter((entry) => entry.removed === false && !entry.alreadyGone),
    };
  }


  return { importMaterials, resolveTrustedMaterialDirectory, removeOwnedPath, removeEmptyRunDirectory, isPathInside, assertHardLinkSupported };
}

module.exports = { createMaterialImporter };
