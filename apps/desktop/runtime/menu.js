const path = require('path');
const os = require('os');

function buildMenu({ app, Menu, dialog, shell, getWindow, checkForUpdates, buildIdentity }) {
  const isMac = process.platform === 'darwin';

  const template = [
    ...(isMac ? [{
      label: app.name,
      submenu: [
        { role: 'about' },
        { type: 'separator' },
        { role: 'services' },
        { type: 'separator' },
        { role: 'hide' },
        { role: 'hideOthers' },
        { role: 'unhide' },
        { type: 'separator' },
        { role: 'quit' },
      ],
    }] : []),
    {
      label: '文件',
      submenu: [
        {
          label: '打开视频目录',
          click: () => {
            const videoPath = path.join(os.homedir(), 'Movies', 'AI视频日记', '01_待处理视频');
            shell.openPath(videoPath);
          },
        },
        {
          label: '打开知识库',
          click: () => {
            dialog.showOpenDialog(getWindow(), {
              properties: ['openDirectory'],
              title: '选择 Obsidian Vault',
            });
          },
        },
        { type: 'separator' },
        isMac ? { role: 'close' } : { role: 'quit' },
      ],
    },
    {
      label: '编辑',
      submenu: [
        { role: 'undo' },
        { role: 'redo' },
        { type: 'separator' },
        { role: 'cut' },
        { role: 'copy' },
        { role: 'paste' },
        { role: 'selectAll' },
      ],
    },
    {
      label: '视图',
      submenu: [
        { role: 'reload' },
        { role: 'forceReload' },
        { role: 'toggleDevTools' },
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' },
      ],
    },
    {
      label: '帮助',
      submenu: [
        {
          label: '曜核AI剪辑 帮助',
          click: () => {
            getWindow()?.webContents.send('open-help');
          },
        },
        { type: 'separator' },
        {
          label: '检查更新...',
          click: () => {
            checkForUpdates(false);
          },
        },
        { type: 'separator' },
        {
          label: '关于曜核AI剪辑',
          click: () => {
            dialog.showMessageBox(getWindow(), {
              type: 'info',
              title: '关于曜核AI剪辑',
              message: `AI视频日记 Agent Studio v${app.getVersion()}`,
              detail: `构建身份：${buildIdentity}\n\n本地优先的 macOS 内容生产 Agent。\n\n将素材、想法和语音指令推进成可验证、可回滚、可导入剪映的完整视频草稿。\n\n© 2026 AI视频日记 Contributors\nMIT License`,
            });
          },
        },
      ],
    },
  ];

  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}


module.exports = { buildMenu };
