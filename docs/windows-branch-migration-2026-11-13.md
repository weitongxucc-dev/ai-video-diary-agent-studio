# Windows 分支迁移记录（2026-11-13）

## 结论

Windows 以后只在 `codex/windows-integration-1.5.2` 开发。该分支已基于 GitHub `main` 的 `be1c52e1d788cd3828633b16d4c80b82a0438b8d` 更新，当前远端提交为 `80b31e3af895f581008f8034ae999add462770d3`。

## 已迁移

- `7565ca3f` 的桌面 Windows 预览安装器链路：electron-builder Windows 配置、API/Web 构建脚本、PyInstaller onedir、Windows standalone 资源链接和 `scripts/windows-env.ps1`。
- 旧分支中仍有价值的 Windows 打包硬化：构建身份分块读取、Windows 凭据隐藏导入、测试结束时释放 SQLite 文件句柄、Windows 后端实验探针 `scripts/windows_backend_probe.py`。
- `4799d385` 中唯一尚未进入 canonical 的开发入口：`apps/desktop/scripts/dev.js`，并将 `apps/desktop` 的 `dev` 命令改为跨平台入口。
- `7b001dd9` 已经包含旧分支的 Windows 平台文件、进程归属、Pi 运行时检查和工作区凭据能力；这些内容没有再次重放旧提交。

## 未迁移及原因

- `4799d385`、`e3563f66` 的 API/runtime 文件与测试已由 `7b001dd9` 等价迁移；旧提交的历史不能直接合并到新分支。
- 旧分支中的设计预览目录属于历史 UI 参考，不是 Windows 运行能力，也不进入 canonical Windows 开发线。
- `18e7bb93` 只是旧分支测试隔离修补，当前 `main` 的测试夹具已包含对应隔离边界，没有单独重放。

## 验证证据

- `node apps/desktop/scripts/build-identity-test.js`：PASS。
- `node --check apps/desktop/electron-builder.windows.cjs`、`node --check apps/desktop/scripts/generate-build-identity.js`、`python3 -m py_compile scripts/windows_backend_probe.py`：通过。
- API Windows 相关回归：34 passed，4 skipped（Windows 专属凭据用例在 macOS 跳过）。
- Windows 真机、安装包启动/退出恢复、真实模型和真实成片尚未在 Windows 主机验收；这些仍是产品验收缺口。

## 退役条件

Draft PR #146 已关闭，`windows/local-model-validation` 已删除；本记录和上述提交作为迁移与回滚依据保留，后续只从 `codex/windows-integration-1.5.2` 继续开发。
