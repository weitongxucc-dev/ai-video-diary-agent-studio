# Windows 集成分支 1.5.2

候选基线：`main` / `35f5152576b4cbb91dfc6c3d009ca2ba9fea9f28`

当前迁移提交：`7b001dd9f901e15f0625d7ec95ce53b0fdc248c3`

## 本轮已迁移

- Windows 文件锁、临时目录和子进程环境：`platform_files.py`、`subprocess_environment.py` 及其调用方。
- Agent 进程控制：Windows Job Object、停止与子孙进程回收、受控 turn 环境。
- Pi 运行时：Windows npm shim 的 SDK 路径解析、15 秒启动检查边界、隔离环境变量。
- 工作区凭据：Windows 凭据存储分支、缓存失效和回滚测试。
- 相关跨平台测试和锁文件变更。

## 本轮暂缓

Windows Electron 安装器、NSIS、standalone API 物化和签名/发布入口暂缓到下一批。这些改动依赖 Windows 构建机和真实安装验收，不能只凭 macOS 静态检查合入 `main`。

## 当前证据

- macOS 定向 Python 测试：17 passed，4 skipped。
- Python 目标模块 `compileall`：通过。
- `git diff --check`：通过。
- Windows 真机、安装器、Pi 实际加载和完整媒体链路：`BLOCKED`，当前环境没有 Windows 目标机；PR #146 的旧安装包证据不属于本候选。

## 下一轮 Windows 验收

1. 在 Windows x64 上从本分支构建并安装 preview 包。
2. 验证 API/Web ready、工作区凭据持久化、Pi SDK 加载和停止回收。
3. 运行 Windows 定向测试，再执行手机端/CLI 不受影响的回归检查。
4. 只有安装、真实对话和成片链路均通过，才把暂缓的桌面打包改动迁入并创建合并请求。
