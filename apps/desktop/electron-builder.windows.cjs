const { build } = require('./package.json');
const path = require('node:path');

// This machine's unsigned internal preview. Keep the macOS release config separate.
module.exports = {
  ...build,
  appId: 'com.aivideodiary.studio.windows.preview',
  directories: { output: 'dist-windows' },
  // Reuse the project's installed Electron instead of downloading it again.
  electronDist: path.dirname(require('electron')),
  // Local unsigned previews prioritize iteration speed; no update feed is used.
  compression: 'store',
  artifactName: 'Yaohe-Studio-${version}-windows-${arch}-setup.${ext}',
  win: {
    target: [{ target: 'nsis', arch: ['x64'] }],
    icon: 'assets/icon.png',
    signAndEditExecutable: false,
  },
  nsis: {
    differentialPackage: false,
    oneClick: false,
    perMachine: false,
    allowToChangeInstallationDirectory: true,
    deleteAppDataOnUninstall: false,
    shortcutName: 'Yaohe Studio Windows Preview',
  },
  extraResources: build.extraResources.map((resource) => resource.from === 'dist/api-server'
    ? { ...resource, to: 'api-service' }
    : resource),
  publish: null,
};
