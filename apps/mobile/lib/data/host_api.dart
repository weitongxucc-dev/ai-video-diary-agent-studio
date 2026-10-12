import 'dart:convert';
import 'package:http/http.dart' as http;

class AppFailure implements Exception {
  const AppFailure(this.message, {this.retryable = false});
  final String message;
  final bool retryable;
  @override
  String toString() => message;
}

class HostSession {
  const HostSession({
    required this.address,
    required this.memberId,
    required this.name,
    required this.token,
  });
  final String address, memberId, name, token;
  factory HostSession.fromJson(Map<String, dynamic> value) => HostSession(
    address: value['address'] as String,
    memberId: value['member_id'] as String,
    name: value['name'] as String,
    token: value['member_token'] as String,
  );
  Map<String, dynamic> toJson() => {
    'address': address,
    'member_id': memberId,
    'name': name,
    'member_token': token,
  };
}

Uri hostAddress(String address, {bool allowLoopbackHttp = false}) {
  final uri = Uri.tryParse(address.trim());
  final loopback =
      uri != null && {'localhost', '127.0.0.1', '::1'}.contains(uri.host);
  if (uri == null ||
      uri.host.isEmpty ||
      uri.userInfo.isNotEmpty ||
      uri.hasQuery ||
      uri.hasFragment ||
      (uri.path.isNotEmpty && uri.path != '/') ||
      !(uri.scheme == 'https' ||
          (allowLoopbackHttp && loopback && uri.scheme == 'http'))) {
    throw const AppFailure('请输入主机的安全连接地址。地址以 https:// 开头，不包含账号或其他路径。');
  }
  return uri.replace(path: '');
}

class HostApi {
  HostApi({http.Client? client, this.allowLoopbackHttp = false})
    : _client = client ?? http.Client();
  final http.Client _client;
  final bool allowLoopbackHttp;

  Future<Map<String, dynamic>> _request(
    String address,
    String path, {
    String? token,
    Map<String, dynamic>? body,
  }) async {
    final base = hostAddress(address, allowLoopbackHttp: allowLoopbackHttp);
    final request = http.Request(
      body == null ? 'GET' : 'POST',
      base.resolve(path),
    )..followRedirects = false;
    if (token != null) {
      if (token.isEmpty) throw const AppFailure('连接凭证缺失，请重新连接主机。');
      request.headers['X-Local-Member-Token'] = token;
    }
    if (body != null) {
      request.headers['Content-Type'] = 'application/json';
      request.body = jsonEncode(body);
    }
    final http.Response response;
    try {
      response = await _client
          .send(request)
          .then(http.Response.fromStream)
          .timeout(const Duration(seconds: 12));
    } on Exception {
      throw const AppFailure('暂时联系不上曜核。请在电脑上打开曜核，并连接同一网络。', retryable: true);
    }
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw AppFailure(switch (response.statusCode) {
        401 => '连接已失效，请重新连接主机。',
        403 => '主机未允许这次访问，请在电脑端检查账号和使用权限。',
        404 => '这台主机还不支持这个手机入口，请更新主机软件。',
        409 => '邀请已被使用、名额已满或当前状态不允许加入。请在电脑端重新生成邀请。',
        >= 300 && < 400 => '主机地址发生跳转。请核实新地址后重新连接。',
        _ => '主机未完成这次请求，请稍后重试。',
      }, retryable: response.statusCode >= 500);
    }
    try {
      return jsonDecode(utf8.decode(response.bodyBytes))
          as Map<String, dynamic>;
    } on FormatException {
      throw const AppFailure('主机返回的内容无法读取，请检查主机软件版本。');
    } on TypeError {
      throw const AppFailure('主机返回的内容与手机端不匹配，请检查主机软件版本。');
    }
  }

  Future<HostSession> join(String address, String code, String name) async {
    if (code.trim().isEmpty || name.trim().isEmpty) {
      throw const AppFailure('请填写邀请代码和你的称呼。');
    }
    final canonical = hostAddress(
      address,
      allowLoopbackHttp: allowLoopbackHttp,
    ).toString();
    final data = await _request(
      canonical,
      '/api/members/join',
      body: {'code': code.trim(), 'name': name.trim()},
    );
    if (data['member_id'] is! String ||
        data['name'] is! String ||
        data['member_token'] is! String ||
        (data['member_token'] as String).isEmpty) {
      throw const AppFailure('主机没有返回有效的连接凭证，请检查主机软件版本。');
    }
    return HostSession.fromJson({...data, 'address': canonical});
  }

  Future<List<Map<String, dynamic>>> tasks(
    HostSession session, {
    bool Function()? connectionIsCurrent,
  }) async {
    final identity = await _request(
      session.address,
      '/api/members/whoami',
      token: session.token,
    );
    if (identity['member_id'] != session.memberId) {
      throw const AppFailure('主机上的使用者发生变化，请重新连接。');
    }
    if (connectionIsCurrent != null && !connectionIsCurrent()) {
      throw const AppFailure('连接已取消。');
    }
    final data = await _request(
      session.address,
      '/api/creation-tasks',
      token: session.token,
    );
    if (data['items'] is! List) throw const AppFailure('主机未返回可读取的制作进度。');
    try {
      final items = List<Map<String, dynamic>>.from(data['items'] as List);
      if (items.any(
        (item) => [
          'id',
          'name',
          'status',
          'stage',
        ].any((key) => item[key] is! String),
      )) {
        throw const AppFailure('主机返回的制作进度缺少必要内容。');
      }
      return items;
    } on TypeError {
      throw const AppFailure('主机返回的制作进度无法读取。');
    }
  }

  void close() => _client.close();
}
