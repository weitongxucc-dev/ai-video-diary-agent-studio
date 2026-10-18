import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:path_provider/path_provider.dart';

import 'voice_sample_io.dart';

class VoiceSampleController extends ChangeNotifier {
  VoiceSampleController({
    required String? Function() sample,
    required Future<void> Function(String) onRecorded,
    Future<bool> Function()? beforeRecording,
    VoiceRecording? recorder,
    VoicePlayback? player,
    Future<String> Function()? recordingDirectory,
  }) : _beforeRecording = beforeRecording,
       _sample = sample,
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

  final Future<bool> Function()? _beforeRecording;
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
  bool _requestingPermission = false, _stopRecordingRequested = false;
  bool _listening = false;
  int _sheetGeneration = 0;
  int _seconds = 0;
  String? _error;

  bool get recording => _recording;
  bool get busy => _busy;
  int get seconds => _seconds;
  String? get error => _error;
  bool get playing => _player.playing;
  bool get hasSample => _sample() != null;

  void openSheet() {
    _sheetGeneration++;
    _closing = false;
  }

  Future<void> toggleRecording() {
    if (_disposed || _busy || (_closing && !_recording)) return Future.value();
    final operation = _toggleRecording();
    _operation = operation;
    return operation;
  }

  Future<void> _toggleRecording() async {
    final generation = _sheetGeneration;
    _busy = true;
    _error = null;
    notifyListeners();
    try {
      if (_recording) {
        await _finishRecording();
      } else {
        _stopRecordingRequested = false;
        _requestingPermission = true;
        final permitted = await _recorder.hasPermission();
        _requestingPermission = false;
        if (!permitted) {
          if (!_disposed) {
            _error = '麦克风访问未允许。请在手机设置中允许曜核使用麦克风，再开始录音。';
          }
          return;
        }
        if (_disposed || _closing || generation != _sheetGeneration) return;
        final directory = await _recordingDirectory();
        await _player.stop();
        if (_disposed ||
            _closing ||
            generation != _sheetGeneration ||
            _stopRecordingRequested) {
          return;
        }
        if (_beforeRecording != null && !await _beforeRecording()) return;
        if (_disposed ||
            _closing ||
            generation != _sheetGeneration ||
            _stopRecordingRequested) {
          return;
        }
        await _recorder.start(
          '$directory/voice-${DateTime.now().millisecondsSinceEpoch}.wav',
        );
        if (_disposed) return;
        _recording = true;
        _seconds = 0;
        if (_stopRecordingRequested ||
            _closing ||
            generation != _sheetGeneration) {
          await _finishRecording();
          return;
        }
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
      _requestingPermission = false;
      if (!_disposed) {
        _busy = false;
        notifyListeners();
      }
    }
  }

  Future<void> _finishRecording() async {
    final path = await _recorder.stop();
    _timer?.cancel();
    if (_disposed) return;
    _recording = false;
    if (path == null) {
      _error = '没有生成可用录音，请重新录制。';
      return;
    }
    await _onRecorded(path);
  }

  Future<void> listen() async {
    final sample = _sample();
    if (_disposed || _closing || sample == null) return;
    final generation = _sheetGeneration;
    var ownsPlaybackRequest = false;
    try {
      if (_player.playing) {
        await _player.stop();
        return;
      }
      if (_listening) return;
      _listening = true;
      ownsPlaybackRequest = true;
      _error = null;
      await _player.setSource(sample);
      if (_disposed || _closing || generation != _sheetGeneration) return;
      await _player.play();
      if (!_disposed && (_closing || generation != _sheetGeneration)) {
        await _player.stop();
      }
    } on Exception {
      if (!_disposed) {
        _error = '这段录音无法播放，请重新录制。';
        notifyListeners();
      }
    } finally {
      if (ownsPlaybackRequest) _listening = false;
    }
  }

  Future<void> stopOnInactive() async {
    // A permission dialog itself is inactive; once native start is in flight,
    // its result must be stopped even when the request has not returned yet.
    if (_recording || (_busy && !_requestingPermission)) {
      _stopRecordingRequested = true;
    }
    if (_recording && !_busy) await toggleRecording();
  }

  Future<void> closeSheet() async {
    final generation = ++_sheetGeneration;
    _closing = true;
    await _operation;
    if (_disposed || generation != _sheetGeneration) return;
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
