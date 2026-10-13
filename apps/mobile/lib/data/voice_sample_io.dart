import 'package:flutter/foundation.dart';
import 'package:just_audio/just_audio.dart';
import 'package:record/record.dart';

abstract interface class VoiceRecording {
  Future<bool> hasPermission();
  Future<void> start(String path);
  Future<String?> stop();
  Future<void> dispose();
}

abstract interface class VoicePlayback {
  bool get playing;
  Stream<void> get stateChanges;
  Future<void> setSource(String path);
  Future<void> play();
  Future<void> stop();
  Future<void> dispose();
}

class NativeVoiceRecording implements VoiceRecording {
  NativeVoiceRecording() : _recorder = AudioRecorder();
  final AudioRecorder _recorder;

  @override
  Future<bool> hasPermission() => _recorder.hasPermission();
  @override
  Future<void> start(String path) => _recorder.start(
    const RecordConfig(
      encoder: AudioEncoder.wav,
      sampleRate: 24000,
      numChannels: 1,
    ),
    path: path,
  );
  @override
  Future<String?> stop() => _recorder.stop();
  @override
  Future<void> dispose() => _recorder.dispose();
}

class NativeVoicePlayback implements VoicePlayback {
  NativeVoicePlayback() : _player = AudioPlayer();
  final AudioPlayer _player;

  @override
  bool get playing =>
      _player.playing && _player.processingState != ProcessingState.completed;
  @override
  Stream<void> get stateChanges => _player.playerStateStream.map<void>((_) {});
  @override
  Future<void> setSource(String path) async {
    await _player.setAudioSource(
      AudioSource.uri(kIsWeb ? Uri.parse(path) : Uri.file(path)),
    );
  }

  @override
  Future<void> play() => _player.play();
  @override
  Future<void> stop() => _player.stop();
  @override
  Future<void> dispose() => _player.dispose();
}
