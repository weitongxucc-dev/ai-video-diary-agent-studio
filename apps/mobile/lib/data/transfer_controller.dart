import 'dart:async';
import 'dart:io';
import 'package:crypto/crypto.dart';
import 'package:flutter/foundation.dart';
import 'auth_session.dart';
import 'host_api.dart';
import 'local_media_draft.dart';
import 'mobile_store.dart';
import 'mobile_transfer.dart';
import 'request_id.dart';
import 'voice_sampling_draft.dart';

class TransferController extends ChangeNotifier {
  TransferController(this.store, {TransferDraftRepository? repository})
    : repository = repository ?? TransferDraftRepository() {
    store.addListener(_connectionChanged);
    _connectionChanged();
  }
  final MobileStore store;
  final TransferDraftRepository repository;
  List<TransferDraft> drafts = [];
  bool busy = false, restoring = false;
  String? error, activeId;
  int sentBytes = 0;
  String? _scope;
  bool _disposed = false;
  int _generation = 0;
  Completer<void>? _abort;
  TransferDraft? _cancelAfterStop;
  bool _stopRequested = false;
  String _key(SamplingBinding binding) =>
      '${binding.deviceId}:${binding.workspaceId}:${binding.memberId}';
  bool _current(int generation) => !_disposed && generation == _generation;
  void _connectionChanged() {
    final session = store.session;
    final next = session == null ? null : _key(session.samplingBinding);
    if (next == _scope) return;
    _scope = next;
    _generation++;
    if (_abort?.isCompleted == false) _abort!.complete();
    _abort = null;
    _cancelAfterStop = null;
    _stopRequested = false;
    drafts = [];
    busy = false;
    error = null;
    activeId = null;
    sentBytes = 0;
    if (session != null) {
      unawaited(_restore(session.samplingBinding, _generation));
    }
    if (!_disposed) notifyListeners();
  }

  Future<void> _restore(SamplingBinding binding, int generation) async {
    restoring = true;
    try {
      final values = await repository.read(binding);
      if (_current(generation)) drafts = values;
    } on Exception {
      if (_current(generation)) error = '无法读取发送记录，原素材仍保留在手机。';
    } finally {
      if (_current(generation)) {
        restoring = false;
        notifyListeners();
      }
    }
  }

  String _mime(String filename) =>
      switch (filename.split('.').last.toLowerCase()) {
        'mp4' => 'video/mp4',
        'mov' => 'video/quicktime',
        'm4v' => 'video/x-m4v',
        'webm' => 'video/webm',
        _ => 'application/octet-stream',
      };
  Future<void> prepareAndSend(
    List<DraftVideo> files,
    String description,
  ) async {
    if (busy || restoring || files.isEmpty) return;
    final session = store.session;
    if (session == null) {
      error = '请先连接自己的电脑。';
      notifyListeners();
      return;
    }
    final generation = _generation;
    busy = true;
    error = null;
    notifyListeners();
    try {
      var next = [...drafts];
      for (final video in files) {
        if (video.path == null) throw const AppFailure('素材没有可读取的本地文件，请重新选择。');
        final file = File(video.path!);
        final length = await file.length();
        final digest = (await sha256.bind(file.openRead()).first).toString();
        if (!_current(generation)) return;
        // A repeated tap resumes the same immutable declaration.
        if (!next.any(
          (draft) =>
              draft.path == video.path &&
              draft.sha256 == digest &&
              draft.description == description.trim() &&
              draft.receipt?.state != 'cancelled',
        )) {
          next.add(
            TransferDraft(
              binding: session.samplingBinding,
              requestId: newRequestId(),
              path: video.path!,
              filename: video.name,
              mimeType: _mime(video.name),
              byteLength: length,
              sha256: digest,
              description: description.trim(),
            ),
          );
        }
      }
      await repository.save(session.samplingBinding, next);
      if (!_current(generation)) return;
      drafts = next;
    } on AppFailure catch (failure) {
      if (_current(generation)) error = failure.message;
    } on Exception {
      if (_current(generation)) error = '素材读取或发送记录保存失败，尚未开始发送。';
    } finally {
      if (_current(generation)) {
        busy = false;
        notifyListeners();
      }
    }
    if (_current(generation) && error == null) await resumeAll();
  }

  Future<void> _remember(
    TransferDraft original,
    TransferReceipt receipt,
    int generation,
  ) async {
    original.verify(receipt);
    if (!_current(generation)) return;
    final next = drafts
        .map(
          (draft) => draft.requestId == original.requestId
              ? original.withReceipt(receipt)
              : draft,
        )
        .toList();
    await repository.save(original.binding, next);
    if (_current(generation)) {
      drafts = next;
      notifyListeners();
    }
  }

  Future<TransferReceipt> _queryOrDeclare(TransferDraft draft) async {
    try {
      return await store.withSession(
        (session) => store.api.transfer(session, draft),
      );
    } on AppFailure catch (failure) {
      if (failure.statusCode != 404) rethrow;
      return store.withSession(
        (session) => store.api.transfer(
          session,
          draft,
          method: 'PUT',
          declaration: true,
        ),
      );
    }
  }

  Future<void> resumeAll() async {
    if (busy || restoring) return;
    final generation = _generation;
    _stopRequested = false;
    busy = true;
    error = null;
    notifyListeners();
    try {
      for (final draft in [...drafts]) {
        if (!_current(generation)) return;
        activeId = draft.requestId;
        sentBytes = 0;
        notifyListeners();
        var receipt = await _queryOrDeclare(draft);
        await _remember(draft, receipt, generation);
        if (!_current(generation) || _stopRequested) return;
        if (['cancelled', 'completed'].contains(receipt.state)) {
          continue;
        }
        if (receipt.state == 'declared' ||
            (receipt.state == 'failed' && receipt.restartFromZero)) {
          final file = File(draft.path);
          if (await file.length() != draft.byteLength ||
              (await sha256.bind(file.openRead()).first).toString() !=
                  draft.sha256) {
            throw const AppFailure('素材文件已经变化，原请求没有重发，请重新选择新文件。');
          }
          if (!_current(generation) || _stopRequested) return;
          final abort = Completer<void>();
          _abort = abort;
          receipt = await store.withSession(
            (session) => store.api.transferContent(
              session,
              draft,
              abortTrigger: abort.future,
              onBytes: (bytes) {
                if (_current(generation)) {
                  sentBytes = bytes;
                  notifyListeners();
                }
              },
            ),
          );
          if (identical(_abort, abort)) _abort = null;
          await _remember(draft, receipt, generation);
        }
        if (!_current(generation) || _stopRequested) return;
        if (receipt.state == 'received') {
          receipt = await store.withSession(
            (session) => store.api.transfer(
              session,
              draft,
              method: 'POST',
              suffix: '/complete',
            ),
          );
          await _remember(draft, receipt, generation);
        }
      }
    } on AppFailure catch (failure) {
      if (_current(generation)) error = failure.message;
    } on AuthenticationFailure catch (failure) {
      if (_current(generation)) error = failure.message;
    } on Exception {
      if (_current(generation)) error = '发送尚未确认，请查询原发送结果。';
    } finally {
      if (_current(generation)) {
        busy = false;
        activeId = null;
        _abort = null;
        notifyListeners();
        final cancelTarget = _cancelAfterStop;
        _cancelAfterStop = null;
        if (cancelTarget != null) await cancel(cancelTarget);
      }
    }
  }

  Future<void> cancel(TransferDraft draft) async {
    // Abort only the content stream. The server remains the cancellation authority.
    if (busy) {
      _stopRequested = true;
      _cancelAfterStop = draft;
      if (_abort?.isCompleted == false) _abort!.complete();
      error = '正在停止传输并核对原请求的取消结果。';
      notifyListeners();
      return;
    }
    final generation = _generation;
    busy = true;
    error = null;
    notifyListeners();
    try {
      final receipt = await store.withSession(
        (session) => store.api.transfer(session, draft),
      );
      await _remember(draft, receipt, generation);
      if (!_current(generation)) return;
      if (receipt.state != 'completed' && receipt.state != 'cancelled') {
        final cancelled = await store.withSession(
          (session) => store.api.transfer(
            session,
            draft,
            method: 'POST',
            suffix: '/cancel',
          ),
        );
        await _remember(draft, cancelled, generation);
      }
    } on AppFailure catch (failure) {
      if (_current(generation)) error = failure.message;
    } on AuthenticationFailure catch (failure) {
      if (_current(generation)) error = failure.message;
    } on Exception {
      if (_current(generation)) error = '取消结果未确认，请查询原请求。';
    } finally {
      if (_current(generation)) {
        busy = false;
        notifyListeners();
      }
    }
  }

  @override
  void dispose() {
    _disposed = true;
    if (_abort?.isCompleted == false) _abort!.complete();
    _abort = null;
    store.removeListener(_connectionChanged);
    super.dispose();
  }
}
