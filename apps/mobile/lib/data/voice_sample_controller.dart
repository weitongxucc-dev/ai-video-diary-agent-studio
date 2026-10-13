import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:path_provider/path_provider.dart';

import 'voice_sample_io.dart';

class VoiceSampleController extends ChangeNotifier {
  VoiceSampleController({
    required String? Function() sample,
    required Future<void> Function(String) onRecorded,
    VoiceRecording? recorder,
    VoicePlayback? player,
    Future<String> Function()? recordingDirectory,
  }) : _sample = sample,
       _onRecorded = onRecorded,
       _recorder = recorder ?? NativeVoiceRecording(),
       _player = player ?? NativeVoicePlayback(),
       _recordingDirectory =
           recordingDirectory ??
           (() async =>
               kIsWeb ? '' : (await getApplicationDocumentsDirectory()).path) {
    _playbackUpdates = _player.stateChanges.listen((_) {
      if (!_disposed) notifyListeners();
    });
  }

  final String? Function() _sample;
  final Future<void> Function(String) _onRecorded;
  final VoiceRecording _recorder;
  final VoicePlayback _player;
  final Future<String> Function() _recordingDirectory;
  late final StreamSubscription<void> _playbackUpdates;
  Future<void>? _operation;
  Timer? _timer;
  bool _disposed = false, _closing = false;
  bool _recording = false, _busy = false;
  int _seconds = 0;
  String? _error;

  bool get recording => _recording;
  bool get busy => _busy;
  int get seconds => _seconds;
  String? get error => _error;
  bool get playing => _player.playing;
  bool get hasSample => _sample() != null;

  void openSheet() => _closing = false;

  Future<void> toggleRecording() {
    if (_disposed || _busy || (_closing && !_recording)) return Future.value();
    final operation = _toggleRecording();
    _operation = operation;
    return operation;
  }

  Future<void> _toggleRecording() async {
    _busy = true;
    _error = null;
    notifyListeners();
    try {
      if (_recording) {
        final path = await _recorder.stop();
        _timer?.cancel();
        if (_disposed) return;
        _recording = false;
        if (path == null) {
          _error = '没有生成可用录音，请重新录制。';
          return;
        }
        await _onRecorded(path);
      } else {
        if (!await _recorder.hasPermission()) {
          if (!_disposed) {
            _error = '麦克风访问未允许。请在手机设置中允许曜核使用麦克风，再开始录音。';
          }
          return;
        }
        if (_disposed || _closing) return;
        final directory = await _recordingDirectory();
        await _player.stop();
        if (_disposed || _closing) return;
        await _recorder.start(
          '$directory/voice-${DateTime.now().millisecondsSinceEpoch}.wav',
        );
        if (_disposed) return;
        _recording = true;
        _seconds = 0;
        _timer = Timer.periodic(const Duration(seconds: 1), (_) {
          if (!_disposed) {
            _seconds++;
            notifyListeners();
          }
        });
      }
    } on Exception {
      if (!_disposed) {
        _error = _recording
            ? '尚未确认录音是否结束，请再次点击结束录音。'
            : '录音未能开始，请检查麦克风和手机存储后重试。';
      }
    } finally {
      if (!_disposed) {
        _busy = false;
        notifyListeners();
      }
    }
  }

  Future<void> listen() async {
    final sample = _sample();
    if (_disposed || _closing || sample == null) return;
    try {
      if (_player.playing) {
        await _player.stop();
        return;
      }
      _error = null;
      await _player.setSource(sample);
      if (_disposed || _closing) return;
      await _player.play();
    } on Exception {
      if (!_disposed) {
        _error = '这段录音无法播放，请重新录制。';
        notifyListeners();
      }
    }
  }

  Future<void> stopOnInactive() async {
    if (_recording && !_busy) await toggleRecording();
  }

  Future<void> closeSheet() async {
    _closing = true;
    await _operation;
    if (_disposed) return;
    if (_recording) await toggleRecording();
    await _player.stop();
  }

  @override
  void dispose() {
    _disposed = true;
    _timer?.cancel();
    unawaited(_playbackUpdates.cancel());
    unawaited(_recorder.dispose());
    unawaited(_player.dispose());
    super.dispose();
  }
}
