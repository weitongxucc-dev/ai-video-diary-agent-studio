import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:yaohe_mobile/data/host_api.dart';

import 'fixtures.dart';

Map<String, dynamic> executionFixture({
  required String status,
  String taskId = 'task-a',
  String workspaceId = 'workspace-a',
}) => {
  'task_id': taskId,
  'workspace_id': workspaceId,
  'current_stage': 'render',
  'job': {
    'id': 'job-a',
    'workspace_id': workspaceId,
    'task_id': taskId,
    'status': status,
    'progress': 42,
    'attempt': 2,
    'failure_reason': status == 'failed' ? 'render_failed' : null,
    'source': {'request_id': 'request-a', 'assistant_turn_id': 'turn-a'},
  },
};

void main() {
  for (final entry in const {
    'queued': TaskRuntimeStatus.running,
    'running': TaskRuntimeStatus.running,
    'succeeded': TaskRuntimeStatus.completed,
    'completed': TaskRuntimeStatus.completed,
    'failed': TaskRuntimeStatus.failed,
    'cancelled': TaskRuntimeStatus.failed,
    'interrupted': TaskRuntimeStatus.failed,
    'future_status': TaskRuntimeStatus.unknown,
  }.entries) {
    test('execution status ${entry.key} maps to ${entry.value.name}', () {
      final value = TaskExecutionSnapshot.fromExecution(
        executionFixture(status: entry.key),
        expectedTaskId: 'task-a',
        expectedWorkspaceId: 'workspace-a',
      );
      expect(value.status, entry.value);
      expect(value.taskId, 'task-a');
      expect(value.jobId, 'job-a');
      expect(value.requestId, 'request-a');
      expect(value.assistantTurnId, 'turn-a');
      expect(value.progress, 42);
    });
  }

  test('task execution rejects a foreign task or workspace', () {
    expect(
      () => TaskExecutionSnapshot.fromExecution(
        executionFixture(status: 'running', taskId: 'other'),
        expectedTaskId: 'task-a',
        expectedWorkspaceId: 'workspace-a',
      ),
      throwsA(isA<FormatException>()),
    );
    expect(
      () => TaskExecutionSnapshot.fromExecution(
        executionFixture(status: 'running', workspaceId: 'workspace-b'),
        expectedTaskId: 'task-a',
        expectedWorkspaceId: 'workspace-a',
      ),
      throwsA(isA<FormatException>()),
    );
    final foreignJob = executionFixture(status: 'running');
    (foreignJob['job'] as Map<String, dynamic>)['task_id'] = 'other';
    expect(
      () => TaskExecutionSnapshot.fromExecution(
        foreignJob,
        expectedTaskId: 'task-a',
        expectedWorkspaceId: 'workspace-a',
      ),
      throwsA(isA<FormatException>()),
    );
  });

  test(
    'job progress preserves failure and returns unknown without current job',
    () {
      final failed = TaskExecutionSnapshot.fromJobProgress(
        {
          'task_id': 'task-a',
          'current': {
            'id': 'job-a',
            'kind': 'render',
            'status': 'failed',
            'progress': 12,
            'failure_reason': 'render_failed',
          },
          'items': [],
          'total_count': 1,
        },
        expectedTaskId: 'task-a',
        expectedWorkspaceId: 'workspace-a',
      );
      expect(failed.status, TaskRuntimeStatus.failed);
      expect(failed.reason, 'render_failed');

      final unknown = TaskExecutionSnapshot.fromJobProgress(
        {'task_id': 'task-a', 'current': null, 'items': [], 'total_count': 0},
        expectedTaskId: 'task-a',
        expectedWorkspaceId: 'workspace-a',
      );
      expect(unknown.status, TaskRuntimeStatus.unknown);
    },
  );

  test(
    'creation execution sends formal authorization and workspace headers',
    () async {
      final api = HostApi(
        client: MockClient((request) async {
          expect(request.url.path, '/api/creation-tasks/task-a/execution');
          expect(request.headers['Authorization'], 'Bearer token');
          expect(request.headers['X-Workspace-Id'], 'workspace-a');
          return http.Response.bytes(
            utf8.encode(jsonEncode(executionFixture(status: 'running'))),
            200,
          );
        }),
      );
      addTearDown(api.close);

      final result = await api.creationExecution(sessionFixture(), 'task-a');
      expect(result.status, TaskRuntimeStatus.running);
    },
  );

  test('job progress sends the task path and rejects a foreign task', () async {
    final api = HostApi(
      client: MockClient((request) async {
        expect(request.url.path, '/api/creation-tasks/task-a/job-runs');
        return http.Response.bytes(
          utf8.encode(
            jsonEncode({
              'task_id': 'other',
              'current': null,
              'items': [],
              'total_count': 0,
            }),
          ),
          200,
        );
      }),
    );
    addTearDown(api.close);

    await expectLater(
      api.taskJobProgress(sessionFixture(), 'task-a'),
      throwsA(isA<AppFailure>()),
    );
  });
}
