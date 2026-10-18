import 'dart:async';
import 'dart:io';
import 'package:crypto/crypto.dart';
import 'package:flutter/foundation.dart';
import 'auth_session.dart';
import 'host_api.dart';
import 'mobile_store.dart';
import 'voice_sampling_draft.dart';
import 'voice_sampling_task.dart';

/// Owns task selection and local capture binding. The host owns task status.
class VoiceSamplingController extends ChangeNotifier {
  VoiceSamplingController(
    this.store, {
    VoiceSamplingDraftRepository? repository,
  }) : repository = repository ?? VoiceSamplingDraftRepository() {
    store.addListener(_connectionChanged);
    _connectionChanged();
  }
  final MobileStore store;
  final VoiceSamplingDraftRepository repository;
  List<VoiceSamplingTask> tasks = [];
  VoiceSamplingTask? task;
  VoiceSamplingDraft? draft;
  String? error;
  bool busy = false;
  String? _scope;
  bool _disposed = false;
  int _generation = 0;
  ({SamplingBinding binding, VoiceSamplingTask task})? _capture;
  String? get sample => draft?.audioPath;
  bool get queryRequired =>
      (draft?.queryRequired ?? false) ||
      (task?.status == 'received' && !received);
  bool get received =>
      task?.status == 'received' &&
      task?.submissionRequestId == draft?.submissionRequestId &&
      task?.audioSha256 == draft?.audioSha256;
  String get status => received
      ? '电脑已接收并核验录音；声音模型状态以电脑档案为准。'
      : queryRequired
      ? '发送结果尚未确认，请查询同一采样任务。'
      : sample != null
      ? '录音保存在当前任务下，尚未发送。'
      : task == null
      ? '请先读取并选择电脑创建的采样任务。'
      : '按电脑提供的文本录音。';
  String? _bindingKey() => store.session == null
      ? null
      : '${store.session!.deviceId}:${store.session!.workspaceId}:${store.session!.memberId}';
  void _connectionChanged() {
    final next = _bindingKey();
    if (next == _scope) return;
    _scope = next;
    _generation++;
    tasks = [];
    task = null;
    draft = null;
    error = null;
    busy = false;
    if (!_disposed) notifyListeners();
  }

  bool _current(int generation) => !_disposed && generation == _generation;
  Future<void> load() async {
    if (busy || _disposed) return;
    final generation = _generation;
    busy = true;
    error = null;
    notifyListeners();
    try {
      final values = await store.withSession(store.api.voiceSamplingTasks);
      if (!_current(generation)) return;
      tasks = values;
      final selected =
          values.where((value) => value.id == task?.id).firstOrNull ??
          values.firstOrNull;
      if (selected != null) {
        await _select(selected, generation);
      } else {
        task = null;
        draft = null;
      }
    } on AppFailure catch (failure) {
      if (_current(generation)) error = failure.message;
    } on AuthenticationFailure catch (failure) {
      if (_current(generation)) error = failure.message;
    } on Exception {
      if (_current(generation)) error = '无法恢复声音任务或录音，请检查连接与手机存储。';
    } finally {
      if (_current(generation)) {
        busy = false;
        notifyListeners();
      }
    }
  }

  Future<void> select(String id) async {
    if (busy) return;
    final value = tasks.firstWhere((value) => value.id == id);
    try {
      await _select(value, _generation);
      error = null;
    } on Exception {
      error = '无法恢复这个采样任务的录音，原文件保持保留。';
    }
    if (!_disposed) notifyListeners();
  }

  Future<void> _select(VoiceSamplingTask value, int generation) async {
    final binding = store.session!.samplingBinding;
    final restored = await repository.read(binding, value);
    if (!_current(generation)) return;
    task = value;
    draft = restored;
  }

  Future<bool> beginCapture() async {
    final generation = _generation;
    try {
      await _prepareCapture();
      return true;
    } on AppFailure catch (failure) {
      if (_current(generation)) error = failure.message;
    } on AuthenticationFailure catch (failure) {
      if (_current(generation)) error = failure.message;
    } on Exception {
      if (_current(generation)) error = '无法核验采样任务，请重新读取。';
    }
    if (_current(generation)) notifyListeners();
    return false;
  }

  Future<void> _prepareCapture() async {
    final selected = task;
    final session = store.session;
    if (selected == null ||
        session == null ||
        busy ||
        queryRequired ||
        selected.status != 'awaiting_sample') {
      throw const AppFailure('请先选择等待录音的电脑采样任务。');
    }
    final generation = _generation;
    final current = await store.withSession(
      (session) => store.api.voiceSamplingTask(session, selected.id),
    );
    if (!_current(generation) ||
        current.status != 'awaiting_sample' ||
        current.textVersion != selected.textVersion ||
        current.referenceText != selected.referenceText ||
        current.purpose != selected.purpose ||
        current.ownerUserId != session.memberId) {
      throw const AppFailure('采样任务已变化，请重新读取电脑任务。');
    }
    _capture = (binding: session.samplingBinding, task: current);
  }

  Future<void> saveRecording(String path) async {
    final capture = _capture;
    if (capture == null) throw const AppFailure('没有采样任务绑定，录音没有发送。');
    // Capture retains its original account even if the connection changed meanwhile.
    final saved = await repository.saveRecording(
      capture.binding,
      capture.task,
      path,
    );
    if (_disposed) return;
    if (_bindingKey() ==
            '${capture.binding.deviceId}:${capture.binding.workspaceId}:${capture.binding.memberId}' &&
        task?.id == capture.task.id &&
        task?.textVersion == capture.task.textVersion) {
      draft = saved;
      error = null;
      notifyListeners();
    }
  }

  void _applyReceipt(VoiceSamplingTask receipt, VoiceSamplingDraft original) {
    if (receipt.id != original.task.id ||
        receipt.textVersion != original.task.textVersion ||
        receipt.referenceText != original.task.referenceText ||
        receipt.ownerUserId != original.binding.memberId ||
        receipt.workspaceId != original.binding.workspaceId ||
        receipt.purpose != original.task.purpose) {
      throw const AppFailure('采样回执与原任务不一致，原录音保持保留。');
    }
    if (receipt.status == 'received' &&
        (receipt.submissionRequestId != original.submissionRequestId ||
            receipt.audioSha256 != original.audioSha256)) {
      throw const AppFailure('电脑已接收的录音属于另一提交，请在电脑核对。');
    }
    task = receipt;
  }

  Future<void> submitOrQuery() async {
    final original = draft;
    if (busy || original == null || received) return;
    final generation = _generation;
    busy = true;
    error = null;
    notifyListeners();
    try {
      if (queryRequired) {
        final receipt = await store.withSession(
          (session) => store.api.voiceSamplingTask(session, original.task.id),
        );
        if (!_current(generation)) return;
        _applyReceipt(receipt, original);
        if (receipt.status == 'awaiting_sample') {
          final cleared = original.withSubmission(
            queryRequired: false,
            audioSha256: original.audioSha256,
          );
          await repository.save(cleared);
          if (_current(generation)) draft = cleared;
        }
      } else {
        final file = File(original.audioPath);
        final digest = (await sha256.bind(file.openRead()).first).toString();
        final pending = original.withSubmission(
          queryRequired: true,
          audioSha256: digest,
        );
        // Persist the original request before the first network mutation.
        await repository.save(pending);
        if (!_current(generation)) return;
        draft = pending;
        final receipt = await store.withSession(
          (session) => store.api.submitVoiceSample(session, pending),
        );
        if (!_current(generation)) return;
        _applyReceipt(receipt, pending);
      }
    } on AppFailure catch (failure) {
      if (_current(generation)) error = failure.message;
    } on AuthenticationFailure catch (failure) {
      if (_current(generation)) error = failure.message;
    } on Exception {
      if (_current(generation)) error = '录音处理未完成，请保留原文件并查询原任务。';
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
    store.removeListener(_connectionChanged);
    super.dispose();
  }
}
