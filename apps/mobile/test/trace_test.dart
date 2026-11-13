import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:yaohe_mobile/data/host_api.dart';

import 'fixtures.dart';

void main() {
  test('trace projection keeps redacted runtime summary', () {
    final trace = AgentTraceSnapshot.fromJson({
      'turn_id': 'turn-a',
      'conversation_id': 'conversation-a',
      'status': 'completed',
      'usage_status': 'known',
      'event_count': 4,
      'tool_counts': {'读取任务': 2},
      'compaction_count': 1,
      'duration_ms': 1200,
      'associations': [
        {'kind': 'conversation', 'ref': 'opaque'},
      ],
    }, expectedTurnId: 'turn-a');
    expect(trace.status, 'completed');
    expect(trace.eventCount, 4);
    expect(trace.toolCounts['读取任务'], 2);
    expect(trace.associations, ['conversation']);
  });

  test('trace rejects a foreign turn', () {
    expect(
      () => AgentTraceSnapshot.fromJson({
        'turn_id': 'other',
      }, expectedTurnId: 'turn-a'),
      throwsA(isA<FormatException>()),
    );
  });

  test('trace API sends bearer and workspace headers', () async {
    final api = HostApi(
      client: MockClient((request) async {
        expect(request.url.path, '/api/assistant/turns/turn-a/trace');
        expect(request.headers['Authorization'], 'Bearer token');
        expect(request.headers['X-Workspace-Id'], 'workspace-a');
        return http.Response.bytes(
          utf8.encode(jsonEncode({'turn_id': 'turn-a', 'status': 'running'})),
          200,
        );
      }),
    );
    addTearDown(api.close);
    final result = await api.assistantTurnTrace(sessionFixture(), 'turn-a');
    expect(result.status, 'running');
  });
}
