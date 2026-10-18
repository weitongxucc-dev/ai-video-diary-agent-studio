import 'dart:io';
import 'dart:convert' show jsonEncode;
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:yaohe_mobile/data/host_api.dart';
import 'fixtures.dart';

void main() {
  for (final shape in ['workspace', 'flat', 'other-task', 'other-workspace']) {
    test(
      'task detail $shape reads only the real nested task projection',
      () async {
        final task = {
          'id': shape == 'other-task' ? 'other' : 'task-a',
          'workspace_id': shape == 'other-workspace' ? 'other' : 'workspace-a',
          'name': 'actual task',
          'status': 'completed',
          'stage': 'review',
          'output_ready': true,
          'timeline_sequence': 7,
          'current_timeline_version_id': 'timeline-a',
        };
        final api = HostApi(
          client: MockClient((request) async {
            expect(request.url.path, '/api/creation-tasks/task-a');
            expect(request.headers['Authorization'], 'Bearer token');
            expect(request.headers['X-Workspace-Id'], 'workspace-a');
            return http.Response(
              jsonEncode(
                shape == 'flat'
                    ? task
                    : {
                        'task': task,
                        'plan': {},
                        'scripts': [],
                        'selected_topic': null,
                        'narration': null,
                      },
              ),
              200,
            );
          }),
        );
        addTearDown(api.close);
        if (shape == 'workspace') {
          final result = await api.creationTask(sessionFixture(), 'task-a');
          expect(result, task);
          expect(result['current_timeline_version_id'], 'timeline-a');
          expect(result['output_ready'], true);
        } else {
          await expectLater(
            api.creationTask(sessionFixture(), 'task-a'),
            throwsA(isA<AppFailure>()),
          );
        }
      },
    );
  }
  for (final kind in [
    'matching',
    'wrong-version',
    'wrong-sequence',
    'missing-receipt',
    'conflict',
  ]) {
    test('output $kind publishes only matching version-bound bytes', () async {
      final directory = await Directory.systemTemp.createTemp(
        'yaohe-output-test-',
      );
      addTearDown(() => directory.delete(recursive: true));
      final file = File('${directory.path}/result.mp4');
      final api = HostApi(
        client: MockClient((request) async {
          expect(request.url.queryParameters, {
            'expected_version_id': 'timeline-a',
          });
          expect(request.headers['Authorization'], 'Bearer token');
          expect(request.headers['X-Workspace-Id'], 'workspace-a');
          expect(request.followRedirects, false);
          return http.Response.bytes(
            [1, 2, 3, 4],
            kind == 'conflict' ? 409 : 200,
            headers: {
              'content-type': 'video/mp4',
              if (kind != 'missing-receipt')
                'x-timeline-version-id': kind == 'wrong-version'
                    ? 'timeline-b'
                    : 'timeline-a',
              if (kind != 'missing-receipt')
                'x-timeline-sequence': kind == 'wrong-sequence' ? '8' : '7',
            },
          );
        }),
      );
      addTearDown(api.close);
      final pending = api.downloadOutput(
        sessionFixture(),
        'task-a',
        file,
        expectedVersionId: 'timeline-a',
        expectedSequence: 7,
      );
      if (kind == 'matching') {
        await pending;
        expect(await file.readAsBytes(), [1, 2, 3, 4]);
      } else {
        await expectLater(pending, throwsA(isA<AppFailure>()));
        expect(await file.exists(), false);
      }
      expect(await File('${file.path}.part').exists(), false);
    });
  }
}
