import 'dart:convert';
import 'dart:io';
import 'package:crypto/crypto.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:yaohe_mobile/data/host_api.dart';
import 'package:yaohe_mobile/data/mobile_store.dart';
import 'package:yaohe_mobile/data/voice_sampling_controller.dart';
import 'package:yaohe_mobile/data/voice_sampling_draft.dart';
import 'fixtures.dart';

Map<String, dynamic> voiceTask({
  String status = 'awaiting_sample',
  String text = '电脑指定的唯一朗读文本',
  String? digest,
  String? submission,
}) => {
  'id': 'sample-a',
  'workspace_id': 'workspace-a',
  'owner_user_id': 'member-a',
  'request_id': 'create-sample-request',
  'purpose': 'voice_profile',
  'display_name': '我的声音',
  'language': 'zh',
  'reference_text': text,
  'text_version': 'text-v1',
  'status': status,
  'submission_request_id': submission,
  'audio_sha256': digest,
};
http.Response json(Map<String, dynamic> body) => http.Response(
  jsonEncode(body),
  200,
  headers: {'content-type': 'application/json; charset=utf-8'},
);
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  test(
    'recording and lost reply stay with the same task and submission after restart',
    () async {
      FlutterSecureStorage.setMockInitialValues({});
      final directory = await Directory.systemTemp.createTemp(
        'yaohe-voice-controller-test-',
      );
      addTearDown(() => directory.delete(recursive: true));
      final file = await File(
        '${directory.path}/sample.wav',
      ).writeAsBytes([1, 2, 3, 4]);
      final digest = sha256.convert([1, 2, 3, 4]).toString();
      var received = false, posts = 0;
      http.Client client() => MockClient((request) async {
        if (request.url.path.endsWith('/companion/session')) {
          return json(identityFixture());
        }
        if (request.url.path.endsWith('/sample')) {
          posts++;
          expect(
            request.body,
            contains('name="request_id"\r\n\r\nstable-audio-request'),
          );
          expect(request.body, contains('name="text_version"\r\n\r\ntext-v1'));
          expect(request.body, contains('name="source_device"\r\n\r\nmobile'));
          received = true;
          return http.Response('', 503);
        }
        final task = voiceTask(
          status: received ? 'received' : 'awaiting_sample',
          digest: received ? digest : null,
          submission: received ? 'stable-audio-request' : null,
        );
        return request.url.path.endsWith('/voice-sampling-tasks')
            ? json({
                'items': [task],
              })
            : json(task);
      });
      final store = MobileStore(api: HostApi(client: client()))
        ..session = sessionFixture();
      final controller = VoiceSamplingController(
        store,
        repository: VoiceSamplingDraftRepository(
          requestId: () => 'stable-audio-request',
        ),
      );
      await controller.load();
      expect(controller.task!.referenceText, '电脑指定的唯一朗读文本');
      expect(await controller.beginCapture(), true);
      await controller.saveRecording(file.path);
      await controller.submitOrQuery();
      expect(controller.queryRequired, true);
      expect(controller.received, false);
      expect(posts, 1);
      controller.dispose();
      store.dispose();
      final nextStore = MobileStore(api: HostApi(client: client()))
        ..session = sessionFixture();
      final reopened = VoiceSamplingController(nextStore);
      await reopened.load();
      expect(reopened.draft!.submissionRequestId, 'stable-audio-request');
      expect(reopened.received, true);
      expect(reopened.task!.voiceProfileId, isNull);
      await reopened.submitOrQuery();
      expect(posts, 1);
      reopened.dispose();
      nextStore.dispose();
    },
  );
  test(
    'prompt mutation blocks recording with an actionable task error',
    () async {
      FlutterSecureStorage.setMockInitialValues({});
      final store = MobileStore(
        api: HostApi(
          client: MockClient((request) async {
            if (request.url.path.endsWith('/companion/session')) {
              return json(identityFixture());
            }
            if (request.url.path.endsWith('/voice-sampling-tasks')) {
              return json({
                'items': [voiceTask()],
              });
            }
            return json(voiceTask(text: '未升级版本的另一段话'));
          }),
        ),
      )..session = sessionFixture();
      final controller = VoiceSamplingController(store);
      await controller.load();
      expect(await controller.beginCapture(), false);
      expect(controller.error, contains('采样任务已变化'));
      expect(controller.sample, isNull);
      controller.dispose();
      store.dispose();
    },
  );
  test(
    'disconnect during recording retains the original binding without adopting it',
    () async {
      FlutterSecureStorage.setMockInitialValues({});
      final repository = VoiceSamplingDraftRepository();
      final store = MobileStore(
        api: HostApi(
          client: MockClient(
            (request) async => request.url.path.endsWith('/companion/session')
                ? json(identityFixture())
                : request.url.path.endsWith('/voice-sampling-tasks')
                ? json({
                    'items': [voiceTask()],
                  })
                : json(voiceTask()),
          ),
        ),
      )..session = sessionFixture();
      final controller = VoiceSamplingController(store, repository: repository);
      await controller.load();
      final original = controller.task!;
      expect(await controller.beginCapture(), true);
      await store.disconnect();
      await controller.saveRecording('/retained-original.wav');
      expect(controller.sample, isNull);
      expect(
        (await repository.read(
          sessionFixture().samplingBinding,
          original,
        ))!.audioPath,
        '/retained-original.wav',
      );
      controller.dispose();
      store.dispose();
    },
  );
}
