import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:crypto/crypto.dart';
import 'connection_invitation.dart';

class AuthenticationFailure implements Exception {
  const AuthenticationFailure(this.message, {this.retryable = false});
  final String message;
  final bool retryable;
  @override
  String toString() => message;
}

class AuthTokens {
  const AuthTokens({
    required this.accessToken,
    required this.refreshToken,
    required this.userId,
    required this.expiresAt,
  });
  final String accessToken, refreshToken, userId;
  final DateTime expiresAt;
  factory AuthTokens.fromResponse(Map<String, dynamic> value, DateTime now) {
    final access = value['access_token'], refresh = value['refresh_token'];
    final user = value['user'], expires = value['expires_in'];
    if (value['token_type'] != 'bearer' ||
        access is! String ||
        access.isEmpty ||
        refresh is! String ||
        refresh.isEmpty ||
        user is! Map<String, dynamic> ||
        user['id'] is! String ||
        (user['id'] as String).isEmpty ||
        expires is! num ||
        expires <= 0) {
      throw const AuthenticationFailure('登录服务没有返回有效会话，请重新登录。');
    }
    return AuthTokens(
      accessToken: access,
      refreshToken: refresh,
      userId: user['id'] as String,
      expiresAt: now.toUtc().add(Duration(seconds: expires.toInt())),
    );
  }
  factory AuthTokens.fromJson(Map<String, dynamic> value) => AuthTokens(
    accessToken: value['access_token'] as String,
    refreshToken: value['refresh_token'] as String,
    userId: value['user_id'] as String,
    expiresAt: DateTime.parse(value['expires_at'] as String).toUtc(),
  );
  Map<String, dynamic> toJson() => {
    'access_token': accessToken,
    'refresh_token': refreshToken,
    'user_id': userId,
    'expires_at': expiresAt.toIso8601String(),
  };
}

/// Supabase Auth REST /token grants, checked against its official OpenAPI.
/// Uses system trust exclusively; host-specific CA configuration never enters it.
class CloudAuthApi {
  CloudAuthApi({http.Client? client, DateTime Function()? now})
    : _client = client ?? http.Client(),
      _now = now ?? DateTime.now;
  final http.Client _client;
  final DateTime Function() _now;

  Future<AuthTokens> _grant(
    CloudAuthConfiguration configuration,
    String grant,
    Map<String, String> body,
  ) async {
    final root = secureRoot(configuration.url.toString());
    final request =
        http.Request(
            'POST',
            root
                .resolve('/auth/v1/token')
                .replace(queryParameters: {'grant_type': grant}),
          )
          ..followRedirects = false
          ..headers.addAll({
            'apikey': configuration.publishableKey,
            'Content-Type': 'application/json',
          })
          ..body = jsonEncode(body);
    final http.Response response;
    try {
      response = await _client
          .send(request)
          .then(http.Response.fromStream)
          .timeout(const Duration(seconds: 15));
    } on Exception {
      throw const AuthenticationFailure(
        '暂时无法联系登录服务，请检查网络后重试。',
        retryable: true,
      );
    }
    if (response.statusCode >= 300 && response.statusCode < 400) {
      throw const AuthenticationFailure('登录服务发生地址跳转，请重新核对电脑上的邀请。');
    }
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw AuthenticationFailure(
        response.statusCode == 429
            ? '登录请求过于频繁，请稍后重试。'
            : grant == 'refresh_token'
            ? '登录已失效，请重新登录。'
            : '登录未成功，请检查账号和密码。',
        retryable: response.statusCode == 429 || response.statusCode >= 500,
      );
    }
    try {
      return AuthTokens.fromResponse(
        jsonDecode(utf8.decode(response.bodyBytes)) as Map<String, dynamic>,
        _now(),
      );
    } on FormatException {
      throw const AuthenticationFailure('登录服务返回的内容无法读取。');
    } on TypeError {
      throw const AuthenticationFailure('登录服务返回的会话不完整。');
    }
  }

  Future<AuthTokens> signIn(
    CloudAuthConfiguration configuration,
    String account,
    String password,
  ) {
    final normalized = account.trim();
    if (normalized.isEmpty || password.isEmpty) {
      throw const AuthenticationFailure('请输入账号和密码。');
    }
    if (!RegExp(r'^[A-Za-z0-9._-]{3,32}$').hasMatch(normalized)) {
      throw const AuthenticationFailure('账号需为 3–32 位字母、数字、点、横线或下划线。');
    }
    // Exact desktop sign-in contract. ASCII validation makes NFKC a no-op.
    final digest = sha256
        .convert(utf8.encode(normalized.toLowerCase()))
        .toString()
        .substring(0, 24);
    final identifier = 'u_$digest@account.ai-video-diary.local';
    return _grant(configuration, 'password', {
      'email': identifier,
      'password': password,
    });
  }

  Future<AuthTokens> refresh(
    CloudAuthConfiguration configuration,
    AuthTokens previous,
  ) async {
    final next = await _grant(configuration, 'refresh_token', {
      'refresh_token': previous.refreshToken,
    });
    if (next.userId != previous.userId) {
      throw const AuthenticationFailure('登录使用者发生变化，请重新连接。');
    }
    return next;
  }

  void close() => _client.close();
}
