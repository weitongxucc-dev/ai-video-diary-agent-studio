const fs = require('fs');

function createNativeFiles({ dialog, shell, getWindow }) {
  async function choose(options) {
    const result = await dialog.showOpenDialog(getWindow(), options);
    if (result.canceled || result.filePaths.length === 0) return null;
    return result.filePaths[0];
  }
  function openDirectory() { return choose({ properties: ['openDirectory'], title: '选择目录' }); }
  function openFile() { return choose({ properties: ['openFile'], title: '选择文件' }); }
  function openWavFile() {
    return choose({ properties: ['openFile'], title: '选择授权声音参考音频', filters: [{ name: 'WAV 音频', extensions: ['wav'] }] });
  }
  async function openPath(filePath) {
    if (typeof filePath !== 'string' || filePath.length === 0) throw new Error('invalid path');
    if (!fs.existsSync(filePath)) throw new Error(`path not found: ${filePath}`);
    return shell.openPath(filePath);
  }
  async function pickMaterialSources() {
    const result = await dialog.showOpenDialog(getWindow(), {
      properties: ['openFile', 'multiSelections'],
      title: '选择要导入的本地素材',
      filters: [{ name: '视频素材', extensions: ['mp4', 'mov', 'm4v'] }],
    });
    if (result.canceled || result.filePaths.length === 0) return [];
    return result.filePaths;
  }


  return { openDirectory, openFile, openWavFile, openPath, pickMaterialSources };
}

module.exports = { createNativeFiles };
