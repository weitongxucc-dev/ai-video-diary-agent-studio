import 'dart:async';
import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:yaohe_mobile/data/auth_session.dart';
import 'package:yaohe_mobile/data/connection_invitation.dart';
import 'package:yaohe_mobile/data/host_api.dart';
import 'package:yaohe_mobile/data/mobile_store.dart';
import 'fixtures.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  test(
    'QR requires explicit purpose, HTTPS and current non-secret trust configuration',
    () {
      final payload = {
        'type': 'yaohe-companion',
        'version': 1,
        'purpose': 'pair',
        'endpoint': 'https://host',
        'device_id': 'device-a',
        'workspace_id': 'workspace-a',
        'token': 'ticket',
        'expires_at': '2100-01-01T00:00:00Z',
        'tls_ca_pem': sessionFixture().caCertificatePem,
        'auth': authConfiguration.toJson(),
      };
      expect(ConnectionInvitation.parse(jsonEncode(payload)).purpose, 'pair');
      for (final invalid in [
        {...payload}..remove('purpose'),
        {...payload, 'purpose': 'other'},
        {...payload, 'endpoint': 'http://host'},
        {...payload, 'expires_at': '2020-01-01T00:00:00Z'},
        {...payload, 'tls_ca_pem': '-----BEGIN PRIVATE KEY-----'},
      ]) {
        expect(
          () => ConnectionInvitation.parse(jsonEncode(invalid)),
          throwsFormatException,
        );
      }
    },
  );
  test(
    'cloud password and refresh use independent system-trust endpoint without redirects',
    () async {
      final requests = <http.Request>[];
      final auth = CloudAuthApi(
        client: MockClient((request) async {
          requests.add(request);
          return http.Response(
            jsonEncode({
              'access_token': 'access',
              'refresh_token': 'refresh',
              'token_type': 'bearer',
              'expires_in': 3600,
              'user': {'id': 'member-a'},
            }),
            200,
          );
        }),
      );
      final signed = await auth.signIn(
        authConfiguration,
        ' AbC ',
        'private-password',
      );
      await auth.refresh(authConfiguration, signed);
      expect(requests.map((request) => request.url.host).toSet(), {
        'cloud.example',
      });
      expect(
        requests.map((request) => request.url.queryParameters['grant_type']),
        ['password', 'refresh_token'],
      );
      expect(jsonDecode(requests.first.body), {
        'email': 'u_ba7816bf8f01cfea414140de@account.ai-video-diary.local',
        'password': 'private-password',
      });
      expect(jsonDecode(requests.last.body), {'refresh_token': 'refresh'});
      expect(requests.every((request) => !request.followRedirects), true);
      auth.close();
    },
  );
  test('cloud redirect cannot forward login secrets', () async {
    var calls = 0;
    final auth = CloudAuthApi(
      client: MockClient((request) async {
        calls++;
        return http.Response('', 307, headers: {'location': 'https://another'});
      }),
    );
    await expectLater(
      auth.signIn(authConfiguration, 'abc', 'password'),
      throwsA(isA<AuthenticationFailure>()),
    );
    expect(calls, 1);
    auth.close();
  });
  test(
    'concurrent authorized operations share one rotating refresh and persist it',
    () async {
      FlutterSecureStorage.setMockInitialValues({});
      final release = Completer<http.Response>();
      var refreshes = 0;
      final store = MobileStore(
        auth: CloudAuthApi(
          client: MockClient((request) async {
            refreshes++;
            return release.future;
          }),
        ),
        api: HostApi(
          client: MockClient((request) async {
            expect(request.headers['Authorization'], 'Bearer fresh');
            return http.Response(jsonEncode(identityFixture()), 200);
          }),
        ),
      )..session = sessionFixture(expires: DateTime.utc(2020));
      final first = store.withSession(
        (session) async => session.credentials.accessToken,
      );
      final second = store.withSession(
        (session) async => session.credentials.refreshToken,
      );
      await Future<void>.delayed(Duration.zero);
      expect(refreshes, 1);
      release.complete(
        http.Response(
          '{"access_token":"fresh","refresh_token":"rotated","token_type":"bearer","expires_in":3600,"user":{"id":"member-a"}}',
          200,
        ),
      );
      expect(await first, 'fresh');
      expect(await second, 'rotated');
      final saved = jsonDecode(
        (await const FlutterSecureStorage().read(key: 'yaohe_session'))!,
      );
      expect(saved['credentials']['refresh_token'], 'rotated');
      store.dispose();
    },
  );
  test('refresh cannot change account identity', () async {
    final auth = CloudAuthApi(
      client: MockClient(
        (_) async => http.Response(
          '{"access_token":"fresh","refresh_token":"rotated","token_type":"bearer","expires_in":3600,"user":{"id":"other"}}',
          200,
        ),
      ),
    );
    await expectLater(
      auth.refresh(authConfiguration, credentials()),
      throwsA(isA<AuthenticationFailure>()),
    );
    auth.close();
  });
  test(
    'product account mapping matches desktop SHA-256 vectors and rejects email input',
    () async {
      var calls = 0;
      final identifiers = <String>[];
      final auth = CloudAuthApi(
        client: MockClient((request) async {
          calls++;
          identifiers.add(
            (jsonDecode(request.body) as Map<String, dynamic>)['email']
                as String,
          );
          return http.Response(
            jsonEncode({
              'access_token': 'access',
              'refresh_token': 'refresh',
              'token_type': 'bearer',
              'expires_in': 3600,
              'user': {'id': 'member-a'},
            }),
            200,
          );
        }),
      );
      for (final account in [' Tester_01 ', 'tester_01', 'USER.Name-2']) {
        await auth.signIn(authConfiguration, account, 'password');
      }
      expect(identifiers, [
        'u_e08d2a4a98b06ff5667aea26@account.ai-video-diary.local',
        'u_e08d2a4a98b06ff5667aea26@account.ai-video-diary.local',
        'u_719f0e8903cf2c43eeb84d53@account.ai-video-diary.local',
      ]);
      for (final invalid in [
        'ab',
        'a@b.test',
        'ＡＢＣ',
        '中文账号',
        'a b',
        'abcdefghijklmnopqrstuvwxyz1234567',
      ]) {
        await expectLater(
          Future(() => auth.signIn(authConfiguration, invalid, 'password')),
          throwsA(isA<AuthenticationFailure>()),
        );
      }
      expect(calls, 3);
      auth.close();
    },
  );
}
