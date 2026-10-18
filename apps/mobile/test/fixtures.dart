import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:yaohe_mobile/data/auth_session.dart';
import 'package:yaohe_mobile/data/connection_invitation.dart';
import 'package:yaohe_mobile/data/host_session.dart';

final authConfiguration = CloudAuthConfiguration(
  url: Uri.parse('https://cloud.example'),
  publishableKey: 'sb_publishable_fixture',
);
AuthTokens credentials({String access = 'token', DateTime? expires}) =>
    AuthTokens(
      accessToken: access,
      refreshToken: 'refresh-token',
      userId: 'member-a',
      expiresAt: expires ?? DateTime.utc(2100),
    );
HostSession sessionFixture({
  String token = 'token',
  String? name = 'A',
  DateTime? expires,
}) => HostSession(
  address: 'https://host',
  deviceId: 'device-a',
  workspaceId: 'workspace-a',
  memberId: 'member-a',
  name: name,
  role: 'owner',
  caCertificatePem:
      '-----BEGIN CERTIFICATE-----\nfixture\n-----END CERTIFICATE-----',
  authConfiguration: authConfiguration,
  credentials: credentials(access: token, expires: expires),
);
ConnectionInvitation invitationFixture({String purpose = 'pair'}) =>
    ConnectionInvitation(
      endpoint: Uri.parse('https://host'),
      deviceId: 'device-a',
      workspaceId: 'workspace-a',
      purpose: purpose,
      token: 'short-lived-pairing-ticket',
      expiresAt: DateTime.utc(2100),
      caCertificatePem: sessionFixture().caCertificatePem,
      auth: authConfiguration,
    );
Map<String, dynamic> identityFixture({
  String member = 'member-a',
  String? name = 'A',
}) => {
  'device_id': 'device-a',
  'workspace_id': 'workspace-a',
  'member_id': member,
  'name': name,
  'role': 'owner',
};
const testCacheKey =
    'tasks:v1:https%3A%2F%2Fhost:device-a:workspace-a:member-a';
CloudAuthApi authFixture({String access = 'new-token'}) => CloudAuthApi(
  client: MockClient(
    (_) async => http.Response(
      jsonEncode({
        'access_token': access,
        'refresh_token': 'rotated-refresh',
        'token_type': 'bearer',
        'expires_in': 3600,
        'user': {'id': 'member-a'},
      }),
      200,
    ),
  ),
);
