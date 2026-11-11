# 曜核 Flutter 手机伴侣

Android 与 iOS 共用 `lib/` 中的 Dart 界面、连接及状态逻辑。平台目录只放系统配置；Flutter Web 是本地渲染和交互检查入口，不是原生手机验收。

产品范围见[产品需求 §1.1](../../docs/product-requirements.md#11-版本交付边界)，权限见[认证边界](../../docs/auth-workspace-boundary.md)。跨端验收见[联合交付清单](../../.project-control/0.11-development-plan.md#114-p7桌面与手机联动的联合交付)，本应用不另行定义版本承诺，也不以本地功能存在代替跨端验收。声音采样的电脑/手机同文、跳过及主机接收语义见[首次使用](../../docs/product-requirements.md#41-首次使用与按需设置)；这是目标行为，下面的代码入口和实际运行才证明接通情况。手机布局不直接套用桌面 [DESIGN.md](../../DESIGN.md) 的栏位与控件尺寸。

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
- `lib/data/voice_sample_controller.dart`和`voice_sample_io.dart`：录音/试听生命周期及原生插件边界。关闭弹窗时处理仍在等待的录音请求，声音加载的迟到结果不能重新开始播放；正式采样由`voice_sampling_controller.dart`沿采样仓储保存原任务绑定和录音引用。
- `lib/ui/connection_sheet.dart`：默认查找 `_yaohe._tcp` 服务并提供扫码入口，发现结果仅为候选。`connection_invitation.dart`解析正式配对或成员邀请；用户通过正式账号登录，主机核验设备、成员和工作区后才保存连接。协议由[架构§5.1.1](../../docs/system-architecture.md#511-手机联动的共同接收与回读)拥有。
- `lib/ui/voice_sample_sheet.dart`：固定操作区与可滚动内容，录音/试听控制同宽、状态切换不移动按钮；播放器状态决定“试听／停止试听”。未加载的旧样本不显示虚假零秒时长。

个人昵称与头像按正式账号统一管理。手机已通过`auth_session.dart`接入Supabase账号登录与刷新，账号页展示当前连接的姓名及设备角色；手机暂不提供注册、昵称修改或头像编辑。素材可先保存在本机，声音采样须绑定电脑创建的采样任务与朗读文本。

- `lib/data/host_api.dart`：按邀请用途调用正式配对或成员邀请接受入口，通过`/api/companion/session`核验连接身份，再读取`/api/creation-tasks`等授权结果。请求携带正式账号凭证与工作区，拒绝跨主机跳转；接口与身份语义见架构§5.1.1。
- `lib/data/mobile_store.dart`：安全存储连接、按主机与成员隔离的最后确认进度。断线保留原记录与更新时间，同时说明读取失败；不推算完成、不伪造进度。
- `lib/ui/invitation_scanner.dart`：原生相机扫描、系统选择单张邀请图片并在手机内识码。拒绝相机权限后由用户明确重试，不因授权弹窗关闭自动重复申请；识别内容交给调用方核验，不直接打开地址或发送凭证。
- `lib/data/host_discovery.dart`：原生 DNS-SD 查找和停止，查询窗口15秒；结果仅是连接候选，不修改已保存凭证的目标地址。服务类型与桌面网关保持一致；连接载荷与身份信任遵循架构§5.1.1。

已保存的连接在 App 回到前台时重新核验；前台每30秒读取真实进度，后台停止轮询。临时网络/服务失败可重试，授权失败和身份变化停止自动重试。用户退出连接后，旧请求不得继续读任务或恢复已退出的会话。

手机端选择的视频复制到本 App 的私有文档目录，清单/说明和录音引用存入安全存储。不会删除原视频。录音只在用户点击后申请麦克风；切到其他应用时请求停止，生成真实文件才提供试听。移除清单不删除原视频或已复制文件，缓存清理不由移除清单动作触发。

浏览器的文件选择和录音是浏览器插件能力；刷新后不宣称仍持有原文件或录音 blob，保留文本说明。浏览器验证不能证明 Android/iOS 私有目录、授权、录音和后台行为。

## 主机接入依赖

连接需要真实可达且证书可信的 HTTPS 主机地址。桌面在正式身份可用且用户启用手机连接后开放受限网关；手机通过正式账号登录，核验邀请与会话后接入。配对同一账号与邀请新成员是两种不同用途，不能混用；信任、凭证和授权规则见[架构§5.1.1](../../docs/system-architecture.md#511-手机联动的共同接收与回读)及[认证边界](../../docs/auth-workspace-boundary.md#9-主账号邀请与共享授权)。

`mobile_transfer.dart`、`transfer_controller.dart`负责文件声明、实际字节发送、原请求回查与恢复；`voice_sampling_controller.dart`绑定正式采样任务及文本版本，按主机回执区分本机保存、发送中和完整接收。`host_media_api.dart`读取授权媒体。上述代码入口不等于真实跨端验收通过：正式登录/配对、传输中断恢复、声音登记与实际制作、结果回看、系统通知及两端重启分别以真实设备证据核实。

两端联调沿 [desktop-integration-request.md](desktop-integration-request.md)进行；发现或识码成功不能代替绑定成功，主机完整接收不能代替作品制作成功。用户真实素材和录音保留；包名及持久化键保持一致，Android 更新使用覆盖安装，不能卸载或清除数据冒充升级。

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
