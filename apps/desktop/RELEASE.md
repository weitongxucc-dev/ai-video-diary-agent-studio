# 发布流程

## 版本号

版本号在 `apps/desktop/package.json` 的 `version` 字段定义（当前 `0.1.0`）。
Git tag 必须与 package.json version 一致，格式为 `v<version>`（如 `v0.1.0`）。

## 签名与自动更新

### 现状：未签名（仅可手工安装）

当前项目 **未配置** macOS 代码签名。未签名构建产物的能力边界：

| 场景 | 支持 | 说明 |
|------|------|------|
| 手工安装 .dmg | ✅ | 右键（Control+点击）→ 打开，确认后即可 |
| 自动更新 | ❌ | macOS 要求自动更新 .app 必须经过 Developer ID 签名 |
| Gatekeeper 静默通过 | ❌ | 未签名 app 会提示「无法验证开发者」 |

**未签名 app 的自动更新不可用。** electron-builder 官方文档明确：macOS 自动更新（Squirrel.Mac / electron-updater）的前置条件是 app 已使用 Developer ID 签名。在上述限制移除前，本 workflow 仍可安全构建未签名产物用于手工测试。

### 启用签名（生产自动更新的前置条件）

需要配置以下 repository secrets（Settings → Secrets and variables → Actions）：

| Secret | 说明 |
|--------|------|
| `CSC_LINK` | Developer ID Application 证书的 Base64 编码（.p12 或 .pfx） |
| `CSC_KEY_PASSWORD` | 证书导出时设置的密码 |

workflow 已通过环境变量引用这两个 secret：

```yaml
env:
  CSC_LINK: ${{ secrets.CSC_LINK }}
  CSC_KEY_PASSWORD: ${{ secrets.CSC_KEY_PASSWORD }}
```

secret 不存在或为空时，electron-builder 跳过签名（`--publish never` 模式下不报错）。

签名需要以下前置条件：

1. **Apple Developer Program** 会员（$99/年）
2. **Developer ID Application 证书**：Apple Developer 后台 → Certificates → Developer ID Application → 下载 .cer → 导入钥匙串 → 导出为 .p12

   ```bash
   # 导出证书为 Base64 字符串（用于 GitHub Secret）
   base64 -i cert.p12 | pbcopy
   ```

3. **公证（建议）**：App Store Connect API 密钥 + `notarytool`，在 electron-builder build.mac 中配置 `notarize` 字段

   公证需要的额外 secrets：

   | Secret | 说明 |
   |--------|------|
   | `APPLE_ID` | Apple Developer 账号邮箱 |
   | `APPLE_APP_SPECIFIC_PASSWORD` | App 专用密码 |
   | `APPLE_TEAM_ID` | Team ID（Developer 后台查看） |

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

推送 tag 后，GitHub Actions 自动触发 `.github/workflows/release.yml`：
两个矩阵 job 并行构建 arm64/x64 → 产物上传为 artifact → 单个 publish job 统一下载、生成 `latest-mac.yml`、创建 GitHub Release。

## 产物

每次发布会在 GitHub Releases 页面生成：

| 文件 | 用途 |
|------|------|
| `AI视频日记 Agent Studio-<version>-arm64.dmg` | Apple Silicon Mac 安装包 |
| `AI视频日记 Agent Studio-<version>-arm64-mac.zip` | Apple Silicon 自动更新包 |
| `AI视频日记 Agent Studio-<version>-x64.dmg` | Intel Mac 安装包 |
| `AI视频日记 Agent Studio-<version>-x64-mac.zip` | Intel Mac 自动更新包 |
| `latest-mac.yml` | electron-updater 更新清单（包含双架构文件引用） |

## latest-mac.yml 生成

`latest-mac.yml` 由 publish job 在单一步骤中生成，**不依赖** electron-builder 的部分输出。

生成逻辑（Node.js inline script）：
1. 从 arm64/x64 两个 artifact 中收集 .zip 文件
2. 对每个 .zip 计算 `crypto.createHash('sha512').digest('base64')` 和文件大小
3. 组装 YAML：`version` + `files[]` 数组（每个架构一个 entry，含 url/sha512/size）+ `path`/`sha512` 顶层字段

这避免了并行 `--publish always` 导致的 `latest-mac.yml` 竞争覆盖，保证双架构引用始终完整。

## 自动更新流程（签名后启用）

```
App 启动 → 10s 后 checkForUpdates()
         → GET <release>/latest-mac.yml
         → 版本比对 → 有新版本 → 弹窗提示
         → 下载 <arch>-mac.zip → 验证 sha512
         → 替换 .app → 重启 → 完成
```

## 构建环境

GitHub Actions 矩阵 runner：

| 架构 | Runner | PyInstaller 产出 | electron-builder 目标 |
|------|--------|------------------|----------------------|
| arm64 | `macos-latest` (Apple Silicon M1) | arm64 `api-server` | `--arm64` |
| x64 | `macos-13` (Intel) | x86_64 `api-server` | `--x64` |

固定版本：

- pnpm `9.15.0`（`packageManager` 字段）
- Node.js `22`
- Python `3.11`

CI 使用 `pnpm install --frozen-lockfile` 确保依赖版本与 lock 文件一致。
推送 tag 前请确认 lock 文件已同步（通常提交 version bump 时同时提交）。
