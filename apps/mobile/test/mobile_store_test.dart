import 'dart:async';
import 'dart:convert';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:yaohe_mobile/data/host_api.dart';
import 'package:yaohe_mobile/data/mobile_store.dart';
import 'fixtures.dart';

class GatedStorage extends FlutterSecureStorage {
  GatedStorage([Map<String, String>? initial]) : values = {...?initial};
  final Map<String, String> values;
  Future<void> Function(String key)? readGate, writeGate, deleteGate;

  @override
  Future<String?> read({
    required String key,
    AppleOptions? iOptions,
    AndroidOptions? aOptions,
    LinuxOptions? lOptions,
    WebOptions? webOptions,
    AppleOptions? mOptions,
    WindowsOptions? wOptions,
  }) async {
    final value = values[key];
    await readGate?.call(key);
    return value;
  }

  @override
  Future<void> write({
    required String key,
    required String? value,
    AppleOptions? iOptions,
    AndroidOptions? aOptions,
    LinuxOptions? lOptions,
    WebOptions? webOptions,
    AppleOptions? mOptions,
    WindowsOptions? wOptions,
  }) async {
    await writeGate?.call(key);
    if (value == null) {
      values.remove(key);
    } else {
      values[key] = value;
    }
  }

  @override
  Future<void> delete({
    required String key,
    AppleOptions? iOptions,
    AndroidOptions? aOptions,
    LinuxOptions? lOptions,
    WebOptions? webOptions,
    AppleOptions? mOptions,
    WindowsOptions? wOptions,
  }) async {
    await deleteGate?.call(key);
    values.remove(key);
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  test(
    'failed reconnect retains exact last confirmed record and time without claiming connected',
    () async {
      final session = sessionFixture();
      FlutterSecureStorage.setMockInitialValues({
        'yaohe_session': jsonEncode(session.toJson()),
        testCacheKey: jsonEncode({
          'tasks': [
            {'id': 'persisted-task', 'status': 'running'},
          ],
          'updated_at': '2026-10-10T10:00:00.000Z',
        }),
      });
      final store = MobileStore(
        auth: authFixture(),
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

  final savedSession = sessionFixture();
  Map<String, String> savedValues() => {
    'yaohe_session': jsonEncode(savedSession.toJson()),
  };
  http.Response success(http.Request request) => http.Response(
    request.url.path.endsWith('/companion/session')
        ? jsonEncode(identityFixture())
        : '{"items":[{"id":"task-a","workspace_id":"workspace-a","name":"confirmed task","status":"running","stage":"script"}]}',
    200,
  );

  http.Response joinSuccess(http.Request request) =>
      request.url.path.endsWith('/pairings/accept')
      ? http.Response(
          jsonEncode({
            'target_workspace_id': 'workspace-a',
            'device_id': 'device-a',
            'member_id': 'member-a',
          }),
          200,
        )
      : success(request);

  test(
    'disconnect deletes a credential whose write is already in flight',
    () async {
      final entered = Completer<void>(), release = Completer<void>();
      final storage = GatedStorage();
      storage.writeGate = (key) async {
        if (key == 'yaohe_session') {
          entered.complete();
          await release.future;
        }
      };
      final store = MobileStore(
        auth: authFixture(),
        storage: storage,
        api: HostApi(
          client: MockClient((request) async => joinSuccess(request)),
        ),
      );
      addTearDown(store.dispose);
      final joining = store.connect(
        invitationFixture(),
        'member.test',
        'password',
      );
      await entered.future;
      final disconnecting = store.disconnect();
      expect(store.session, isNull);
      expect(store.tasks, isEmpty);
      release.complete();
      await Future.wait([joining, disconnecting]);
      expect(storage.values['yaohe_session'], isNull);
      expect(store.connected, false);
    },
  );

  for (final delayedKey in ['yaohe_session', testCacheKey]) {
    test(
      'initialization cannot restore $delayedKey after disconnect',
      () async {
        final entered = Completer<void>(), release = Completer<void>();
        final storage = GatedStorage({
          ...savedValues(),
          testCacheKey: jsonEncode({
            'tasks': [
              {'id': 'old-task'},
            ],
            'updated_at': '2026-10-14T00:00:00.000Z',
          }),
        });
        storage.readGate = (key) async {
          if (key == delayedKey) {
            entered.complete();
            await release.future;
          }
        };
        var requests = 0;
        final store = MobileStore(
          auth: authFixture(),
          storage: storage,
          api: HostApi(
            client: MockClient((request) async {
              requests++;
              return success(request);
            }),
          ),
        );
        addTearDown(store.dispose);
        final initializing = store.initialize();
        await entered.future;
        await store.disconnect();
        release.complete();
        await initializing;
        expect(store.ready, true);
        expect(store.session, isNull);
        expect(store.tasks, isEmpty);
        expect(store.lastUpdated, isNull);
        expect(requests, 0);
      },
    );
  }

  test('a pending disconnect may finish after store disposal', () async {
    final entered = Completer<void>(), release = Completer<void>();
    final storage = GatedStorage(savedValues());
    storage.deleteGate = (_) async {
      entered.complete();
      await release.future;
    };
    final store = MobileStore(
      auth: authFixture(),
      storage: storage,
      api: HostApi(client: MockClient((request) async => success(request))),
    );
    final disconnecting = store.disconnect();
    await entered.future;
    store.dispose();
    release.complete();
    await disconnecting;
    expect(storage.values['yaohe_session'], isNull);
  });

  test('new connection persists after an earlier pending disconnect', () async {
    final entered = Completer<void>(), release = Completer<void>();
    final storage = GatedStorage(savedValues());
    storage.deleteGate = (_) async {
      entered.complete();
      await release.future;
    };
    final store = MobileStore(
      auth: authFixture(),
      storage: storage,
      api: HostApi(client: MockClient((request) async => joinSuccess(request))),
    );
    addTearDown(store.dispose);
    await store.initialize();
    final disconnecting = store.disconnect();
    await entered.future;
    final joining = store.connect(
      invitationFixture(),
      'member.test',
      'password',
    );
    await Future<void>.delayed(Duration.zero);
    expect(store.session, isNull);
    expect(
      jsonDecode(
        storage.values['yaohe_session']!,
      )['credentials']['access_token'],
      'token',
    );
    release.complete();
    await Future.wait([disconnecting, joining]);
    expect(store.session?.credentials.accessToken, 'new-token');
    expect(
      jsonDecode(
        storage.values['yaohe_session']!,
      )['credentials']['access_token'],
      'new-token',
    );
    expect(store.connected, true);
  });

  testWidgets(
    'foreground retries transient failure and stops requests in background',
    (tester) async {
      FlutterSecureStorage.setMockInitialValues(savedValues());
      var available = false, calls = 0;
      final store = MobileStore(
        auth: authFixture(),
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
      auth: authFixture(),
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
        auth: authFixture(),
        api: HostApi(
          client: MockClient((request) async {
            if (request.url.path.endsWith('/companion/session') &&
                delayIdentity) {
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
      pendingIdentity.complete(
        http.Response(jsonEncode(identityFixture()), 200),
      );
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
