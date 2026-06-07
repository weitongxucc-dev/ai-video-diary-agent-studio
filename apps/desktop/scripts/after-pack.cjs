const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

module.exports = async function afterPack(context) {
  if (context.electronPlatformName !== "darwin") {
    return;
  }

  const appName = `${context.packager.appInfo.productFilename}.app`;
  const appPath = path.join(context.appOutDir, appName);
  if (!fs.existsSync(appPath)) {
    throw new Error(`Cannot ad-hoc sign missing macOS app bundle: ${appPath}`);
  }

  console.log(`Ad-hoc signing ${appPath}`);
  const result = spawnSync(
    "/usr/bin/codesign",
    ["--force", "--deep", "--sign", "-", appPath],
    { stdio: "inherit" },
  );
  if (result.status !== 0) {
    throw new Error(`Ad-hoc codesign failed for ${appPath}`);
  }
};
