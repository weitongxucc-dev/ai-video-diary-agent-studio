import 'dart:convert';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:yaohe_mobile/data/voice_sampling_draft.dart';
import 'package:yaohe_mobile/data/voice_sampling_task.dart';

VoiceSamplingTask task({
  String version = 'text-v1',
  String text = '电脑返回的同一朗读文本',
  String workspace = 'workspace-a',
}) => VoiceSamplingTask(
  id: 'sample-a',
  workspaceId: workspace,
  ownerUserId: 'user-a',
  requestId: 'original-task-request',
  purpose: 'voice_profile',
  displayName: '我的声音',
  language: 'zh',
  referenceText: text,
  textVersion: version,
  status: 'awaiting_sample',
);
const binding = SamplingBinding(
  deviceId: 'device-a',
  workspaceId: 'workspace-a',
  memberId: 'user-a',
);

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUp(() => FlutterSecureStorage.setMockInitialValues({}));

  test(
    'recording restores the exact prompt, task and stable submission identity',
    () async {
      final repository = VoiceSamplingDraftRepository(
        requestId: () => 'saved-submission',
      );
      await repository.saveRecording(binding, task(), '/private/recording.wav');
      final reopened = await VoiceSamplingDraftRepository().read(
        binding,
        task(),
      );
      expect(reopened!.submissionRequestId, 'saved-submission');
      expect(reopened.task.referenceText, task().referenceText);
      expect(reopened.task.requestId, 'original-task-request');
      expect(reopened.audioPath, '/private/recording.wav');
      expect(reopened.task.status, 'awaiting_sample');
    },
  );

  test(
    'text-version and identity changes never adopt an earlier recording',
    () async {
      final repository = VoiceSamplingDraftRepository();
      await repository.saveRecording(binding, task(), '/kept.wav');
      expect(await repository.read(binding, task(version: 'text-v2')), isNull);
      expect(
        await repository.read(
          const SamplingBinding(
            deviceId: 'device-b',
            workspaceId: 'workspace-a',
            memberId: 'user-a',
          ),
          task(),
        ),
        isNull,
      );
      await expectLater(
        repository.read(
          const SamplingBinding(
            deviceId: 'device-a',
            workspaceId: 'workspace-a',
            memberId: 'user-b',
          ),
          task(),
        ),
        throwsFormatException,
      );
      expect((await repository.read(binding, task()))!.audioPath, '/kept.wav');
    },
  );

  test(
    'a changed prompt under the same version or wrong workspace is rejected',
    () async {
      final repository = VoiceSamplingDraftRepository();
      await repository.saveRecording(binding, task(), '/kept.wav');
      await expectLater(
        repository.read(binding, task(text: '未升级版本的另一段话')),
        throwsFormatException,
      );
      await expectLater(
        repository.saveRecording(
          binding,
          task(workspace: 'another'),
          '/file.wav',
        ),
        throwsFormatException,
      );
      expect((await repository.read(binding, task()))!.audioPath, '/kept.wav');
    },
  );

  test(
    'unassigned legacy samples are retained and never treated as task samples',
    () async {
      FlutterSecureStorage.setMockInitialValues({
        'yaohe_local_draft': jsonEncode({
          'sample': '/legacy.wav',
          'files': [],
          'notes': '保留说明',
        }),
      });
      expect(
        await VoiceSamplingDraftRepository().read(binding, task()),
        isNull,
      );
      final legacy = await const FlutterSecureStorage().read(
        key: 'yaohe_local_draft',
      );
      expect(jsonDecode(legacy!)['sample'], '/legacy.wav');
    },
  );

  test(
    'a new recording gets a new request while retries keep the saved request',
    () async {
      var number = 0;
      final repository = VoiceSamplingDraftRepository(
        requestId: () => 'request-${++number}',
      );
      final first = await repository.saveRecording(
        binding,
        task(),
        '/first.wav',
      );
      expect(
        (await repository.read(binding, task()))!.submissionRequestId,
        first.submissionRequestId,
      );
      final next = await repository.saveRecording(
        binding,
        task(),
        '/second.wav',
      );
      expect(next.submissionRequestId, isNot(first.submissionRequestId));
    },
  );
}
