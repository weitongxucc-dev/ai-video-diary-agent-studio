import 'dart:async';
import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:yaohe_mobile/data/local_media_draft.dart';
import 'package:yaohe_mobile/data/local_media_draft_controller.dart';

class DelayedDraftRepository extends LocalMediaDraftRepository {
  final firstWrite = Completer<void>();
  final writes = <LocalMediaDraft>[];
  @override
  Future<void> write(LocalMediaDraft draft) async {
    writes.add(draft);
    if (writes.length == 1) await firstWrite.future;
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUp(() => FlutterSecureStorage.setMockInitialValues({}));

  test(
    'existing draft key restores available files and retains notes',
    () async {
      FlutterSecureStorage.setMockInitialValues({
        LocalMediaDraftRepository.storageKey: jsonEncode({
          'notes': '保留原声',
          'sample': '/voice.wav',
          'files': [
            {'name': 'kept.mp4', 'size': 100, 'path': '/kept.mp4'},
            {'name': 'missing.mp4', 'size': 200, 'path': '/missing.mp4'},
          ],
        }),
      });
      final controller = LocalMediaDraftController(
        repository: LocalMediaDraftRepository(
          isWeb: false,
          fileExists: (path) async => path == '/kept.mp4',
        ),
      );
      addTearDown(controller.dispose);
      await controller.initialize();
      expect(controller.files.single.name, 'kept.mp4');
      expect(controller.notes, '保留原声');
      expect(controller.sample, '/voice.wav');
      expect(controller.error, contains('部分视频'));
      expect(controller.ready, isTrue);
    },
  );

  test(
    'web restore retains notes without claiming native media access',
    () async {
      FlutterSecureStorage.setMockInitialValues({
        LocalMediaDraftRepository.storageKey: jsonEncode({
          'notes': '手机说明',
          'sample': '/old-voice.wav',
          'files': [
            {'name': 'old.mp4', 'size': 2, 'path': '/old.mp4'},
          ],
        }),
      });
      final repository = LocalMediaDraftRepository(isWeb: true);
      final restored = await repository.read();
      expect(restored.notes, '手机说明');
      expect(restored.sample, isNull);
      expect(restored.files, isEmpty);
      await repository.write(restored);
      final saved = jsonDecode(
        (await const FlutterSecureStorage().read(
          key: LocalMediaDraftRepository.storageKey,
        ))!,
      );
      expect(saved['notes'], '手机说明');
      expect(saved['sample'], isNull);
      expect(saved['files'], isEmpty);
    },
  );

  test(
    'concurrent selections produce one append and persistent draft',
    () async {
      final selection = Completer<List<DraftVideo>>();
      var selections = 0;
      final controller = LocalMediaDraftController(
        selectVideos: () {
          selections++;
          return selection.future;
        },
      );
      addTearDown(controller.dispose);
      await controller.initialize();
      final choosing = controller.choose();
      expect(await controller.choose(), isFalse);
      selection.complete([
        const DraftVideo(name: 'one.mp4', size: 10, path: '/one.mp4'),
      ]);
      expect(await choosing, isTrue);
      expect(selections, 1);
      expect(controller.files.length, 1);
      final saved = jsonDecode(
        (await const FlutterSecureStorage().read(
          key: LocalMediaDraftRepository.storageKey,
        ))!,
      );
      expect(saved['files'].single['path'], '/one.mp4');
    },
  );

  test('notes and recorded sample save in order through one owner', () async {
    final repository = DelayedDraftRepository();
    final controller = LocalMediaDraftController(repository: repository);
    addTearDown(controller.dispose);
    controller.updateNotes('第一份说明');
    final first = controller.save();
    controller.updateNotes('最后一份说明');
    final second = controller.setSample('/final.wav');
    await Future<void>.delayed(Duration.zero);
    expect(repository.writes.length, 1);
    repository.firstWrite.complete();
    await Future.wait([first, second]);
    expect(repository.writes.last.notes, '最后一份说明');
    expect(repository.writes.last.sample, '/final.wav');
  });

  test('late file selection cannot restore a disposed page draft', () async {
    final selection = Completer<List<DraftVideo>>();
    final controller = LocalMediaDraftController(
      selectVideos: () => selection.future,
    );
    final choosing = controller.choose();
    controller.dispose();
    selection.complete([
      const DraftVideo(name: 'late.mp4', size: 10, path: '/late.mp4'),
    ]);
    expect(await choosing, isFalse);
    expect(controller.files, isEmpty);
    expect(
      await const FlutterSecureStorage().read(
        key: LocalMediaDraftRepository.storageKey,
      ),
      isNull,
    );
  });
}
