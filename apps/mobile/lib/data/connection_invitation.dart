import 'dart:convert';

Uri secureRoot(String address) {
  final uri = Uri.tryParse(address.trim());
  if (uri == null ||
      uri.scheme != 'https' ||
      uri.host.isEmpty ||
      uri.userInfo.isNotEmpty ||
      uri.hasQuery ||
      uri.hasFragment ||
      (uri.path.isNotEmpty && uri.path != '/')) {
    throw const FormatException('连接必须使用不含账号、参数或路径的 HTTPS 根地址。');
  }
  return uri.replace(path: '');
}

class CloudAuthConfiguration {
  const CloudAuthConfiguration({
    required this.url,
    required this.publishableKey,
  });
  final Uri url;
  final String publishableKey;
  factory CloudAuthConfiguration.fromJson(Map<String, dynamic> value) {
    final key = value['publishable_key'];
    if (key is! String || !key.startsWith('sb_publishable_')) {
      throw const FormatException('邀请没有有效的公开登录配置。');
    }
    return CloudAuthConfiguration(
      url: secureRoot(value['supabase_url'] as String),
      publishableKey: key,
    );
  }
  Map<String, dynamic> toJson() => {
    'supabase_url': url.toString(),
    'publishable_key': publishableKey,
  };
}

class ConnectionInvitation {
  const ConnectionInvitation({
    required this.endpoint,
    required this.deviceId,
    required this.workspaceId,
    required this.token,
    required this.purpose,
    required this.expiresAt,
    required this.caCertificatePem,
    required this.auth,
  });
  final Uri endpoint;
  final String deviceId, workspaceId, token, purpose, caCertificatePem;
  final DateTime expiresAt;
  final CloudAuthConfiguration auth;

  factory ConnectionInvitation.parse(String text, {DateTime? now}) {
    final value = jsonDecode(text);
    if (value is! Map<String, dynamic> ||
        value['type'] != 'yaohe-companion' ||
        value['version'] != 1) {
      throw const FormatException('这不是当前支持的曜核手机邀请。');
    }
    String required(String key) {
      final field = value[key];
      if (field is! String || field.trim().isEmpty) {
        throw const FormatException('邀请信息不完整，请在电脑重新生成。');
      }
      return field;
    }

    final purpose = required('purpose');
    if (purpose != 'pair' && purpose != 'invite') {
      throw const FormatException('连接用途不可识别，请在电脑重新生成。');
    }
    final expires = required('expires_at');
    final expiry = DateTime.tryParse(expires);
    if (expiry == null ||
        !RegExp(r'(Z|[+-]\d{2}:\d{2})$').hasMatch(expires) ||
        !expiry.isAfter((now ?? DateTime.now()).toUtc())) {
      throw const FormatException('邀请已过期或时间不可核验，请在电脑重新生成。');
    }
    final certificate = required('tls_ca_pem').trim();
    if (!certificate.startsWith('-----BEGIN CERTIFICATE-----') ||
        !certificate.endsWith('-----END CERTIFICATE-----') ||
        certificate.contains('PRIVATE KEY')) {
      throw const FormatException('邀请缺少有效的主机公开证书，请在电脑重新生成。');
    }
    return ConnectionInvitation(
      endpoint: secureRoot(required('endpoint')),
      deviceId: required('device_id'),
      workspaceId: required('workspace_id'),
      token: required('token'),
      purpose: purpose,
      expiresAt: expiry.toUtc(),
      caCertificatePem: certificate,
      auth: CloudAuthConfiguration.fromJson(
        value['auth'] as Map<String, dynamic>,
      ),
    );
  }
}
