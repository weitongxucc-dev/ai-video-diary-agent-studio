import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:yaohe_mobile/data/voice_sample_controller.dart';
import 'package:yaohe_mobile/data/voice_sample_io.dart';

class RecordingFixture implements VoiceRecording {
  bool permitted = true, disposed = false, failStop = false;
  Future<bool>? pendingPermission;
  Future<void>? pendingStart;
  Completer<void>? startEntered;
  int starts = 0, stops = 0;
  @override
  Future<bool> hasPermission() => pendingPermission ?? Future.value(permitted);
  @override
  Future<void> start(String path) async {
    starts++;
    startEntered?.complete();
    await pendingStart;
  }

  @override
  Future<String?> stop() async {
    stops++;
    if (failStop) throw Exception('unconfirmed native stop');
    return '/recorded.wav';
  }

  @override
  Future<void> dispose() async => disposed = true;
}

class PlaybackFixture implements VoicePlayback {
  final changes = StreamController<void>.broadcast();
  String? source;
  Future<void>? pendingSource;
  Future<void>? pendingPlay;
  Completer<void>? playEntered;
  @override
  bool playing = false;
  @override
  Stream<void> get stateChanges => changes.stream;
  @override
  Future<void> setSource(String path) async {
    source = path;
    await pendingSource;
  }

  @override
  Future<void> play() async {
    playEntered?.complete();
    await pendingPlay;
    playing = true;
    changes.add(null);
  }

  @override
  Future<void> stop() async {
    playing = false;
    changes.add(null);
  }

  @override
  Future<void> dispose() => changes.close();
}

void main() {
  test(
    'native start returning after inactive is stopped and preserved once',
    () async {
      final started = Completer<void>(), entered = Completer<void>();
      final recorder = RecordingFixture()
        ..pendingStart = started.future
        ..startEntered = entered;
      var saves = 0;
      final controller = VoiceSampleController(
        sample: () => null,
        onRecorded: (_) async => saves++,
        recorder: recorder,
        player: PlaybackFixture(),
        recordingDirectory: () async => '/fixture',
      );
      addTearDown(controller.dispose);
      final starting = controller.toggleRecording();
      await entered.future;
      await controller.stopOnInactive();
      started.complete();
      await starting;
      expect(controller.recording, isFalse);
      expect(recorder.stops, 1);
      expect(saves, 1);
    },
  );

  test('native playback returning after close is stopped again', () async {
    final played = Completer<void>(), entered = Completer<void>();
    final player = PlaybackFixture()
      ..pendingPlay = played.future
      ..playEntered = entered;
    final controller = VoiceSampleController(
      sample: () => '/current.wav',
      onRecorded: (_) async {},
      recorder: RecordingFixture(),
      player: player,
    );
    addTearDown(controller.dispose);
    controller.openSheet();
    final listening = controller.listen();
    await entered.future;
    await controller.closeSheet();
    played.complete();
    await listening;
    expect(controller.playing, isFalse);
  });

  test('reopening cannot adopt an older pending permission request', () async {
    final permitted = Completer<bool>();
    final recorder = RecordingFixture()..pendingPermission = permitted.future;
    final controller = VoiceSampleController(
      sample: () => null,
      onRecorded: (_) async => fail('stale start'),
      recorder: recorder,
      player: PlaybackFixture(),
      recordingDirectory: () async => '/fixture',
    );
    addTearDown(controller.dispose);
    controller.openSheet();
    final starting = controller.toggleRecording();
    final closing = controller.closeSheet();
    controller.openSheet();
    permitted.complete(true);
    await Future.wait([starting, closing]);
    expect(recorder.starts, 0);
    expect(controller.recording, isFalse);
    recorder.pendingPermission = null;
    await controller.toggleRecording();
    expect(recorder.starts, 1);
    await controller.closeSheet();
  });

  test('closing during audio loading cannot restart hidden playback', () async {
    final loaded = Completer<void>();
    final player = PlaybackFixture()..pendingSource = loaded.future;
    final controller = VoiceSampleController(
      sample: () => '/current.wav',
      onRecorded: (_) async {},
      recorder: RecordingFixture(),
      player: player,
    );
    addTearDown(controller.dispose);
    controller.openSheet();
    final listening = controller.listen();
    await controller.closeSheet();
    loaded.complete();
    await listening;
    expect(controller.playing, isFalse);
  });

  test(
    'permission refusal does not start recording or create a sample',
    () async {
      final recorder = RecordingFixture()..permitted = false;
      final controller = VoiceSampleController(
        sample: () => null,
        onRecorded: (_) async => fail('no sample should be committed'),
        recorder: recorder,
        player: PlaybackFixture(),
        recordingDirectory: () async => '/fixture',
      );
      addTearDown(controller.dispose);
      await controller.toggleRecording();
      expect(recorder.starts, 0);
      expect(controller.recording, isFalse);
      expect(controller.hasSample, isFalse);
      expect(controller.error, contains('麦克风'));
    },
  );

  test(
    'closing during permission request prevents a hidden recording',
    () async {
      final permission = Completer<bool>();
      final recorder = RecordingFixture()
        ..pendingPermission = permission.future;
      final controller = VoiceSampleController(
        sample: () => null,
        onRecorded: (_) async => fail('closed request cannot create a sample'),
        recorder: recorder,
        player: PlaybackFixture(),
        recordingDirectory: () async => '/fixture',
      );
      addTearDown(controller.dispose);
      controller.openSheet();
      final starting = controller.toggleRecording();
      final closing = controller.closeSheet();
      permission.complete(true);
      await Future.wait([starting, closing]);
      expect(recorder.starts, 0);
      expect(controller.recording, isFalse);
    },
  );

  test('closing active recording commits its actual result once', () async {
    String? sample;
    var saves = 0;
    final recorder = RecordingFixture();
    final controller = VoiceSampleController(
      sample: () => sample,
      onRecorded: (path) async {
        sample = path;
        saves++;
      },
      recorder: recorder,
      player: PlaybackFixture(),
      recordingDirectory: () async => '/fixture',
    );
    addTearDown(controller.dispose);
    await controller.toggleRecording();
    expect(controller.recording, isTrue);
    await controller.closeSheet();
    expect(recorder.starts, 1);
    expect(recorder.stops, 1);
    expect(saves, 1);
    expect(sample, '/recorded.wav');
    expect(controller.recording, isFalse);
  });

  test(
    'unconfirmed stop preserves the prior sample and reports uncertainty',
    () async {
      final recorder = RecordingFixture()..failStop = true;
      final controller = VoiceSampleController(
        sample: () => '/prior.wav',
        onRecorded: (_) async =>
            fail('unconfirmed stop cannot replace a sample'),
        recorder: recorder,
        player: PlaybackFixture(),
        recordingDirectory: () async => '/fixture',
      );
      addTearDown(controller.dispose);
      await controller.toggleRecording();
      await controller.toggleRecording();
      expect(controller.hasSample, isTrue);
      expect(controller.error, contains('尚未确认录音是否结束'));
    },
  );

  test(
    'playback uses the current stored sample and stops on second request',
    () async {
      final player = PlaybackFixture();
      final controller = VoiceSampleController(
        sample: () => '/current.wav',
        onRecorded: (_) async {},
        recorder: RecordingFixture(),
        player: player,
        recordingDirectory: () async => '/fixture',
      );
      addTearDown(controller.dispose);
      await controller.listen();
      expect(player.source, '/current.wav');
      expect(controller.playing, isTrue);
      await controller.listen();
      expect(controller.playing, isFalse);
    },
  );
}
