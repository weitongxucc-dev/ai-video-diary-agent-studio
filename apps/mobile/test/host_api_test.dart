import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:yaohe_mobile/data/host_api.dart';

void main() {
  test(
    'credentials cannot travel over LAN HTTP or address-embedded secrets',
    () {
      for (final address in [
        'http://192.168.1.2:8000',
        'https://secret@host',
        'https://host?token=secret',
        'https://host/api',
      ]) {
        expect(() => hostAddress(address), throwsA(isA<AppFailure>()));
      }
      expect(
        () => hostAddress('http://127.0.0.1:8000'),
        throwsA(isA<AppFailure>()),
      );
      expect(
        hostAddress('http://127.0.0.1:8000', allowLoopbackHttp: true).host,
        '127.0.0.1',
      );
    },
  );

  test(
    'a changed member cannot read task data; server token is sent on identity read',
    () async {
      var calls = 0;
      final api = HostApi(
        client: MockClient((request) async {
          calls++;
          expect(request.headers['X-Local-Member-Token'], 'private-token');
          expect(request.followRedirects, false);
          return http.Response(
            jsonEncode({'member_id': 'another-member'}),
            200,
          );
        }),
      );
      expect(
        api.tasks(
          const HostSession(
            address: 'https://host',
            memberId: 'member-a',
            name: 'A',
            token: 'private-token',
          ),
        ),
        throwsA(isA<AppFailure>()),
      );
      await Future<void>.delayed(Duration.zero);
      expect(calls, 1);
      api.close();
    },
  );

  test('only server-returned task data enters the list', () async {
    final api = HostApi(
      client: MockClient((request) async {
        if (request.url.path.endsWith('whoami')) {
          return http.Response('{"member_id":"member-a"}', 200);
        }
        return http.Response(
          '{"items":[{"id":"task-a","name":"server task","status":"running","stage":"script"}]}',
          200,
        );
      }),
    );
    final tasks = await api.tasks(
      const HostSession(
        address: 'https://host',
        memberId: 'member-a',
        name: 'A',
        token: 'token',
      ),
    );
    expect(tasks.single['id'], 'task-a');
    expect(tasks.single['status'], 'running');
    api.close();
  });

  test(
    'redirect is rejected rather than resending the member secret',
    () async {
      final api = HostApi(
        client: MockClient(
          (request) async => http.Response(
            '',
            302,
            headers: {'location': 'https://other-host'},
          ),
        ),
      );
      await expectLater(
        api.join('https://host', 'invite', 'name'),
        throwsA(isA<AppFailure>()),
      );
      api.close();
    },
  );
}
