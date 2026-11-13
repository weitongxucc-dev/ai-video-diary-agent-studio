# MOB-1 手机端真机验收清单

本清单只记录真实 Android/iOS 设备、真实曜核主机和真实局域网联调。模拟 HTTP、Flutter widget test、Web 预览和隔离原生 smoke 只能证明对应的单端或隔离能力，不能填写为真机通过。

## 当前可复用证据

- 工作区：`/Volumes/Dujiangyan-VM/AI视频日记/repositories/main`
- 分支：`main`
- 验收基线提交：以执行前 `git rev-parse HEAD` 为准；当前代码基线为 `2296974f`。
- Flutter：`3.41.6`，Dart `3.11.4`。
- `flutter analyze`：通过，`No issues found`。
- `flutter test`：通过，输出 `+79`，共 79 个测试；包含执行状态四态、任务/工作区串线、请求/助手回合关联和 Trace 回读解析测试。
- `flutter devices`：当前只发现 macOS 和 Chrome，没有 Android/iOS 实体设备，也没有无线设备。
- `flutter build apk --debug`：未完成，当前环境没有 Android SDK；这不是 APK 构建通过，也不是设备验收失败。

## 开始前条件

1. 准备一台实际 Android 手机和一台实际 iPhone/iPad；分别记录型号、系统版本、设备 ID 和 App 包版本。Android 包名是 `cn.yaohe.yaohe_mobile`，更新时覆盖安装，不能卸载或清除数据来代替升级验证。
2. 准备一台运行当前桌面/主机版本的电脑。主机必须启用受限 HTTPS 网关，使用真实可信 CA，发布 `_yaohe._tcp`，并能在同一局域网被手机访问。
3. 准备一个有效的正式账号和目标 Workspace。至少准备一条属于该 Workspace 的制作任务，其中已有可播放成片、`current_timeline_version_id` 和 `timeline_sequence`；另准备一个无权 Workspace 或成员，用于拒绝用例。
4. 准备一份可保留的本地视频和一条电脑创建的声音采样任务。记录原文件路径、任务 ID、Workspace ID 和时间线版本；不要把访问令牌、密码或私钥写入截图、日志或报告。
5. 在设备上打开必要的系统能力：网络、相机、麦克风；iOS 还需允许本地网络访问。首次拒绝权限后，按产品入口明确重试，不能用重装 App 代替恢复验证。

## Flutter 入口

在 `apps/mobile` 下执行：

```sh
flutter pub get
flutter devices
flutter analyze
flutter test
flutter run -d <真实设备ID> -t lib/main.dart
```

原生发现/扫码隔离诊断只用于确认插件链路，不能当作正式绑定：

```sh
flutter run --debug -d <真实Android设备ID> -t test/native_connection_smoke.dart
flutter run --debug -d <真实Android设备ID> -t lib/main.dart
```

Android 若需构建包，先确认 Android SDK、JDK 17、平台和构建工具已安装，再执行 `flutter build apk --debug`。iOS 需在 Xcode 中使用真实签名设备运行；模拟器不证明相机、麦克风、Bonjour 或局域网证书行为。

## 最小设备执行脚本

在每台真实设备上按同一顺序执行，逐项保存录屏、主机响应摘要和文件摘要；`<DEVICE_ID>`、账号和邀请只在本机输入，不写入日志：

```sh
cd /Volumes/Dujiangyan-VM/AI视频日记/repositories/main/apps/mobile
flutter pub get
flutter devices
flutter analyze
flutter test
flutter run -d <DEVICE_ID> -t lib/main.dart
```

应用内执行以下步骤：

1. 扫描当前主机邀请，确认用途、设备、Workspace、成员和 CA 摘要后登录；记录 `/api/companion/session` 的设备、成员和 Workspace 回执。
2. 打开任务列表和一条真实任务，刷新详情；记录 `task_id`、Workspace、任务状态和时间线版本。
3. 打开“作品进度与成片”，分别准备运行中、已完成、失败或无作业的任务，记录手机显示的四态；保存作业 ID，以及主机回执实际返回的 `request_id`、`assistant_turn_id`。缺失关联必须记录为未知。
4. 对 `output_ready=true` 的任务下载成片，记录请求中的 `expected_version_id`、响应的时间线版本/序号、MIME、文件大小和 SHA-256，然后在手机上播放、暂停、退出后台再恢复。
5. 在下载前推进一次时间线，确认手机拒绝旧版本并要求刷新；断网后重新打开作品，确认手机回查原任务而不显示本地完成态。

Android 和 iOS 分别填写下方结果模板；同一条证据不能同时代替另一平台的真机验收。

## Android / iOS 共同验收

每一项都记录设备、时间、主机地址摘要、Workspace/成员摘要、结果截图或录屏、主机日志回执和失败后的恢复动作。

| 状态 | 验收项 | 通过标准 | 真实证据 |
| --- | --- | --- | --- |
| ☐ | 发现与扫码 | 发现只显示当前局域网候选；扫码或导入邀请后能解析正式用途、设备、Workspace、过期时间和 CA；不会直接打开地址或发送凭证 | 设备录屏 + 主机发现日志 |
| ☐ | 正式登录与配对 | 使用正式账号登录；同账号配对和新成员邀请分别走对应入口；主机回读的设备、成员、Workspace 与邀请一致后才保存绑定 | 登录请求结果、`/api/companion/session` 回执、手机连接页 |
| ☐ | 错误授权 | 过期/撤销邀请、错设备、停用成员、错误 Workspace 或不可信证书均拒绝；不产生半绑定或把凭证发往跳转地址 | 手机错误提示 + 主机 4xx/证书日志 |
| ☐ | 任务回执 | 刷新任务时带当前成员凭证和 `X-Workspace-Id`；列表和详情只出现当前 Workspace 的真实任务，任务状态与主机页面一致 | 手机任务页 + 主机响应摘要 |
| ☐ | 执行状态与结果关联 | 任务详情读取 `/api/creation-tasks/{task_id}/execution`；入口不可用时回查 `/job-runs`；手机区分运行中、已完成、失败、未知，并展示任务/作业及主机返回的 `request_id`、`assistant_turn_id`；缺失或串线回执显示未知并要求查询原请求 | 手机状态卡 + 主机执行回执摘要 + 原请求/回合记录 |
| ☐ | 会话恢复 | 退出 App 再启动能重新核验；过期令牌只刷新同一用户；退出连接后旧请求不能恢复任务或覆盖新绑定 | 重启前后录屏 + token 刷新/退出日志 |
| ☐ | 网络切换 | Wi-Fi 暂断、切换同网 AP、主机短暂不可达后，失败状态明确；恢复后查询真实状态，不重复提交未知请求 | 网络切换时间线 + 原请求/查询回执 |
| ☐ | 成片下载与播放 | 只有 `output_ready` 且版本匹配时可下载；请求带 `expected_version_id`；响应版本头、序号和 `video/*` 均匹配后才落盘播放；中断不留下可播放半文件 | 手机落盘文件、文件大小/SHA-256、播放录屏、主机响应头 |
| ☐ | 时间线冲突 | 主机在下载前改变时间线时，手机显示版本已变化并要求刷新，不播放旧版本 | 先改任务再下载的 409 回执 + 手机提示 |
| ☐ | 进后台与恢复 | App 进入后台停止轮询并暂停播放；回前台重新核验；系统权限提示或网络失败不会伪造完成 | 生命周期录屏 + 请求时间线 |

## Android 专项

| 状态 | 验收项 | 通过标准 | 真实证据 |
| --- | --- | --- | --- |
| ☐ | 相机权限 | 首次扫码只在进入扫码入口时申请；拒绝后页面可明确重试，不自动循环弹窗 | 系统权限弹窗录屏 |
| ☐ | 麦克风权限 | 只有点击录音才申请；拒绝后不生成假样本；切后台会停止录音并保留已生成文件 | 权限与文件目录证据 |
| ☐ | 私有文件与覆盖安装 | 选择的视频、录音和下载成片位于 App 私有目录；覆盖安装后连接、草稿和原请求记录按预期恢复 | 安装前后状态、文件摘要 |
| ☐ | 原生播放 | 下载完成后 `video_player` 能初始化、播放、暂停；文件损坏或不完整时不进入播放状态 | 播放录屏 + 文件摘要 |

## iOS 专项

| 状态 | 验收项 | 通过标准 | 真实证据 |
| --- | --- | --- | --- |
| ☐ | 相机/麦克风/本地网络 | `NSCameraUsageDescription`、`NSMicrophoneUsageDescription` 和本地网络权限按实际动作出现；拒绝后可恢复或显示明确原因 | 系统权限录屏 |
| ☐ | Bonjour 发现 | 同网主机发布 `_yaohe._tcp` 时能发现；关闭主机或离网后候选停止，不把旧地址当作当前绑定 | 发现列表 + 主机广播/关闭时间线 |
| ☐ | HTTPS 证书信任 | 主机 CA、主机名和有效期均通过；证书变更、过期或不匹配时拒绝连接 | 主机证书摘要 + 手机错误提示 |
| ☐ | 私有目录与播放 | 成片下载到 App 支持目录后可由播放器打开；切后台/锁屏后状态符合预期，重启后不把临时文件当成成片 | 文件摘要 + 播放/重启录屏 |

## 结果记录格式

对每个平台单独填写：

```text
平台/型号/系统：
设备 ID：
App 构建号：
主机版本与地址摘要：
Workspace/成员摘要：
执行时间（Asia/Singapore）：
通过项：
失败项：
原始证据路径：
未验收或阻塞：
```

只有同时具备真实设备运行、真实主机 HTTPS、真实成员/Workspace 回执和真实成片文件播放证据，才能将 MOB-1 标为真机通过。当前环境仍只能标记为“代码与 Flutter 测试通过，真机待执行”。
