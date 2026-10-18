import 'auth_session.dart';
import 'connection_invitation.dart';
import 'voice_sampling_draft.dart';

class HostSession {
  const HostSession({
    required this.address,
    required this.deviceId,
    required this.workspaceId,
    required this.memberId,
    required this.name,
    required this.role,
    required this.caCertificatePem,
    required this.authConfiguration,
    required this.credentials,
  });
  final String address, deviceId, workspaceId, memberId, role, caCertificatePem;
  final String? name;
  final CloudAuthConfiguration authConfiguration;
  final AuthTokens credentials;
  SamplingBinding get samplingBinding => SamplingBinding(
    deviceId: deviceId,
    workspaceId: workspaceId,
    memberId: memberId,
  );

  HostSession withCredentials(AuthTokens value) => HostSession(
    address: address,
    deviceId: deviceId,
    workspaceId: workspaceId,
    memberId: memberId,
    name: name,
    role: role,
    caCertificatePem: caCertificatePem,
    authConfiguration: authConfiguration,
    credentials: value,
  );
  factory HostSession.fromJson(Map<String, dynamic> value) {
    if (value['format_version'] != 1 || value.containsKey('member_token')) {
      throw const FormatException('旧开发连接不能作为正式登录，请重新扫描电脑邀请。');
    }
    final credentials = AuthTokens.fromJson(
      value['credentials'] as Map<String, dynamic>,
    );
    if (credentials.userId != value['member_id'] ||
        !['owner', 'member'].contains(value['role'])) {
      throw const FormatException('保存的账号与成员身份不匹配，请重新登录。');
    }
    return HostSession(
      address: secureRoot(value['address'] as String).toString(),
      deviceId: value['device_id'] as String,
      workspaceId: value['workspace_id'] as String,
      memberId: value['member_id'] as String,
      name: value['name'] as String?,
      role: value['role'] as String,
      caCertificatePem: value['tls_ca_pem'] as String,
      authConfiguration: CloudAuthConfiguration.fromJson(
        value['auth'] as Map<String, dynamic>,
      ),
      credentials: credentials,
    );
  }
  Map<String, dynamic> toJson() => {
    'format_version': 1,
    'address': address,
    'device_id': deviceId,
    'workspace_id': workspaceId,
    'member_id': memberId,
    'name': name,
    'role': role,
    'tls_ca_pem': caCertificatePem,
    'auth': authConfiguration.toJson(),
    'credentials': credentials.toJson(),
  };
}
