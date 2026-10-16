# macOS 桌面应用

Electron 负责启动本地 Next.js 和 FastAPI API，并管理窗口、端口、日志和子进程生命周期。

0.11 的对话助手通过本机 Pi 1.3.0 的 coding-agent SDK 运行（需要 Node.js 22.19+）。开发和当前测试包会从 PATH 或当前用户的 nvm 安装中寻找 `pi`；也可在私密运行配置中设置 `AI_VIDEO_DIARY_PI_EXECUTABLE`。业务工具、`agent_runtime` 会话桥、创作 Skills 和提示词模板随桌面包提供并进入构建标识，Pi 和 Node.js 尚未随包分发；缺失时对话入口会明确报错，原有精确编辑入口仍可使用。对话会话写在所配置模型盘的同级 `agent/` 目录。

业务工具入口为`apps/api/agent_tools/diary_tools.ts`，其`diary/`与`materials/`子目录须保持原结构一同打包；工具体检沿实际相对导入链核验文件。Python宿主入口为`apps/api/app/assistant_pi.py`，具体进程、停止、上下文及回执投影由`app/assistant/`拥有，HTTP工具仍消费原业务API。

安装 Node.js 22.19 或更高版本后，按运行桥要求安装 `@earendil-works/pi-coding-agent@1.3.0`，不要无版本安装或把本机最新版本当作兼容性证明。用 `node --version` 和 `pi --version` 核对实际程序，再重新打开应用。运行时升级和复用原则见[架构第3.15节](../../docs/system-architecture.md#315-pi-复用与视频创作-agent)；体检分别显示 Pi、Node 和业务工具文件状态，仅阻断所依赖能力。文件就绪和 Key 已保存都不等于模型请求或业务工具成功，仍需实际验证。

## 开发环境

需要 Node.js 22、pnpm 9.15、Python 3.11、uv 和 ffmpeg。先在仓库根目录安装依赖：

```bash
corepack pnpm install --frozen-lockfile
cd apps/api && uv sync --locked --group dev
```

开发时分别启动 API、Web 和 Electron：

```bash
corepack pnpm dev:api
corepack pnpm dev:web
corepack pnpm --dir apps/desktop dev
```

## 本地构建

```bash
corepack pnpm --dir apps/desktop build:mac
```

该命令会构建 Next.js standalone、PyInstaller API 和 Electron 包。产物位于 `apps/desktop/dist/`，该目录不进入 Git。当前打包客户端只启动 API 和 Web；遗留 Worker 不进入默认构建或包资源。

本机 Python/OpenSSL 和 CPU 架构差异容易产生只能在构建机运行的二进制。发给测试者时，应优先使用 GitHub Actions 的原生 arm64/x64 内测构建，而不是随手转发本机构建。

## 运行结构

`main.js`组装生命周期、窗口与IPC；`runtime/service-supervisor.js`拥有服务监督，`lifecycle-log.js`与`output-logging.js`拥有日志，`configuration.js`与`build-identity.js`读取运行配置和候选身份。素材导入、原生文件和媒体保存分别由`material-import.js`、`native-files.js`与`native-media.js`实现，`ipc-handlers.js`只注册对应边界；窗口、菜单和更新由各自模块组织。`runtime/`源文件同时进入包资源和候选输入摘要。

```text
Electron main
├── Next.js standalone（Electron utility process）
├── FastAPI api-server
└── BrowserWindow
```

Web通过Electron utilityProcess运行，使用后台Helper身份和管道输出，不使用主GUI binary的ELECTRON_RUN_AS_NODE启动。生命周期按utility pid/exit/kill语义回收它，API仍使用既有进程组管理；后台服务不生成独立Dock图标，主应用正常显示。主进程必须等待 API 和 Web 健康后再加载工作台；退出时关闭其拥有的子进程。打包后健康检查失败应显示可恢复错误，不能留下后台孤儿进程。

生命周期 JSONL 使用同一路径的单一异步追加写流。首条日志真实写入完成后才请求单实例锁并启动服务；日志写入或关闭失败、超过60秒未响应时显示明确错误并结束当前实例，不自动修改目录权限、身份或用户数据。正常退出等待日志关闭，迟到的写入回调不能重新放行已失败的启动。

本机独立验收实例可在明确的 local auth 安装包使用绝对 `--user-data-dir`，同时指定 `--local-web-port=<1..65535>`。同一独立数据目录每次沿用同一显式端口，Web 子进程和窗口使用该固定 origin；端口占用会报错，不自动改端口或连接已有服务。默认端口仍为3000；development、Supabase、缺少明确local构建配置或未指定独立绝对数据目录时拒绝覆盖，Supabase登录回调origin保持不变。数据目录隔离不提供复制原用户数据库、密钥或账号的授权。

打包前 Electron Builder 会生成并随包写入 `build-identity.generated.json`。它对实际进入包的桌面运行文件、API 二进制、模型 worker、已物化的 Web tree 以及打包配置按排序路径和内容计算候选输入摘要；不依赖 Git 状态，因此实际被打包的未跟踪文件同样会改变身份。主进程只接受该元数据，生产模式缺失或无效时会明确拒绝启动。生命周期日志的每条记录、运行时 IPC 和“关于”窗口都显示同一 `candidateInputDigest`；它是候选输入身份，不是 `.app` 文件清单或 DMG SHA。日志还记录 API/Web 的退出 `code` 与 `signal`。日志管道被父进程关闭时，主进程仅忽略对应 stdout/stderr 的 `EPIPE`；其他日志流错误仍会使客户端明确失败并停止服务。日志不记录 `.env`、密钥或模型/用户配置路径。

桌面主进程首次启动会在 userData 下生成并持久化设备身份 `device-identity.generated.json`（`device_id` 为随机 UUID、`proof` 为 64 位小写 hex），并在启动本地 API 子进程时以 `DEVICE_ID`/`DEVICE_PROOF` 环境变量注入（与 Supabase 凭据同源，渲染进程不可观测）。文件缺失视为首次启动而生成；文件存在但畸形则视为运维故障、拒绝启动（对应 API 侧 503 unavailable），不伪造、不降级、不静默重生成。

自动化启动链遵循[系统架构§3.12](../../docs/system-architecture.md#312-整机资源调度)：Electron随本地API启动与关闭到期扫描器，媒体执行接入该API已有设备协调者。

macOS包内主程序、Helper可执行名及`CFBundleName`保持ASCII（`Yaohe Studio`），避免系统截断进程名时切断UTF-8字符；`CFBundleDisplayName`和安装包文件名保留中文。Helper寻址依赖同一内部名，不能仅改主程序或显示名。此构建字段不改变顶层包名`ai-video-diary-studio`；隔离验收继续显式指定`--user-data-dir`。

## 图标和权限

- `assets/icon.icns`：macOS 构建输入。
- `assets/icon.png`：通用 512px 源图。
- `assets/generate_icon.py`：从同一图形规则重新生成上述两份资产。
- `assets/entitlements.plist`：Hardened Runtime/JIT 权限。

中间尺寸 PNG 不进入仓库；需要时由生成脚本在内存中构造。

Web 中的已预缩放品牌图标直接读取包内图片，不经过 Next 图片优化缓存；运行时不能写入已签名的应用资源目录。打包后需在启动及实际展示图片后再次校验签名。

## 发布

内部包和正式签名 Release 的区别、所需密钥和操作顺序见 [`RELEASE.md`](RELEASE.md)。
