# 曜核 Flutter 手机伴侣

Android 与 iOS 共用 `lib/` 中的 Dart 界面、连接及状态逻辑。平台目录只放系统配置；Flutter Web 是本地渲染和交互检查入口，不是原生手机验收。

产品范围见[产品需求 §1.1](../../docs/product-requirements.md#11-版本交付边界)，权限见[认证边界](../../docs/auth-workspace-boundary.md)。0.11/0.12联动已并入本轮[联合交付清单](../../.project-control/0.11-development-plan.md#114-p7桌面与手机联动的联合交付)，本应用不另行定义版本承诺，也不以本地功能存在代替跨端验收。声音采样的电脑/手机同文、跳过及主机接收语义见[首次使用](../../docs/product-requirements.md#41-首次使用与按需设置)；这是目标行为，下面的代码入口和实际运行才证明接通情况。桌面 [DESIGN.md](../../DESIGN.md) 的三栏和控件尺寸不直接套到手机。

## 开发

SDK约束以 `pubspec.yaml` 为准，依赖精确解见 `pubspec.lock`；用 `flutter --version` 核对本机实际环境，不沿用旧工作树的工具链快照。Android和iOS目标要求分别以原生项目配置为准。

```sh
cd apps/mobile
flutter pub get
flutter run -d <Android设备ID>
flutter run -d <iOS设备ID>
```

按本机配置设置 Android SDK、JDK、生成物及临时目录，编译前确认所需平台和构建工具已安装；不把历史机器路径或目录存在当作就绪证明。使用外接存储的环境先核实当前目录配置。SDK 下载源为 [Android 官方开发工具](https://developer.android.com/studio)。

```sh
flutter build apk --debug
```

本地浏览器预览：

```sh
flutter build web
python3 -m http.server 4341 --bind 127.0.0.1 --directory build/web
```

## 代码与行为

- `lib/ui/companion_app.dart`：应用组装、外层导航、生命周期和弹窗调用；首页、进度与个人页分别由`companion_materials_page.dart`、`companion_progress_page.dart`和`companion_account_page.dart`呈现。页面只读取对应事实并调用具体动作；进度子页选择由进度页保留。
- `lib/data/local_media_draft.dart`和`local_media_draft_controller.dart`：本地素材、说明和声音引用的恢复与唯一保存入口，以及系统选择和复制。使用原安全存储键及格式；串行保存完整快照，移除清单不删除文件。未接入接收服务时仍只显示本机待发送清单，不生成传输或任务样例。
- `lib/data/voice_sample_controller.dart`和`voice_sample_io.dart`：录音/试听生命周期及原生插件边界。关闭弹窗时处理仍在等待的录音请求，声音加载的迟到结果不能重新开始播放；新样本由本地草稿入口保存。
- `lib/ui/connection_sheet.dart`：默认真实查找 `_yaohe._tcp` 服务与扫码入口，发现结果仅为候选。已有开发连接可重新核验；原手填输入只在 debug 的“开发调试连接”内展开。读取二维码不会猜测地址、发凭证或保存假绑定；正式配对协议待电脑版确认后接入。
- `lib/ui/voice_sample_sheet.dart`：固定操作区与可滚动内容，录音/试听控制同宽、状态切换不移动按钮；播放器状态决定“试听／停止试听”。未加载的旧样本不显示虚假零秒时长。

个人昵称与头像按正式账号统一管理；当前手机尚未接入正式登录和资料服务，账号区域只说明现状，不提供假的资料编辑或注册成功。已有开发成员连接不能当作正式登录，本地素材准备和声音录制仍可独立使用。
- `lib/data/host_api.dart`：复用主机现有 `/api/members/join`、`/api/members/whoami`、`/api/creation-tasks`。使用服务端签发的成员凭证，重新核验成员后读取任务；拒绝地址跳转，避免把连接凭证送往另一台主机。
- `lib/data/mobile_store.dart`：安全存储连接、按主机与成员隔离的最后确认进度。断线保留原记录与更新时间，同时说明读取失败；不推算完成、不伪造进度。
- `lib/ui/invitation_scanner.dart`：原生相机扫描、系统选择单张邀请图片并在手机内识码。拒绝相机权限后由用户明确重试，不因授权弹窗关闭自动重复申请；识别内容交给调用方核验，不直接打开地址或发送凭证。
- `lib/data/host_discovery.dart`：原生 DNS-SD 查找和停止，查询窗口15秒；结果仅是连接候选，不修改已保存凭证的目标地址。当前客户端查询类型需与电脑版发布的类型对齐；正式载荷与身份信任仍由共同接入协议确定。

已保存的连接在 App 回到前台时重新核验；前台每30秒读取真实进度，后台停止轮询。临时网络/服务失败可重试，授权失败和身份变化停止自动重试。用户退出连接后，旧请求不得继续读任务或恢复已退出的会话。

手机端选择的视频复制到本 App 的私有文档目录，清单/说明和录音引用存入安全存储。不会删除原视频。录音只在用户点击后申请麦克风；切到其他应用时请求停止，生成真实文件才提供试听。移除清单不删除原视频或已复制文件，缓存清理不属于本轮交付。

浏览器的文件选择和录音是浏览器插件能力；刷新后不宣称仍持有原文件或录音 blob，保留文本说明。浏览器验证不能证明 Android/iOS 私有目录、授权、录音和后台行为。

## 主机接入依赖

连接需要真实可达且证书可信的 HTTPS 主机地址。仅在 debug 模式允许 localhost/127.0.0.1 的 HTTP 调试，不允许对 LAN HTTP 发送邀请或成员凭证。主机现有 loopback 服务不会因为本应用创建而自动开放到局域网，手机也不会绕过证书或账号权限。现有成员 API 是本地模式的接入代码；Supabase 账号登录、设备邀请和空间选择还需与当前主机契约对齐。

素材发送沿 `host_api.dart` 与主机实际接收接口核查；本地路径导入不能当作手机文件上传。当前本地草稿只表示手机已保存，未接入接收时不显示发送成功。正式登录/配对、文件发送与主机按空间接收、断点重传、声音登记及系统通知分别验收，不能由发现设备或扫码成功推定整条连接链完成。

电脑版接入要求保存在 [desktop-integration-request.md](desktop-integration-request.md)。扫码与发现已进入 App 的连接界面；正式二维码、认证和发现协议对齐之前，不把识码或发现服务当作绑定成功。用户真实素材和录音保留；包名及持久化键保持一致，Android 更新使用覆盖安装，不能卸载或清除数据冒充升级。

## 原生连接能力的隔离检查

`test/native_connection_smoke.dart` 是专用诊断入口，独立于产品会话和素材草稿；不会访问真实账号或创建任务。它注册明确命名的“曜核原生发现测试”服务以验证平台发现链路，端口不提供产品 API；扫码只核对非邀请测试文本 `yaohe-native-scanner-fixture-v1`，不建立成员绑定。

```sh
flutter run --debug -d <隔离Android设备ID> -t test/native_connection_smoke.dart
# 检查结束后恢复正常入口
flutter run --debug -d <隔离Android设备ID> -t lib/main.dart
```

相机只在进入扫描界面时请求；邀请图片由系统选择器限定为用户选择的一张，不申请整个相册读取权限。原生 Android 与 iOS 分别验收；模拟器识码/发现不证明真实电脑服务、正式账号或同网配对。

## 最小检查

```sh
flutter analyze
flutter test
```

测试使用隔离 HTTP 和安全存储夹具，覆盖凭证传输、跨主机跳转拒绝、成员变化拒绝、离线缓存、退出清理和未配对页面。它们不是当前桌面服务、真实局域网或原生设备验收。
