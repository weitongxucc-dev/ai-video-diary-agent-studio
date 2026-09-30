// Isolated HTTP/temporary-file boundary test for the desktop subtitle downloader.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { pipeline } = require('node:stream/promises');

async function main() {
  const source = fs.readFileSync(path.join(__dirname, '..', 'main.js'), 'utf8');
  const start = source.indexOf('async function downloadSelectedSubtitles(');
  const end = source.indexOf('// ── IPC Handlers', start);
  assert.ok(start >= 0 && end > start, 'desktop subtitle downloader exists');
  const requested = [];
  let response = { status: 200, type: 'application/x-subrip; charset=utf-8', body: '1\n00:00:00,400 --> 00:00:02,800\n中文\nEnglish\n' };
  const server = http.createServer((request, reply) => {
    requested.push(request.url);
    reply.writeHead(response.status, { 'content-type': response.type });
    reply.end(response.body);
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'wil176-subtitle-'));
  try {
    const context = { fs, http, pipeline, URLSearchParams, Buffer, apiPort: server.address().port, serviceStates: { api: { state: 'ready' } } };
    vm.runInNewContext(source.slice(start, end) + '\nglobalThis.download = downloadSelectedSubtitles;', context);
    const selection = { taskId: 'task_export', expectedVersionId: 'version_2', mode: 'zh-en', zhTrackId: 'subtitle_zh', enTrackId: 'subtitle_en' };
    const saved = path.join(root, 'current.srt');
    await context.download(selection, saved);
    assert.equal(fs.readFileSync(saved, 'utf8'), response.body);
    assert.match(requested[0], /expected_version_id=version_2/);
    assert.match(requested[0], /zh_track_id=subtitle_zh/);
    assert.match(requested[0], /en_track_id=subtitle_en/);

    response = { status: 409, type: 'application/json', body: JSON.stringify({ detail: '当前字幕版本已变化，请重新选择后保存。' }) };
    await assert.rejects(context.download(selection, path.join(root, 'stale.srt')), /当前字幕版本已变化/);
    response = { status: 200, type: 'text/html', body: '<html>wrong output</html>' };
    await assert.rejects(context.download(selection, path.join(root, 'wrong-type.srt')), /没有返回可保存的字幕/);
    response = { status: 200, type: 'application/x-subrip', body: '' };
    await assert.rejects(context.download(selection, path.join(root, 'empty.srt')), /空的字幕文件/);

    let handler;
    let dialogResult = { canceled: true };
    let saveFailure = false;
    const ipcContext = {
      ipcMain: { handle(name, callback) { assert.equal(name, 'save-subtitle-file'); handler = callback; } },
      dialog: { showSaveDialog: async () => dialogResult },
      mainWindow: null,
      app: { getPath: () => root },
      path, fs, crypto: require('node:crypto'),
      recordLifecycle: () => {},
      downloadSelectedSubtitles: async (_selection, temporaryPath) => {
        fs.writeFileSync(temporaryPath, response.body || '1\n00:00:00,400 --> 00:00:02,800\n中文\nEnglish\n');
        if (saveFailure) throw new Error('磁盘写入失败');
      },
    };
    const handlerStart = source.indexOf("ipcMain.handle('save-subtitle-file'");
    const handlerEnd = source.indexOf("ipcMain.handle('open-path'", handlerStart);
    vm.runInNewContext(source.slice(handlerStart, handlerEnd), ipcContext);
    assert.deepEqual(JSON.parse(JSON.stringify(await handler(null, selection))), { saved: false, canceled: true });
    const destination = path.join(root, 'desktop-save.srt');
    dialogResult = { canceled: false, filePath: destination };
    const result = await handler(null, selection);
    assert.equal(result.saved, true);
    assert.equal(fs.readFileSync(destination, 'utf8'), '1\n00:00:00,400 --> 00:00:02,800\n中文\nEnglish\n');
    saveFailure = true;
    const failed = await handler(null, selection);
    assert.equal(failed.saved, false);
    assert.match(failed.error, /磁盘写入失败/);
    assert.equal(fs.readdirSync(root).filter((name) => name.includes('.partial-')).length, 0);
    console.log('subtitle desktop download boundary: passed');
  } finally {
    await new Promise((resolve) => server.close(resolve));
    fs.rmSync(root, { recursive: true, force: true });
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
