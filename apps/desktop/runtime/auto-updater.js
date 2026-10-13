const fs = require('fs');
const path = require('path');

function createAutoUpdater({ autoUpdater, dialog, getWindow, isPackaged, resourcesPath }) {
  function configureAutoUpdater() {
    autoUpdater.autoDownload = false;
    autoUpdater.autoInstallOnAppQuit = true;

    autoUpdater.on('checking-for-update', () => {
      console.log('[updater] Checking for updates...');
    });

    autoUpdater.on('update-available', (info) => {
      console.log('[updater] Update available:', info.version);
      dialog.showMessageBox(getWindow(), {
        type: 'info',
        title: '发现新版本',
        message: `新版本 ${info.version} 可用`,
        detail: '是否立即下载更新？',
        buttons: ['下载更新', '稍后提醒'],
        defaultId: 0,
        cancelId: 1,
      }).then(({ response }) => {
        if (response === 0) {
          autoUpdater.downloadUpdate();
        }
      });
    });

    autoUpdater.on('update-not-available', () => {
      console.log('[updater] No updates available.');
    });

    autoUpdater.on('download-progress', (progress) => {
      console.log(`[updater] Download: ${Math.round(progress.percent)}%`);
    });

    autoUpdater.on('update-downloaded', (info) => {
      console.log('[updater] Update downloaded:', info.version);
      dialog.showMessageBox(getWindow(), {
        type: 'info',
        title: '更新已就绪',
        message: `版本 ${info.version} 已下载完成`,
        detail: '重启应用以完成更新，或稍后在菜单中选择"检查更新"再重启。',
        buttons: ['立即重启', '稍后'],
        defaultId: 0,
        cancelId: 1,
      }).then(({ response }) => {
        if (response === 0) {
          autoUpdater.quitAndInstall();
        }
      });
    });

    autoUpdater.on('error', (err) => {
      console.error('[updater] Error:', err.message);
    });
  }

  function checkForUpdates(silent = true) {
    if (!isPackaged || !fs.existsSync(path.join(resourcesPath, 'app-update.yml'))) {
      if (!silent) {
        dialog.showMessageBox(getWindow(), {
          type: 'info',
          title: '本地测试版本',
          message: '此版本没有配置自动更新。',
          detail: '请使用新的测试包手动更新应用。',
        });
      }
      return;
    }
    autoUpdater.checkForUpdates().catch((err) => {
      if (!silent) {
        dialog.showErrorBox(
          '检查更新失败',
          `无法检查更新：${err.message}`
        );
      }
    });
  }


  return { configureAutoUpdater, checkForUpdates };
}

module.exports = { createAutoUpdater };
