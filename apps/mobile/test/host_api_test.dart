import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:yaohe_mobile/data/connection_invitation.dart';
import 'package:yaohe_mobile/data/host_api.dart';
import 'fixtures.dart';

void main() {
  test('credentials require HTTPS root without embedded secrets', () {
    for (final address in [
      'http://192.168.1.2:8000',
      'https://secret@host',
      'https://host?token=secret',
      'https://host/api',
      'http://127.0.0.1:8000',
    ]) {
      expect(() => secureRoot(address), throwsFormatException);
    }
  });
  test('changed formal member prevents reading task data', () async {
    var calls = 0;
    final api = HostApi(
      client: MockClient((request) async {
        calls++;
        expect(request.headers['Authorization'], 'Bearer token');
        expect(request.headers['X-Workspace-Id'], 'workspace-a');
        expect(request.headers.containsKey('X-Local-Member-Token'), false);
        expect(request.followRedirects, false);
        return http.Response(
          jsonEncode(identityFixture(member: 'another')),
          200,
        );
      }),
    );
    await expectLater(api.tasks(sessionFixture()), throwsA(isA<AppFailure>()));
    expect(calls, 1);
    api.close();
  });
  test('only real task projection enters the list', () async {
    final api = HostApi(
      client: MockClient(
        (request) async => http.Response(
          request.url.path.endsWith('/companion/session')
              ? jsonEncode(identityFixture())
              : '{"items":[{"id":"task-a","workspace_id":"workspace-a","name":"server task","status":"running","stage":"script"}]}',
          200,
        ),
      ),
    );
    expect((await api.tasks(sessionFixture())).single['id'], 'task-a');
    api.close();
  });
  test('a task from another workspace never enters the mobile cache', () async {
    final api = HostApi(
      client: MockClient(
        (request) async => http.Response(
          request.url.path.endsWith('/companion/session')
              ? jsonEncode(identityFixture())
              : '{"items":[{"id":"task-a","workspace_id":"workspace-b","name":"foreign task","status":"running","stage":"script"}]}',
          200,
        ),
      ),
    );
    await expectLater(api.tasks(sessionFixture()), throwsA(isA<AppFailure>()));
    api.close();
  });
  test('voice sampling tasks stay bound to the current member and workspace', () async {
    final api = HostApi(
      client: MockClient(
        (request) async => http.Response.bytes(
          utf8.encode(jsonEncode({
            'items': [
              {
                'id': 'sample-a',
                'workspace_id': 'workspace-b',
                'owner_user_id': 'member-a',
                'request_id': 'request-a',
                'purpose': 'voice_profile',
                'display_name': '声音',
                'language': 'zh',
                'reference_text': '请朗读这句话',
                'text_version': 'text-v1',
                'status': 'awaiting_sample',
              },
            ],
          })),
          200,
        ),
      ),
    );
    await expectLater(
      api.voiceSamplingTasks(sessionFixture()),
      throwsA(isA<AppFailure>()),
    );
    api.close();
  });
  for (final purpose in ['pair', 'invite']) {
    test(
      '$purpose uses only its declared route and accepts nullable name',
      () async {
        final paths = <String>[];
        final api = HostApi(
          client: MockClient((request) async {
            paths.add(request.url.path);
            if (paths.length == 1) {
              expect(jsonDecode(request.body), {
                purpose == 'pair' ? 'token' : 'invite_token':
                    'short-lived-pairing-ticket',
              });
              return http.Response(
                jsonEncode({
                  'target_workspace_id': 'workspace-a',
                  'device_id': 'device-a',
                  'member_id': 'member-a',
                }),
                200,
              );
            }
            return http.Response(jsonEncode(identityFixture(name: null)), 200);
          }),
        );
        final session = await api.acceptInvitation(
          invitationFixture(purpose: purpose),
          credentials(),
        );
        expect(session.name, isNull);
        expect(session.role, 'owner');
        expect(paths, [
          '/api/${purpose == 'pair' ? 'companion/pairings' : 'device-invites'}/accept',
          '/api/companion/session',
        ]);
        api.close();
      },
    );
  }
  test('redirect never follows bearer or retries another purpose', () async {
    var calls = 0;
    final api = HostApi(
      client: MockClient((request) async {
        calls++;
        expect(request.followRedirects, false);
        return http.Response(
          '',
          302,
          headers: {'location': 'https://other-host'},
        );
      }),
    );
    await expectLater(
      api.acceptInvitation(invitationFixture(), credentials()),
      throwsA(isA<AppFailure>()),
    );
    expect(calls, 1);
    api.close();
  });
  test(
    'pairing rejects a ticket accepted for another account or device',
    () async {
      final api = HostApi(
        client: MockClient(
          (_) async => http.Response(
            '{"target_workspace_id":"workspace-a","device_id":"other","member_id":"member-a"}',
            200,
          ),
        ),
      );
      await expectLater(
        api.acceptInvitation(invitationFixture(), credentials()),
        throwsA(isA<AppFailure>()),
      );
      api.close();
    },
  );
  test('legacy local member token is never restored as formal account', () {
    expect(
      () => HostSession.fromJson({
        'address': 'https://host',
        'member_id': 'member-a',
        'member_token': 'legacy',
      }),
      throwsFormatException,
    );
  });
}
