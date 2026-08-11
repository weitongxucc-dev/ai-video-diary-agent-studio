# 发布流程

## 版本号

版本号在 `apps/desktop/package.json` 的 `version` 字段定义（当前 `0.1.0`）。
Git tag 必须与 package.json version 一致，格式为 `v<version>`（如 `v0.1.0`）。

## 发布步骤

1. 确保本地在 `main` 分支且工作区干净
2. 更新 `apps/desktop/package.json` 中的 `version` 字段
3. 提交并推送：

```bash
git add apps/desktop/package.json
git commit -m "release: v0.1.0"
git push origin main
```

4. 打 tag 并推送：

```bash
git tag v0.1.0
git push origin v0.1.0
```

推送 tag 后，GitHub Actions 自动触发 `.github/workflows/release.yml`，构建并发布到 GitHub Releases。

## 产物

每次发布会在 GitHub Releases 页面生成：

| 文件 | 用途 |
|------|------|
| `AI视频日记 Agent Studio-<version>-arm64.dmg` | Apple Silicon Mac 安装包 |
| `AI视频日记 Agent Studio-<version>-arm64-mac.zip` | Apple Silicon 自动更新包 |
| `AI视频日记 Agent Studio-<version>-x64.dmg` | Intel Mac 安装包 |
| `AI视频日记 Agent Studio-<version>-x64-mac.zip` | Intel Mac 自动更新包 |
| `latest-mac.yml` | electron-updater 更新清单（自动检查更新依赖此文件） |

## 自动更新

应用启动后 10 秒静默检查更新；用户也可通过菜单「帮助 → 检查更新」手动触发。

electron-updater 读取 GitHub Release 上的 `latest-mac.yml`，比对本地版本号。
有新版本时弹出对话框提示下载，下载完成后重启即可完成更新。

**注意：** 自动更新仅在生产打包版本（`.app`/`.dmg`）中生效，
开发模式（`pnpm start` / `NODE_ENV=development`）不会检查更新。

## 签名与公证（未配置）

当前构建 **未配置** macOS 代码签名和 Apple 公证：

- 首次打开 .dmg 安装的 .app 时，macOS Gatekeeper 会提示「无法验证开发者」
- 解决方法：**右键（Control+点击）→ 打开**，确认后即可正常使用
- 只要不修改 .app 包内容，之后双击即可直接打开

自动更新功能 **不依赖** 代码签名 —— electron-updater 通过 HTTPS 下载 zip 后
替换 .app 包，此流程在未签名状态下正常工作。

如需签名公证，需要以下前置条件（不在当前范围内）：

1. Apple Developer Program 会员（$99/年）
2. Developer ID Application 证书（`codesign` 签名用）
3. App Store Connect API 密钥（`notarytool` 公证用）
4. 在 `apps/desktop/package.json` build.mac 中配置 `identity` 和 `notarize`

## 构建环境

GitHub Actions runner：`macos-latest`（Apple Silicon M1）

固定版本：
- pnpm `9.15.0`（`packageManager` 字段）
- Node.js `22`
- Python `3.11`

CI 使用 `pnpm install --frozen-lockfile` 确保依赖版本与 lock 文件一致。
推送 tag 前请确认 lock 文件已同步（通常提交 version bump 时同时提交）。
