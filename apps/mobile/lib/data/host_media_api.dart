part of 'host_api.dart';

class _FileRequest extends http.BaseRequest with http.Abortable {
  _FileRequest(Uri url, this.file, {this.onBytes, this.abortTrigger})
    : super('PUT', url) {
    contentLength = file.lengthSync();
    headers['Content-Type'] = 'application/octet-stream';
  }
  @override
  final Future<void>? abortTrigger;
  final File file;
  final void Function(int)? onBytes;
  @override
  http.ByteStream finalize() {
    super.finalize();
    var sent = 0;
    return http.ByteStream(
      file.openRead().map((chunk) {
        sent += chunk.length;
        onBytes?.call(sent);
        return chunk;
      }),
    );
  }
}

extension HostMediaApi on HostApi {
  Future<TransferReceipt> transfer(
    HostSession session,
    TransferDraft draft, {
    String method = 'GET',
    String suffix = '',
    bool declaration = false,
  }) async {
    if (!draft.belongsTo(session.samplingBinding)) {
      throw const AppFailure('素材发送记录属于另一账号或工作区。');
    }
    final endpoint = secureRoot(session.address);
    final request =
        http.Request(
            method,
            endpoint.resolve(
              '/api/mobile-transfers/${Uri.encodeComponent(draft.requestId)}$suffix',
            ),
          )
          ..headers.addAll({
            'Authorization': 'Bearer ${session.credentials.accessToken}',
            'X-Workspace-Id': session.workspaceId,
          });
    if (declaration) {
      request.headers['Content-Type'] = 'application/json';
      request.body = jsonEncode(draft.declaration);
    }
    return _transferReceipt(
      draft,
      await _send(endpoint, session.caCertificatePem, request),
    );
  }

  TransferReceipt _transferReceipt(
    TransferDraft draft,
    Map<String, dynamic> data,
  ) {
    try {
      final receipt = TransferReceipt.fromJson(data);
      draft.verify(receipt);
      return receipt;
    } on FormatException catch (failure) {
      throw AppFailure(failure.message, code: 'invalid_execution_receipt');
    } on TypeError {
      throw const AppFailure('素材回执格式无法读取，请查询原发送请求。');
    }
  }

  Future<TransferReceipt> transferContent(
    HostSession session,
    TransferDraft draft, {
    void Function(int)? onBytes,
    Future<void>? abortTrigger,
  }) async {
    if (!draft.belongsTo(session.samplingBinding)) {
      throw const AppFailure('素材发送记录属于另一账号或工作区。');
    }
    final abort = Completer<void>();
    void stop() {
      if (!abort.isCompleted) abort.complete();
    }

    if (abortTrigger != null) unawaited(abortTrigger.then((_) => stop()));
    final endpoint = secureRoot(session.address);
    final request =
        _FileRequest(
            endpoint.resolve(
              '/api/mobile-transfers/${Uri.encodeComponent(draft.requestId)}/content',
            ),
            File(draft.path),
            onBytes: onBytes,
            abortTrigger: abort.future,
          )
          ..headers.addAll({
            'Authorization': 'Bearer ${session.credentials.accessToken}',
            'X-Workspace-Id': session.workspaceId,
          });
    return _transferReceipt(
      draft,
      await _send(
        endpoint,
        session.caCertificatePem,
        request,
        abortOnTimeout: stop,
      ),
    );
  }

  Future<Map<String, dynamic>> creationTask(
    HostSession session,
    String id,
  ) async {
    final workspace = await _json(
      session,
      '/api/creation-tasks/${Uri.encodeComponent(id)}',
    );
    // GET detail returns CreationTaskWorkspaceResponse, not the list item DTO.
    final task = workspace['task'];
    if (task is! Map<String, dynamic> ||
        task['id'] != id ||
        task['workspace_id'] != session.workspaceId ||
        task['name'] is! String ||
        task['status'] is! String ||
        task['stage'] is! String ||
        task['output_ready'] is! bool ||
        task['timeline_sequence'] is! int ||
        (task['current_timeline_version_id'] != null &&
            task['current_timeline_version_id'] is! String)) {
      throw const AppFailure('主机返回的任务详情与当前作品或工作区不一致，请刷新后重试。');
    }
    return task;
  }

  Future<TaskExecutionSnapshot> creationExecution(
    HostSession session,
    String taskId, {
    String? jobId,
  }) async {
    final suffix = jobId == null
        ? ''
        : '?job_id=${Uri.encodeQueryComponent(jobId)}';
    final data = await _json(
      session,
      '/api/creation-tasks/${Uri.encodeComponent(taskId)}/execution$suffix',
    );
    try {
      return TaskExecutionSnapshot.fromExecution(
        data,
        expectedTaskId: taskId,
        expectedWorkspaceId: session.workspaceId,
      );
    } on FormatException catch (failure) {
      throw AppFailure(failure.message, code: 'invalid_execution_receipt');
    } on TypeError {
      throw const AppFailure('主机返回的执行回执格式不匹配，请查询原任务。');
    }
  }

  Future<TaskExecutionSnapshot> taskJobProgress(
    HostSession session,
    String taskId,
  ) async {
    final data = await _json(
      session,
      '/api/creation-tasks/${Uri.encodeComponent(taskId)}/job-runs',
    );
    try {
      return TaskExecutionSnapshot.fromJobProgress(
        data,
        expectedTaskId: taskId,
        expectedWorkspaceId: session.workspaceId,
      );
    } on FormatException catch (failure) {
      throw AppFailure(failure.message);
    } on TypeError {
      throw const AppFailure('主机返回的作业进度格式不匹配，请查询原任务。');
    }
  }

  /// Download through the authenticated CA-bound transport before native playback.
  Future<void> downloadOutput(
    HostSession session,
    String taskId,
    File destination, {
    required String expectedVersionId,
    required int expectedSequence,
    void Function(int, int?)? onBytes,
  }) async {
    final endpoint = secureRoot(session.address);
    final request = _request(
      endpoint,
      session.credentials,
      Uri(
        path: '/api/creation-tasks/${Uri.encodeComponent(taskId)}/output',
        queryParameters: {'expected_version_id': expectedVersionId},
      ).toString(),
      workspaceId: session.workspaceId,
    )..followRedirects = false;
    final partial = File('${destination.path}.part');
    try {
      final response = await _client(
        endpoint,
        session.caCertificatePem,
      ).send(request).timeout(const Duration(seconds: 30));
      if (response.statusCode != 200) {
        await response.stream.drain<void>();
        throw AppFailure(
          response.statusCode == 409
              ? '作品版本已变化，请刷新任务后读取新成片。'
              : '尚未取得当前成片，请刷新任务后重试。',
          statusCode: response.statusCode,
        );
      }
      if (response.headers['x-timeline-version-id'] != expectedVersionId ||
          response.headers['x-timeline-sequence'] !=
              expectedSequence.toString()) {
        await response.stream.drain<void>();
        throw const AppFailure('成片版本回执与已打开的作品不一致，没有发布播放文件。');
      }
      if (!(response.headers['content-type'] ?? '').toLowerCase().startsWith(
        'video/',
      )) {
        await response.stream.drain<void>();
        throw const AppFailure('主机返回的内容不是可播放成片。');
      }
      var received = 0;
      final sink = partial.openWrite();
      try {
        await sink.addStream(
          response.stream.timeout(const Duration(seconds: 60)).map((chunk) {
            received += chunk.length;
            onBytes?.call(received, response.contentLength);
            return chunk;
          }),
        );
      } finally {
        await sink.close();
      }
      if (received == 0 ||
          (response.contentLength != null &&
              received != response.contentLength)) {
        throw const AppFailure('成片下载未完整完成，请重新读取。');
      }
      await partial.rename(destination.path);
    } on AppFailure {
      rethrow;
    } on Exception {
      throw const AppFailure('成片下载中断，请检查连接后重新读取。', retryable: true);
    } finally {
      if (await partial.exists()) await partial.delete();
    }
  }
}
