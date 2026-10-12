import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:file_picker/file_picker.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:just_audio/just_audio.dart';
import 'package:path_provider/path_provider.dart';
import 'package:record/record.dart';
import '../data/mobile_store.dart';
import 'pearl_action_button.dart';
import 'companion_navigation_bar.dart';
import 'connection_sheet.dart';
import 'voice_sample_sheet.dart';

const blue = Color(0xff3864f6);
const ink = Color(0xff222632);
const background = Color(0xfff7f8fc);

class DraftVideo {
  const DraftVideo({required this.name, required this.size, this.path});
  final String name;
  final int? size;
  final String? path;
}

class CompanionApp extends StatelessWidget {
  const CompanionApp({super.key, required this.store});
  final MobileStore store;
  @override
  Widget build(BuildContext context) => MaterialApp(
    debugShowCheckedModeBanner: false,
    title: '曜核',
    theme: ThemeData(
      useMaterial3: true,
      colorScheme: ColorScheme.fromSeed(
        seedColor: blue,
        primary: blue,
        surface: Colors.white,
      ),
      scaffoldBackgroundColor: background,
      fontFamily: 'PingFang SC',
      appBarTheme: const AppBarTheme(
        backgroundColor: background,
        foregroundColor: ink,
        centerTitle: false,
        scrolledUnderElevation: 0,
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: background,
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(14),
          borderSide: BorderSide.none,
        ),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          minimumSize: const Size(48, 48),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(16),
          ),
        ),
      ),
    ),
    home: CompanionHome(store: store),
  );
}

class CompanionHome extends StatefulWidget {
  const CompanionHome({super.key, required this.store});
  final MobileStore store;
  @override
  State<CompanionHome> createState() => _CompanionHomeState();
}

class _CompanionHomeState extends State<CompanionHome>
    with WidgetsBindingObserver {
  int tab = 0, progressTab = 0;
  StateSetter? _voiceSheetUpdate;
  bool choosing = false, recording = false, microphoneBusy = false;
  final files = <DraftVideo>[];
  bool draftReady = false;
  final notes = TextEditingController();
  final draftStorage = const FlutterSecureStorage();
  final recorder = AudioRecorder();
  final player = AudioPlayer();
  late final StreamSubscription<PlayerState> playbackUpdates;
  Timer? timer;
  int seconds = 0;
  String? sample;
  String? localError;
  String? voiceError;
  MobileStore get store => widget.store;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    store.setForeground(true);
    playbackUpdates = player.playerStateStream.listen((_) {
      if (mounted) _voiceSheetUpdate?.call(() {});
    });
    unawaited(_restoreDraft());
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    store.setForeground(false);
    timer?.cancel();
    unawaited(playbackUpdates.cancel());
    notes.dispose();
    recorder.dispose();
    player.dispose();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    store.setForeground(state == AppLifecycleState.resumed);
    if (state == AppLifecycleState.inactive && recording && !microphoneBusy) {
      unawaited(_record());
    }
  }

  Future<void> _restoreDraft() async {
    try {
      final value = await draftStorage.read(key: 'yaohe_local_draft');
      if (value == null) return;
      final data = jsonDecode(value) as Map<String, dynamic>;
      final restored = <DraftVideo>[];
      var missing = false;
      if (!kIsWeb) {
        for (final item in (data['files'] as List)) {
          final path = item['path'] as String?;
          if (path != null && await File(path).exists()) {
            restored.add(
              DraftVideo(
                name: item['name'] as String,
                size: item['size'] as int?,
                path: path,
              ),
            );
          } else {
            missing = true;
          }
        }
      }
      if (mounted) {
        setState(() {
          notes.text = data['notes'] as String;
          files.addAll(restored);
          sample = kIsWeb ? null : data['sample'] as String?;
          if (missing) localError = '部分视频已不在手机缓存中，请重新选择。素材说明仍已保留。';
        });
      }
    } on Exception {
      if (mounted) setState(() => localError = '无法恢复手机上的素材清单，请重新选择。');
    } finally {
      if (mounted) setState(() => draftReady = true);
    }
  }

  Future<void> _saveDraft() async {
    try {
      await draftStorage.write(
        key: 'yaohe_local_draft',
        value: jsonEncode({
          'notes': notes.text,
          'sample': kIsWeb ? null : sample,
          'files': kIsWeb
              ? []
              : files
                    .map(
                      (file) => {
                        'name': file.name,
                        'size': file.size,
                        'path': file.path,
                      },
                    )
                    .toList(),
        }),
      );
    } on Exception {
      if (mounted) setState(() => localError = '手机未能保存清单，请检查手机存储。当前选择仍保留在页面中。');
    }
  }

  Future<void> _choose() async {
    if (choosing) return;
    setState(() {
      choosing = true;
      localError = null;
    });
    try {
      final result = await FilePicker.pickFiles(type: FileType.video);
      if (result.isEmpty) return;
      final selected = <DraftVideo>[];
      final folder = kIsWeb ? null : await getApplicationDocumentsDirectory();
      for (final file in result) {
        final size = await file.length();
        String? savedPath;
        if (folder != null) {
          final extension = (file.extension ?? 'video').replaceAll(
            RegExp(r'[^a-zA-Z0-9]'),
            '',
          );
          savedPath =
              '${folder.path}/video-${DateTime.now().microsecondsSinceEpoch}.$extension';
          await file.xFile.saveTo(savedPath);
        }
        selected.add(DraftVideo(name: file.name, size: size, path: savedPath));
      }
      if (mounted) {
        setState(() => files.addAll(selected));
        await _saveDraft();
        if (mounted) unawaited(_review());
      }
    } on Exception {
      if (mounted) setState(() => localError = '没有读取到视频，请重新选择文件或检查相册访问权限。');
    } finally {
      if (mounted) setState(() => choosing = false);
    }
  }

  Future<void> _record() async {
    if (microphoneBusy) return;
    setState(() {
      microphoneBusy = true;
      voiceError = null;
    });
    try {
      if (recording) {
        final path = await recorder.stop();
        timer?.cancel();
        if (path == null) {
          if (mounted) {
            setState(() {
              recording = false;
              voiceError = '没有生成可用录音，请重新录制。';
            });
          }
          return;
        }
        if (mounted) {
          setState(() {
            sample = path;
            recording = false;
          });
        }
        await _saveDraft();
      } else {
        if (!await recorder.hasPermission()) {
          if (mounted) {
            setState(() => voiceError = '麦克风访问未允许。请在手机设置中允许曜核使用麦克风，再开始录音。');
          }
          return;
        }
        final folder = kIsWeb
            ? ''
            : (await getApplicationDocumentsDirectory()).path;
        await player.stop();
        await recorder.start(
          const RecordConfig(
            encoder: AudioEncoder.wav,
            sampleRate: 24000,
            numChannels: 1,
          ),
          path: '$folder/voice-${DateTime.now().millisecondsSinceEpoch}.wav',
        );
        if (mounted) {
          setState(() {
            recording = true;
            seconds = 0;
          });
        }
        timer = Timer.periodic(const Duration(seconds: 1), (_) {
          if (mounted) {
            setState(() => seconds++);
            _voiceSheetUpdate?.call(() {});
          }
        });
      }
    } on Exception {
      if (mounted) {
        setState(() {
          voiceError = recording
              ? '尚未确认录音是否结束，请再次点击结束录音。'
              : '录音未能开始，请检查麦克风和手机存储后重试。';
        });
      }
    } finally {
      if (mounted) {
        setState(() => microphoneBusy = false);
        _voiceSheetUpdate?.call(() {});
      }
    }
  }

  Future<void> _listen() async {
    if (sample == null) return;
    try {
      if (player.playing &&
          player.processingState != ProcessingState.completed) {
        await player.stop();
        return;
      }
      voiceError = null;
      await player.setAudioSource(
        AudioSource.uri(kIsWeb ? Uri.parse(sample!) : Uri.file(sample!)),
      );
      await player.play();
    } on Exception {
      if (mounted) {
        setState(() => voiceError = '这段录音无法播放，请重新录制。');
        _voiceSheetUpdate?.call(() {});
      }
    }
  }

  Future<void> _review() async {
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(28)),
      ),
      builder: (context) => Padding(
        padding: EdgeInsets.fromLTRB(
          22,
          12,
          22,
          22 +
              MediaQuery.viewInsetsOf(context).bottom +
              MediaQuery.paddingOf(context).bottom,
        ),
        child: SingleChildScrollView(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            mainAxisSize: MainAxisSize.min,
            children: [
              Row(
                children: [
                  const Expanded(
                    child: Text(
                      '确认素材与说明',
                      style: TextStyle(
                        fontSize: 20,
                        fontWeight: FontWeight.w500,
                      ),
                    ),
                  ),
                  IconButton(
                    tooltip: '关闭素材说明',
                    onPressed: () => Navigator.pop(context),
                    icon: const Icon(Icons.close, size: 20),
                  ),
                ],
              ),
              const SizedBox(height: 12),
              Container(
                padding: const EdgeInsets.all(15),
                decoration: BoxDecoration(
                  color: const Color(0xfff0f3fa),
                  borderRadius: BorderRadius.circular(17),
                ),
                child: Row(
                  children: [
                    Image.asset(
                      'assets/images/mini-host.png',
                      width: 34,
                      height: 46,
                      excludeFromSemantics: true,
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Text(
                        store.session == null
                            ? '素材保存在手机，连接后再交给曜核'
                            : '已保存连接，素材接收服务尚未接通',
                        style: const TextStyle(
                          fontSize: 13,
                          color: Color(0xff697183),
                          height: 1.6,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 16),
              ...files.map(
                (file) => Padding(
                  padding: const EdgeInsets.symmetric(vertical: 9),
                  child: Row(
                    children: [
                      const Icon(
                        Icons.movie_outlined,
                        size: 19,
                        color: Color(0xff6c86bd),
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: Text(
                          file.name,
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(fontSize: 13),
                        ),
                      ),
                      const SizedBox(width: 10),
                      Text(
                        _size(file.size),
                        style: const TextStyle(
                          fontSize: 12,
                          color: Color(0xff697183),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 18),
              const Text('素材说明', style: TextStyle(fontSize: 14)),
              const SizedBox(height: 10),
              TextField(
                controller: notes,
                maxLines: 3,
                decoration: const InputDecoration(hintText: '想保留哪些重点？'),
              ),
              const SizedBox(height: 16),
              const Text(
                '电脑版的手机素材接收服务仍在开发。视频与说明会保留在手机，尚未发送，也不会自动开始制作。',
                style: TextStyle(
                  fontSize: 13,
                  color: Color(0xff697183),
                  height: 1.7,
                ),
              ),
              const SizedBox(height: 20),
              FilledButton(
                onPressed: () => Navigator.pop(context),
                child: const Text('保存素材与说明'),
              ),
            ],
          ),
        ),
      ),
    );
    await _saveDraft();
  }

  Future<void> _connect() => showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    useSafeArea: true,
    builder: (context) => ConnectionSheet(
      store: store,
      debugBuilder: (context) => _ConnectSheet(store: store),
    ),
  );

  String _size(int? bytes) => bytes == null
      ? '大小暂时无法读取'
      : bytes >= 1024 * 1024
      ? '${(bytes / (1024 * 1024)).toStringAsFixed(1)} MB'
      : '${(bytes / 1024).toStringAsFixed(1)} KB';
  Widget _panel(List<Widget> children) => Container(
    padding: const EdgeInsets.all(20),
    decoration: BoxDecoration(
      color: Colors.white,
      borderRadius: BorderRadius.circular(24),
    ),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: children,
    ),
  );
  Widget _title(String text) => Padding(
    padding: const EdgeInsets.only(bottom: 12),
    child: Text(
      text,
      style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w500),
    ),
  );

  Widget _deviceCard() => Material(
    color: Colors.white,
    borderRadius: BorderRadius.circular(24),
    clipBehavior: Clip.antiAlias,
    child: InkWell(
      onTap: _connect,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 16),
        child: Row(
          children: [
            Image.asset(
              'assets/images/mini-host.png',
              width: 46,
              height: 60,
              cacheWidth: 184,
              filterQuality: FilterQuality.high,
              excludeFromSemantics: true,
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    store.session == null ? '连接你的曜核' : '已保存的曜核连接',
                    style: const TextStyle(
                      fontSize: 16,
                      fontWeight: FontWeight.w500,
                    ),
                  ),
                  const SizedBox(height: 6),
                  Text(
                    store.connected
                        ? '已确认连接 · ${store.session!.name}'
                        : store.session == null
                        ? '在电脑上打开曜核，连接同一网络'
                        : '离线，连接记录已保留',
                    style: const TextStyle(
                      fontSize: 12,
                      color: Color(0xff697183),
                      height: 1.6,
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(width: 6),
            const Icon(Icons.chevron_right, size: 22, color: Color(0xff8f97a6)),
          ],
        ),
      ),
    ),
  );

  Widget _filesPanel({required bool removable}) => Container(
    padding: const EdgeInsets.symmetric(horizontal: 16),
    decoration: BoxDecoration(
      color: Colors.white,
      borderRadius: BorderRadius.circular(24),
    ),
    child: Column(
      children: [
        for (var index = 0; index < files.length; index++) ...[
          if (index > 0) const Divider(height: 1, color: Color(0xfff0f2f7)),
          ListTile(
            contentPadding: const EdgeInsets.symmetric(vertical: 5),
            onTap: _review,
            leading: Container(
              width: 38,
              height: 42,
              decoration: BoxDecoration(
                color: blue.withValues(alpha: .055),
                borderRadius: BorderRadius.circular(12),
              ),
              child: const Icon(Icons.movie_outlined, color: blue, size: 21),
            ),
            title: Text(
              files[index].name,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(fontSize: 14, height: 1.5),
            ),
            subtitle: Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text(
                '${_size(files[index].size)} · 待发送',
                style: const TextStyle(fontSize: 12, color: Color(0xff697183)),
              ),
            ),
            trailing: removable
                ? IconButton(
                    tooltip: '从清单移除',
                    onPressed: () async {
                      setState(() => files.removeAt(index));
                      await _saveDraft();
                    },
                    icon: const Icon(
                      Icons.close,
                      size: 18,
                      color: Color(0xff788090),
                    ),
                  )
                : null,
          ),
        ],
      ],
    ),
  );

  Widget _home() => ListView(
    padding: const EdgeInsets.fromLTRB(22, 5, 22, 14),
    children: [
      _deviceCard(),
      const SizedBox(height: 10),
      Center(
        child: PearlActionButton(
          label: choosing ? '正在选择…' : '选择视频',
          onPressed: choosing ? null : _choose,
          animate: tab == 0,
        ),
      ),
      const SizedBox(height: 2),
      const Center(
        child: Text(
          '把拍好的素材整理好，交给曜核',
          style: TextStyle(fontSize: 13, color: Color(0xff697183)),
        ),
      ),
      const SizedBox(height: 27),
      Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          const Text(
            '待发送素材',
            style: TextStyle(fontSize: 17, fontWeight: FontWeight.w500),
          ),
          Text(
            '${files.length} 段',
            style: const TextStyle(fontSize: 12, color: Color(0xff697183)),
          ),
        ],
      ),
      const SizedBox(height: 14),
      if (files.isEmpty)
        _panel([
          const Icon(Icons.video_library_outlined, color: blue, size: 28),
          const SizedBox(height: 12),
          const Text('还没有选择视频'),
          const SizedBox(height: 8),
          const Text(
            '选择手机里的素材，说明想要保留的重点。',
            style: TextStyle(
              fontSize: 13,
              color: Color(0xff697183),
              height: 1.7,
            ),
          ),
        ])
      else
        _filesPanel(removable: true),
      if (files.isNotEmpty)
        Padding(
          padding: const EdgeInsets.only(top: 8),
          child: Align(
            alignment: Alignment.centerLeft,
            child: TextButton(onPressed: _review, child: const Text('填写素材说明')),
          ),
        ),
      if (localError != null)
        Padding(
          padding: const EdgeInsets.only(top: 12),
          child: Text(
            localError!,
            style: const TextStyle(
              fontSize: 13,
              color: Color(0xffa33a3a),
              height: 1.7,
            ),
          ),
        ),
    ],
  );

  Widget _progress() => RefreshIndicator(
    onRefresh: store.refresh,
    child: ListView(
      physics: const AlwaysScrollableScrollPhysics(),
      padding: const EdgeInsets.fromLTRB(22, 12, 22, 22),
      children: [
        SegmentedButton<int>(
          segments: const [
            ButtonSegment(value: 0, label: Text('素材传输')),
            ButtonSegment(value: 1, label: Text('制作进度')),
          ],
          selected: {progressTab},
          showSelectedIcon: false,
          style: SegmentedButton.styleFrom(
            backgroundColor: const Color(0xffe9ecf3),
            selectedBackgroundColor: Colors.white,
            selectedForegroundColor: ink,
            foregroundColor: const Color(0xff697183),
            side: BorderSide.none,
            minimumSize: const Size(48, 46),
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(14),
            ),
          ),
          onSelectionChanged: (selected) =>
              setState(() => progressTab = selected.single),
        ),
        const SizedBox(height: 26),
        if (progressTab == 0) ...[
          _title('手机里的素材'),
          if (files.isEmpty)
            _panel([
              const Text('还没有准备素材'),
              const SizedBox(height: 8),
              const Text(
                '从首页选择视频，再一起填写说明。',
                style: TextStyle(
                  fontSize: 13,
                  color: Color(0xff697183),
                  height: 1.7,
                ),
              ),
            ])
          else
            _filesPanel(removable: false),
          const SizedBox(height: 16),
          const Text(
            '这些素材尚未发送。电脑版的手机接收服务接通后，才会显示真实发送进度和接收结果。',
            style: TextStyle(
              fontSize: 13,
              color: Color(0xff697183),
              height: 1.7,
            ),
          ),
          const SizedBox(height: 18),
          OutlinedButton(
            onPressed: () => setState(() => tab = 0),
            child: const Text('返回首页准备素材'),
          ),
        ] else ...[
          Row(
            children: [
              const Expanded(
                child: Text(
                  '可查看的制作进度',
                  style: TextStyle(fontSize: 17, fontWeight: FontWeight.w500),
                ),
              ),
              IconButton(
                onPressed: store.session == null || store.loading
                    ? null
                    : store.refresh,
                tooltip: '刷新进度',
                icon: const Icon(Icons.refresh, size: 21),
              ),
            ],
          ),
          const SizedBox(height: 12),
          if (store.session == null)
            _panel([
              const Icon(Icons.wifi, color: blue, size: 28),
              const SizedBox(height: 12),
              const Text('连接曜核后查看制作进度'),
              const SizedBox(height: 12),
              const Text(
                '主机核验身份后才会返回你有权查看的任务。',
                style: TextStyle(
                  fontSize: 13,
                  color: Color(0xff697183),
                  height: 1.7,
                ),
              ),
              const SizedBox(height: 16),
              FilledButton(onPressed: _connect, child: const Text('连接曜核')),
            ])
          else ...[
            if (store.loading) const LinearProgressIndicator(),
            if (store.error != null)
              Padding(
                padding: const EdgeInsets.only(bottom: 16),
                child: _panel([
                  Text(
                    store.error!,
                    style: const TextStyle(
                      fontSize: 13,
                      color: Color(0xffa33a3a),
                      height: 1.7,
                    ),
                  ),
                  TextButton(
                    onPressed: store.loading ? null : store.refresh,
                    child: const Text('重新读取'),
                  ),
                ]),
              ),
            if (store.lastUpdated != null)
              Padding(
                padding: const EdgeInsets.only(bottom: 16),
                child: Text(
                  '${store.connected ? '最近更新' : '仅显示最后确认的记录'} ${store.lastUpdated!.toLocal().toString().substring(0, 16)}',
                  style: const TextStyle(
                    fontSize: 12,
                    color: Color(0xff697183),
                  ),
                ),
              ),
            if (store.tasks.isEmpty && store.connected)
              _panel([
                const Text('还没有可查看的制作任务'),
                const SizedBox(height: 8),
                const Text(
                  '在电脑端开始创作后，这里会显示主机返回的进度。',
                  style: TextStyle(
                    fontSize: 13,
                    color: Color(0xff697183),
                    height: 1.7,
                  ),
                ),
              ]),
            ...store.tasks.map(
              (task) => Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: Material(
                  color: Colors.white,
                  borderRadius: BorderRadius.circular(24),
                  clipBehavior: Clip.antiAlias,
                  child: InkWell(
                    onTap: () => _taskDetails(task),
                    child: Padding(
                      padding: const EdgeInsets.all(20),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Row(
                            children: [
                              const Icon(
                                Icons.movie_outlined,
                                size: 22,
                                color: blue,
                              ),
                              const Spacer(),
                              Text(
                                _status(task['status'] as String?),
                                style: const TextStyle(
                                  fontSize: 12,
                                  color: blue,
                                ),
                              ),
                            ],
                          ),
                          const SizedBox(height: 20),
                          Text(
                            task['name'] as String,
                            style: const TextStyle(
                              fontSize: 17,
                              fontWeight: FontWeight.w500,
                            ),
                          ),
                          const SizedBox(height: 10),
                          Text(
                            _stage(task['stage'] as String?),
                            style: const TextStyle(
                              fontSize: 13,
                              color: Color(0xff697183),
                            ),
                          ),
                          const SizedBox(height: 16),
                          const Row(
                            mainAxisAlignment: MainAxisAlignment.end,
                            children: [
                              Text(
                                '查看进度',
                                style: TextStyle(fontSize: 12, color: blue),
                              ),
                              Icon(Icons.chevron_right, size: 17, color: blue),
                            ],
                          ),
                        ],
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ],
        ],
      ],
    ),
  );

  Future<void> _taskDetails(
    Map<String, dynamic> task,
  ) => showModalBottomSheet<void>(
    context: context,
    useSafeArea: true,
    isScrollControlled: true,
    builder: (context) => Padding(
      padding: EdgeInsets.fromLTRB(
        22,
        22,
        22,
        22 + MediaQuery.paddingOf(context).bottom,
      ),
      child: SingleChildScrollView(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              task['name'] as String,
              style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w500),
            ),
            const SizedBox(height: 20),
            Text(
              _status(task['status'] as String?),
              style: const TextStyle(color: blue),
            ),
            const SizedBox(height: 12),
            Text(
              '当前阶段：${_stage(task['stage'] as String?)}',
              style: const TextStyle(fontSize: 14),
            ),
            if (task['stage_reason'] is String)
              Padding(
                padding: const EdgeInsets.only(top: 16),
                child: Text(
                  task['stage_reason'] as String,
                  style: const TextStyle(color: Color(0xff697183), height: 1.7),
                ),
              ),
            const SizedBox(height: 26),
            OutlinedButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('返回进度'),
            ),
          ],
        ),
      ),
    ),
  );

  Widget _mine() => ListView(
    padding: const EdgeInsets.fromLTRB(22, 12, 22, 22),
    children: [
      Material(
        color: Colors.transparent,
        borderRadius: BorderRadius.circular(18),
        child: InkWell(
          onTap: _accountDetails,
          borderRadius: BorderRadius.circular(18),
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: 8),
            child: Row(
              children: [
                Container(
                  width: 58,
                  height: 58,
                  decoration: const BoxDecoration(
                    color: Color(0xffe7edfc),
                    shape: BoxShape.circle,
                  ),
                  child: const Icon(
                    Icons.person_outline,
                    size: 29,
                    color: Color(0xff5675bb),
                  ),
                ),
                const SizedBox(width: 15),
                const Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        '账号与个人资料',
                        style: TextStyle(
                          fontSize: 18,
                          fontWeight: FontWeight.w500,
                        ),
                      ),
                      SizedBox(height: 6),
                      Text(
                        '正式账号尚未接入，点击查看说明',
                        style: TextStyle(
                          fontSize: 12,
                          color: Color(0xff697183),
                          height: 1.7,
                        ),
                      ),
                    ],
                  ),
                ),
                const Icon(
                  Icons.chevron_right,
                  size: 19,
                  color: Color(0xff8f97a6),
                ),
              ],
            ),
          ),
        ),
      ),
      const SizedBox(height: 31),
      _title('我的连接'),
      _deviceCard(),
      const SizedBox(height: 22),
      Material(
        color: Colors.white,
        borderRadius: BorderRadius.circular(24),
        clipBehavior: Clip.antiAlias,
        child: Column(
          children: [
            ListTile(
              contentPadding: const EdgeInsets.symmetric(
                horizontal: 18,
                vertical: 9,
              ),
              onTap: _voice,
              leading: const Icon(
                Icons.mic_none_outlined,
                color: Color(0xff8090b1),
              ),
              title: const Text('我的声音样本', style: TextStyle(fontSize: 14)),
              subtitle: Text(
                sample == null ? '录制、试听后再选择发送' : '已保留声音样本，尚未发送',
                style: const TextStyle(fontSize: 12, color: Color(0xff697183)),
              ),
              trailing: const Icon(
                Icons.chevron_right,
                size: 19,
                color: Color(0xff8f97a6),
              ),
            ),
            if (store.session != null) ...[
              const Divider(
                height: 1,
                indent: 18,
                endIndent: 18,
                color: Color(0xffeef0f5),
              ),
              ListTile(
                contentPadding: const EdgeInsets.symmetric(
                  horizontal: 18,
                  vertical: 9,
                ),
                onTap: _disconnect,
                leading: const Icon(Icons.wifi, color: Color(0xff8090b1)),
                title: const Text('退出连接', style: TextStyle(fontSize: 14)),
                subtitle: const Text(
                  '手机上的素材与录音会保留',
                  style: TextStyle(fontSize: 12, color: Color(0xff697183)),
                ),
                trailing: const Icon(
                  Icons.chevron_right,
                  size: 19,
                  color: Color(0xff8f97a6),
                ),
              ),
            ],
          ],
        ),
      ),
      const SizedBox(height: 18),
      const Text(
        '选择视频和录音不会自动发送。连接后由主机核验身份，才会返回有权访问的内容。',
        style: TextStyle(fontSize: 12, color: Color(0xff697183), height: 1.7),
      ),
    ],
  );

  Future<void> _accountDetails() => showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    useSafeArea: true,
    shape: const RoundedRectangleBorder(
      borderRadius: BorderRadius.vertical(top: Radius.circular(28)),
    ),
    builder: (context) => Padding(
      padding: EdgeInsets.fromLTRB(
        22,
        16,
        22,
        22 + MediaQuery.paddingOf(context).bottom,
      ),
      child: SingleChildScrollView(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          mainAxisSize: MainAxisSize.min,
          children: [
            Row(
              children: [
                const Expanded(
                  child: Text(
                    '账号与资料说明',
                    style: TextStyle(fontSize: 20, fontWeight: FontWeight.w500),
                  ),
                ),
                IconButton(
                  tooltip: '关闭账号说明',
                  onPressed: () => Navigator.pop(context),
                  icon: const Icon(Icons.close, size: 20),
                ),
              ],
            ),
            const SizedBox(height: 16),
            const Text(
              '昵称和头像跟随正式账号统一管理。已有账号使用同一账号登录；新用户通过统一注册入口创建账号，再接受主账号的邀请。',
              style: TextStyle(fontSize: 14, height: 1.8),
            ),
            const SizedBox(height: 18),
            Container(
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                color: const Color(0xfff0f3fa),
                borderRadius: BorderRadius.circular(16),
              ),
              child: const Text(
                '当前手机版本还未接入正式账号登录，因此暂时不能注册或修改昵称、头像。现有主机开发连接也不等于正式账号登录。',
                style: TextStyle(
                  fontSize: 13,
                  color: Color(0xff697183),
                  height: 1.8,
                ),
              ),
            ),
            const SizedBox(height: 18),
            const Text(
              '本地选择素材、填写说明和录制声音不需要先注册。主机访问与个人资料设置仍由正式账号验证权限。',
              style: TextStyle(
                fontSize: 13,
                color: Color(0xff697183),
                height: 1.8,
              ),
            ),
            const SizedBox(height: 24),
            FilledButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('知道了'),
            ),
          ],
        ),
      ),
    ),
  );

  Future<void> _disconnect() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('退出当前连接'),
        content: const Text('移除此手机保存的连接记录。素材清单、说明和录音仍保留，主机上的内容不会被删除。'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('保留连接'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('确认退出'),
          ),
        ],
      ),
    );
    if (confirmed == true) await store.disconnect();
  }

  Future<void> _voice() async {
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(28)),
      ),
      builder: (context) => StatefulBuilder(
        builder: (context, update) {
          _voiceSheetUpdate = update;
          return VoiceSampleSheet(
            recording: recording,
            hasSample: sample != null,
            busy: microphoneBusy,
            playing:
                player.playing &&
                player.processingState != ProcessingState.completed,
            seconds: seconds,
            error: voiceError ?? localError,
            onRecord: _record,
            onPlay: _listen,
            onClose: () => Navigator.pop(context),
          );
        },
      ),
    );
    _voiceSheetUpdate = null;
    if (recording && !microphoneBusy) await _record();
    await player.stop();
  }

  String _status(String? status) => switch (status) {
    'created' => '已创建',
    'running' => '制作中',
    'waiting_for_user' => '待你处理',
    'completed' => '已完成',
    'failed' => '制作遇到问题',
    _ => '状态尚未确认',
  };
  String _stage(String? stage) => switch (stage) {
    'preparation' => '资料准备',
    'topic' => '选题',
    'script' => '文案',
    'narration' => '配音',
    'person' => '人物口型',
    'montage' => '剪辑',
    'subtitle' => '字幕与封面',
    'review' => '输出',
    'publish' => '发布',
    _ => '阶段尚未确认',
  };

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: store,
    builder: (context, _) => DecoratedBox(
      decoration: const BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [Color(0xfff6f7fb), Color(0xfff0f2f7), Color(0xfff7f8fc)],
          stops: [0, .62, 1],
        ),
      ),
      child: Scaffold(
        backgroundColor: Colors.transparent,
        appBar: AppBar(
          backgroundColor: Colors.transparent,
          systemOverlayStyle: const SystemUiOverlayStyle(
            statusBarColor: Colors.transparent,
            statusBarIconBrightness: Brightness.dark,
            statusBarBrightness: Brightness.light,
          ),
          titleSpacing: 22,
          title: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              if (tab == 0) ...[
                Image.asset(
                  'assets/images/app-icon.png',
                  width: 32,
                  height: 32,
                  excludeFromSemantics: true,
                ),
                const SizedBox(width: 8),
              ],
              Text(
                ['曜核', '进度', '我的'][tab],
                style: const TextStyle(
                  fontSize: 24,
                  fontWeight: FontWeight.w500,
                ),
              ),
            ],
          ),
          actions: [
            Padding(
              padding: const EdgeInsets.only(right: 16),
              child: TextButton.icon(
                onPressed: _connect,
                icon: Icon(
                  store.connected ? Icons.wifi : Icons.wifi_off,
                  size: 18,
                ),
                label: Text(
                  store.connected
                      ? '已连接'
                      : store.session == null
                      ? '连接曜核'
                      : '离线',
                ),
              ),
            ),
          ],
        ),
        body: !store.ready || !draftReady
            ? const Center(child: CircularProgressIndicator())
            : Column(
                children: [
                  Expanded(
                    child: IndexedStack(
                      index: tab,
                      children: [_home(), _progress(), _mine()],
                    ),
                  ),
                  if (tab == 0 && files.isNotEmpty)
                    Padding(
                      padding: const EdgeInsets.fromLTRB(22, 9, 22, 2),
                      child: SizedBox(
                        width: double.infinity,
                        child: FilledButton.icon(
                          onPressed: _review,
                          icon: const Icon(Icons.upload_outlined, size: 21),
                          label: Text('确认 ${files.length} 段素材与说明'),
                        ),
                      ),
                    ),
                ],
              ),
        bottomNavigationBar: CompanionNavigationBar(
          index: tab,
          onSelected: (index) => setState(() => tab = index),
        ),
      ),
    ),
  );
}

class _ConnectSheet extends StatefulWidget {
  const _ConnectSheet({required this.store});
  final MobileStore store;
  @override
  State<_ConnectSheet> createState() => _ConnectSheetState();
}

class _ConnectSheetState extends State<_ConnectSheet> {
  late final address = TextEditingController(
    text: widget.store.session?.address ?? '',
  );
  final code = TextEditingController();
  late final name = TextEditingController(
    text: widget.store.session?.name ?? '',
  );
  @override
  void dispose() {
    address.dispose();
    code.dispose();
    name.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: widget.store,
    builder: (context, _) => Padding(
      padding: EdgeInsets.fromLTRB(
        24,
        24,
        24,
        24 +
            MediaQuery.viewInsetsOf(context).bottom +
            MediaQuery.paddingOf(context).bottom,
      ),
      child: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              '连接我的曜核',
              style: TextStyle(fontSize: 22, fontWeight: FontWeight.w500),
            ),
            const SizedBox(height: 8),
            const Text(
              '在电脑端生成邀请，手机连接同一网络。',
              style: TextStyle(color: Color(0xff697183)),
            ),
            const SizedBox(height: 24),
            TextField(
              controller: address,
              keyboardType: TextInputType.url,
              autocorrect: false,
              decoration: const InputDecoration(
                labelText: '主机连接地址',
                hintText: 'https://…',
              ),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: code,
              autocorrect: false,
              decoration: const InputDecoration(labelText: '邀请代码'),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: name,
              decoration: const InputDecoration(labelText: '你的称呼'),
            ),
            if (widget.store.error != null)
              Padding(
                padding: const EdgeInsets.only(top: 16),
                child: Text(
                  widget.store.error!,
                  style: const TextStyle(color: Color(0xffa33a3a)),
                ),
              ),
            const SizedBox(height: 24),
            SizedBox(
              width: double.infinity,
              child: FilledButton(
                onPressed: widget.store.loading
                    ? null
                    : () async {
                        await widget.store.join(
                          address.text,
                          code.text,
                          name.text,
                        );
                        if (context.mounted &&
                            widget.store.session != null &&
                            widget.store.error == null) {
                          Navigator.pop(context);
                        }
                      },
                child: Text(widget.store.loading ? '正在连接…' : '连接主机'),
              ),
            ),
          ],
        ),
      ),
    ),
  );
}
