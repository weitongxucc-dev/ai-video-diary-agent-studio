import 'dart:async';
import 'dart:convert';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:yaohe_mobile/data/host_api.dart';
import 'package:yaohe_mobile/data/mobile_store.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  test(
    'failed reconnect retains exact last confirmed record and time without claiming connected',
    () async {
      const session = HostSession(
        address: 'https://host',
        memberId: 'member-a',
        name: 'A',
        token: 'token',
      );
      FlutterSecureStorage.setMockInitialValues({
        'yaohe_session': jsonEncode(session.toJson()),
        'tasks:https://host:member-a': jsonEncode({
          'tasks': [
            {'id': 'persisted-task', 'status': 'running'},
          ],
          'updated_at': '2026-10-10T10:00:00.000Z',
        }),
      });
      final store = MobileStore(
        api: HostApi(client: MockClient((_) async => http.Response('', 503))),
      );
      await store.initialize();
      expect(store.connected, false);
      expect(store.tasks.single['id'], 'persisted-task');
      expect(store.lastUpdated, DateTime.parse('2026-10-10T10:00:00.000Z'));
      expect(store.error, isNotNull);
      await store.disconnect();
      expect(store.tasks, isEmpty);
      expect(store.session, isNull);
      store.dispose();
    },
  );

  const savedSession = HostSession(
    address: 'https://host',
    memberId: 'member-a',
    name: 'A',
    token: 'token',
  );
  Map<String, String> savedValues() => {
    'yaohe_session': jsonEncode(savedSession.toJson()),
  };
  http.Response success(http.Request request) => http.Response(
    request.url.path.endsWith('whoami')
        ? '{"member_id":"member-a"}'
        : '{"items":[{"id":"task-a","name":"confirmed task","status":"running","stage":"script"}]}',
    200,
  );

  testWidgets(
    'foreground retries transient failure and stops requests in background',
    (tester) async {
      FlutterSecureStorage.setMockInitialValues(savedValues());
      var available = false, calls = 0;
      final store = MobileStore(
        api: HostApi(
          client: MockClient((request) async {
            calls++;
            return available ? success(request) : http.Response('', 503);
          }),
        ),
      );
      await store.initialize();
      expect(store.connected, false);
      store.setForeground(true, refreshNow: false);
      available = true;
      await tester.pump(const Duration(seconds: 30));
      await tester.pump();
      expect(store.connected, true);
      expect(store.tasks.single['id'], 'task-a');
      store.setForeground(false);
      final beforeBackground = calls;
      await tester.pump(const Duration(seconds: 90));
      expect(calls, beforeBackground);
      store.dispose();
    },
  );

  testWidgets('invalid authorization is not retried automatically', (
    tester,
  ) async {
    FlutterSecureStorage.setMockInitialValues(savedValues());
    var calls = 0;
    final store = MobileStore(
      api: HostApi(
        client: MockClient((request) async {
          calls++;
          return http.Response('', 401);
        }),
      ),
    );
    await store.initialize();
    store.setForeground(true);
    await tester.pump(const Duration(seconds: 90));
    expect(calls, 1);
    expect(store.connected, false);
    store.dispose();
  });

  test(
    'disconnect during identity verification cannot fetch tasks or resurrect the session',
    () async {
      FlutterSecureStorage.setMockInitialValues(savedValues());
      final pendingIdentity = Completer<http.Response>();
      var delayIdentity = false, taskReads = 0;
      final store = MobileStore(
        api: HostApi(
          client: MockClient((request) async {
            if (request.url.path.endsWith('whoami') && delayIdentity) {
              return pendingIdentity.future;
            }
            if (request.url.path.endsWith('creation-tasks')) taskReads++;
            return success(request);
          }),
        ),
      );
      await store.initialize();
      expect(taskReads, 1);
      delayIdentity = true;
      final pending = store.refresh();
      await Future<void>.delayed(Duration.zero);
      await store.disconnect();
      pendingIdentity.complete(http.Response('{"member_id":"member-a"}', 200));
      await pending;
      expect(taskReads, 1);
      expect(store.session, isNull);
      expect(store.tasks, isEmpty);
      expect(store.connected, false);
      expect(
        await const FlutterSecureStorage().read(key: 'yaohe_session'),
        isNull,
      );
      store.dispose();
    },
  );
}
