import 'dart:async';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:path_provider/path_provider.dart';
import 'package:video_player/video_player.dart';
import '../data/auth_session.dart';
import '../data/host_api.dart';
import '../data/mobile_store.dart';
import '../data/request_id.dart';

class TaskResultSheet extends StatefulWidget {
  const TaskResultSheet({super.key, required this.store, required this.taskId});
  final MobileStore store;
  final String taskId;
  @override
  State<TaskResultSheet> createState() => _TaskResultSheetState();
}

class _TaskResultSheetState extends State<TaskResultSheet>
    with WidgetsBindingObserver {
  Map<String, dynamic>? task;
  VideoPlayerController? player;
  String? error;
  bool loading = false;
  int downloaded = 0;
  int? total;
  late final String binding;
  String get _binding => widget.store.session == null
      ? ''
      : '${widget.store.session!.deviceId}:${widget.store.session!.workspaceId}:${widget.store.session!.memberId}';
  bool get _current => mounted && binding == _binding;
  @override
  void initState() {
    super.initState();
    binding = _binding;
    WidgetsBinding.instance.addObserver(this);
    widget.store.addListener(_sessionChanged);
    unawaited(_load());
  }

  void _sessionChanged() {
    if (mounted && binding != _binding) {
      unawaited(player?.pause());
      setState(() => error = '连接身份已变化，请返回进度重新打开作品。');
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state != AppLifecycleState.resumed) unawaited(player?.pause());
  }

  Future<void> _load() async {
    if (loading || !_current) return;
    setState(() {
      loading = true;
      error = null;
    });
    try {
      final current = await widget.store.withSession(
        (session) => widget.store.api.creationTask(session, widget.taskId),
      );
      if (_current) setState(() => task = current);
    } on AppFailure catch (failure) {
      if (_current) setState(() => error = failure.message);
    } on AuthenticationFailure catch (failure) {
      if (_current) setState(() => error = failure.message);
    } on Exception {
      if (_current) setState(() => error = '无法读取作品，请检查电脑连接后重试。');
    } finally {
      if (mounted) setState(() => loading = false);
    }
  }

  Future<void> _download() async {
    if (loading || !_current) return;
    setState(() {
      loading = true;
      error = null;
      downloaded = 0;
      total = null;
    });
    File? file;
    var retained = false;
    VideoPlayerController? candidate;
    try {
      final current = task;
      final version = current?['current_timeline_version_id'];
      final sequence = current?['timeline_sequence'];
      if (current == null ||
          current['id'] != widget.taskId ||
          sequence is! int ||
          current['output_ready'] != true ||
          version is! String ||
          version.isEmpty) {
        throw const AppFailure('当前版本还没有可播放成片，请稍后刷新。');
      }
      if (!_current) return;
      final directory = await getApplicationSupportDirectory();
      file = File('${directory.path}/result-${newRequestId()}.mp4');
      await widget.store.withSession(
        (session) => widget.store.api.downloadOutput(
          session,
          widget.taskId,
          file!,
          expectedVersionId: version,
          expectedSequence: sequence,
          onBytes: (received, length) {
            if (_current) {
              setState(() {
                downloaded = received;
                total = length;
              });
            }
          },
        ),
      );
      if (!_current) return;
      candidate = VideoPlayerController.file(file);
      await candidate.initialize();
      if (!_current) return;
      final previous = player;
      setState(() {
        player = candidate;
        retained = true;
        candidate = null;
        task = current;
      });
      await previous?.dispose();
    } on AppFailure catch (failure) {
      if (_current) setState(() => error = failure.message);
    } on AuthenticationFailure catch (failure) {
      if (_current) setState(() => error = failure.message);
    } on Exception {
      if (_current) setState(() => error = '成片尚不能播放，请检查连接后重新下载。');
    } finally {
      await candidate?.dispose();
      if (file != null && await file.exists() && !retained) {
        await file.delete();
      }
      if (mounted) setState(() => loading = false);
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    widget.store.removeListener(_sessionChanged);
    unawaited(player?.dispose());
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => SizedBox(
    height: MediaQuery.sizeOf(context).height * .84,
    child: Padding(
      padding: EdgeInsets.fromLTRB(
        22,
        14,
        22,
        22 + MediaQuery.paddingOf(context).bottom,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  task?['name'] as String? ?? '作品进度与成片',
                  style: const TextStyle(
                    fontSize: 20,
                    fontWeight: FontWeight.w500,
                  ),
                ),
              ),
              IconButton(
                tooltip: '关闭作品',
                onPressed: () => Navigator.pop(context),
                icon: const Icon(Icons.close),
              ),
            ],
          ),
          if (loading) const LinearProgressIndicator(),
          const SizedBox(height: 12),
          if (task?['stage_reason'] is String)
            Text(
              task!['stage_reason'] as String,
              style: const TextStyle(height: 1.6),
            ),
          if (error != null)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 12),
              child: Text(
                error!,
                style: const TextStyle(color: Color(0xffa33a3a)),
              ),
            ),
          Expanded(
            child: Center(
              child: player?.value.isInitialized == true
                  ? AspectRatio(
                      aspectRatio: player!.value.aspectRatio,
                      child: VideoPlayer(player!),
                    )
                  : Text(
                      downloaded > 0
                          ? '正在接收成片 ${(downloaded / 1048576).toStringAsFixed(1)} MB${total == null ? '' : ' / ${(total! / 1048576).toStringAsFixed(1)} MB'}'
                          : task?['output_ready'] == true
                          ? '当前作品有可播放成片'
                          : '当前版本尚无可播放成片',
                    ),
            ),
          ),
          if (player != null)
            ValueListenableBuilder<VideoPlayerValue>(
              valueListenable: player!,
              builder: (context, value, _) => FilledButton.icon(
                onPressed: !_current
                    ? null
                    : () async {
                        if (value.isPlaying) {
                          await player!.pause();
                        } else {
                          await player!.play();
                        }
                      },
                icon: Icon(value.isPlaying ? Icons.pause : Icons.play_arrow),
                label: Text(value.isPlaying ? '暂停' : '播放本次下载的成片'),
              ),
            ),
          const SizedBox(height: 10),
          OutlinedButton(
            onPressed: loading || !_current ? null : _load,
            child: const Text('刷新作品状态'),
          ),
          FilledButton(
            onPressed: loading || !_current || task?['output_ready'] != true
                ? null
                : _download,
            child: Text(player == null ? '下载并查看成片' : '重新读取成片'),
          ),
        ],
      ),
    ),
  );
}
