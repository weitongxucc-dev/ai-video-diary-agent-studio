import 'dart:convert';
import 'dart:async';
import 'dart:io';
import 'mobile_transfer.dart';
import 'package:http/http.dart' as http;
import 'auth_session.dart';
import 'connection_invitation.dart';
import 'host_session.dart';
import 'voice_sampling_draft.dart';
import 'voice_sampling_task.dart';
import 'trusted_host_client.dart'
    if (dart.library.io) 'trusted_host_client_io.dart'
    as native;
export 'host_session.dart';
part 'host_media_api.dart';

class AppFailure implements Exception {
  const AppFailure(
    this.message, {
    this.retryable = false,
    this.statusCode,
    this.code,
  });
  final String message;
  final bool retryable;
  final int? statusCode;
  final String? code;
  @override
  String toString() => message;
}

class HostApi {
  HostApi({http.Client? client}) : _injectedClient = client;
  final http.Client? _injectedClient;
  http.Client? _hostClient;
  String? _trustBinding;

  http.Client _client(Uri endpoint, String certificate) {
    if (_injectedClient != null) return _injectedClient;
    final binding = '${endpoint.origin}\n$certificate';
    if (_trustBinding != binding) {
      _hostClient?.close();
      _hostClient = native.trustedHostClient(certificate);
      _trustBinding = binding;
    }
    return _hostClient!;
  }

  Future<Map<String, dynamic>> _send(
    Uri endpoint,
    String certificate,
    http.BaseRequest request, {
    void Function()? abortOnTimeout,
  }) async {
    request.followRedirects = false;
    final http.Response response;
    try {
      response = await _client(endpoint, certificate)
          .send(request)
          .then(http.Response.fromStream)
          .timeout(
            const Duration(seconds: 60),
            onTimeout: () {
              abortOnTimeout?.call();
              throw TimeoutException('host response');
            },
          );
    } on Exception {
      throw const AppFailure('尚未取得主机回执，请检查可信连接并查询原请求后重试。', retryable: true);
    }
    if (response.statusCode < 200 || response.statusCode >= 300) {
      if (request.url.path.startsWith('/api/mobile-transfers/')) {
        Object? decoded;
        try {
          decoded = jsonDecode(utf8.decode(response.bodyBytes));
        } on FormatException {
          decoded = null;
        }
        final detail = decoded is Map<String, dynamic>
            ? decoded['detail']
            : null;
        if (detail is Map<String, dynamic> &&
            detail['code'] is String &&
            detail['message'] is String &&
            detail['retryable'] is bool) {
          throw AppFailure(
            detail['message'] as String,
            code: detail['code'] as String,
            retryable: detail['retryable'] as bool,
            statusCode: response.statusCode,
          );
        }
      }
      throw AppFailure(
        switch (response.statusCode) {
          401 => '登录已失效，请重新登录后连接。',
          403 => '当前账号、设备或工作区未获授权，请在电脑检查邀请与成员权限。',
          404 => '该对象不可访问或主机未提供这个入口。',
          409 => '原请求或当前版本存在冲突，请重新读取原结果。',
          422 => '提交内容与主机要求不一致，请重新读取采样任务或邀请。',
          >= 300 && < 400 => '主机地址发生跳转，请重新扫描电脑邀请核验，凭证没有跟随跳转。',
          _ => '主机尚未完成请求，请查询原结果后重试。',
        },
        retryable: response.statusCode >= 500,
        statusCode: response.statusCode,
      );
    }
    try {
      return jsonDecode(utf8.decode(response.bodyBytes))
          as Map<String, dynamic>;
    } on FormatException {
      throw const AppFailure('主机返回的内容无法读取，请核对软件版本。');
    } on TypeError {
      throw const AppFailure('主机返回的内容与手机不匹配，请核对软件版本。');
    }
  }

  http.Request _request(
    Uri endpoint,
    AuthTokens credentials,
    String path, {
    String? workspaceId,
    Map<String, dynamic>? body,
  }) {
    final request = http.Request(
      body == null ? 'GET' : 'POST',
      endpoint.resolve(path),
    );
    request.headers['Authorization'] = 'Bearer ${credentials.accessToken}';
    if (workspaceId != null) request.headers['X-Workspace-Id'] = workspaceId;
    if (body != null) {
      request.headers['Content-Type'] = 'application/json';
      request.body = jsonEncode(body);
    }
    return request;
  }

  Future<Map<String, dynamic>> _json(
    HostSession session,
    String path, {
    Map<String, dynamic>? body,
  }) {
    final endpoint = secureRoot(session.address);
    return _send(
      endpoint,
      session.caCertificatePem,
      _request(
        endpoint,
        session.credentials,
        path,
        workspaceId: session.workspaceId,
        body: body,
      ),
    );
  }

  Future<HostSession> acceptInvitation(
    ConnectionInvitation invitation,
    AuthTokens credentials,
  ) async {
    if (!invitation.expiresAt.isAfter(DateTime.now().toUtc())) {
      throw const AppFailure('邀请已过期，请在电脑重新生成。');
    }
    final accepted = await _send(
      invitation.endpoint,
      invitation.caCertificatePem,
      _request(
        invitation.endpoint,
        credentials,
        invitation.purpose == 'pair'
            ? '/api/companion/pairings/accept'
            : '/api/device-invites/accept',
        body: {
          invitation.purpose == 'pair' ? 'token' : 'invite_token':
              invitation.token,
        },
      ),
    );
    if (accepted['target_workspace_id'] != invitation.workspaceId ||
        (invitation.purpose == 'pair' &&
            (accepted['device_id'] != invitation.deviceId ||
                accepted['member_id'] != credentials.userId))) {
      throw const AppFailure('邀请返回的工作区与二维码不一致，没有保存连接。');
    }
    final identity = await _send(
      invitation.endpoint,
      invitation.caCertificatePem,
      _request(
        invitation.endpoint,
        credentials,
        '/api/companion/session',
        workspaceId: invitation.workspaceId,
      ),
    );
    if (identity['device_id'] != invitation.deviceId ||
        identity['workspace_id'] != invitation.workspaceId ||
        identity['member_id'] != credentials.userId ||
        (identity['name'] != null && identity['name'] is! String) ||
        !['owner', 'member'].contains(identity['role'])) {
      throw const AppFailure('主机、成员或工作区身份与邀请不一致，没有保存连接。');
    }
    return HostSession(
      address: invitation.endpoint.toString(),
      deviceId: invitation.deviceId,
      workspaceId: invitation.workspaceId,
      memberId: identity['member_id'] as String,
      name: identity['name'] as String?,
      role: identity['role'] as String,
      caCertificatePem: invitation.caCertificatePem,
      authConfiguration: invitation.auth,
      credentials: credentials,
    );
  }

  Future<void> verifySession(HostSession session) async {
    final identity = await _json(session, '/api/companion/session');
    if (identity['device_id'] != session.deviceId ||
        identity['workspace_id'] != session.workspaceId ||
        identity['member_id'] != session.memberId) {
      throw const AppFailure('主机、成员或工作区发生变化，请重新扫描电脑邀请。');
    }
  }

  Future<List<Map<String, dynamic>>> tasks(
    HostSession session, {
    bool Function()? connectionIsCurrent,
  }) async {
    await verifySession(session);
    if (connectionIsCurrent != null && !connectionIsCurrent()) {
      throw const AppFailure('连接已取消。');
    }
    final data = await _json(session, '/api/creation-tasks');
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
        throw const FormatException();
      }
      return items;
    } on TypeError {
      throw const AppFailure('主机返回的制作进度缺少必要内容。');
    } on FormatException {
      throw const AppFailure('主机返回的制作进度无法读取。');
    }
  }

  Future<List<VoiceSamplingTask>> voiceSamplingTasks(
    HostSession session,
  ) async {
    final data = await _json(session, '/api/media/voice-sampling-tasks');
    try {
      return (data['items'] as List)
          .map(
            (item) => VoiceSamplingTask.fromJson(item as Map<String, dynamic>),
          )
          .toList();
    } on FormatException {
      throw const AppFailure('声音采样任务不完整，请重新读取电脑任务。');
    } on TypeError {
      throw const AppFailure('声音采样任务格式不匹配，请核对主机版本。');
    }
  }

  Future<VoiceSamplingTask> voiceSamplingTask(
    HostSession session,
    String taskId,
  ) async {
    final data = await _json(
      session,
      '/api/media/voice-sampling-tasks/${Uri.encodeComponent(taskId)}',
    );
    try {
      return VoiceSamplingTask.fromJson(data);
    } on FormatException {
      throw const AppFailure('声音采样任务不完整，请重新读取电脑任务。');
    } on TypeError {
      throw const AppFailure('声音采样任务格式不匹配，请核对主机版本。');
    }
  }

  Future<VoiceSamplingTask> submitVoiceSample(
    HostSession session,
    VoiceSamplingDraft draft,
  ) async {
    if (jsonEncode(session.samplingBinding.toJson()) !=
        jsonEncode(draft.binding.toJson())) {
      throw const AppFailure('声音草稿属于另一设备、成员或工作区，没有发送。');
    }
    final endpoint = secureRoot(session.address);
    final request =
        http.MultipartRequest(
            'POST',
            endpoint.resolve(
              '/api/media/voice-sampling-tasks/${Uri.encodeComponent(draft.task.id)}/sample',
            ),
          )
          ..headers.addAll({
            'Authorization': 'Bearer ${session.credentials.accessToken}',
            'X-Workspace-Id': session.workspaceId,
          })
          ..fields.addAll({
            'request_id': draft.submissionRequestId,
            'text_version': draft.task.textVersion,
            'purpose': draft.task.purpose,
            'source_device': 'mobile',
          });
    request.files.add(
      await http.MultipartFile.fromPath(
        'audio',
        draft.audioPath,
        filename: 'sample.wav',
      ),
    );
    final data = await _send(endpoint, session.caCertificatePem, request);
    try {
      return VoiceSamplingTask.fromJson(data);
    } on FormatException {
      throw const AppFailure('声音接收回执不完整，请查询原采样任务。');
    } on TypeError {
      throw const AppFailure('声音接收回执格式不匹配，请查询原采样任务。');
    }
  }

  void close() {
    _hostClient?.close();
    _injectedClient?.close();
  }
}
