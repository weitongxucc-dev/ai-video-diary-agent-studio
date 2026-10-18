import 'dart:convert';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'request_id.dart';
import 'voice_sampling_task.dart';

/// The authenticated binding that owned capture, never a caller-selected owner.
class SamplingBinding {
  const SamplingBinding({
    required this.deviceId,
    required this.workspaceId,
    required this.memberId,
  });
  final String deviceId, workspaceId, memberId;
  Map<String, String> toJson() => {
    'device_id': deviceId,
    'workspace_id': workspaceId,
    'member_id': memberId,
  };
  factory SamplingBinding.fromJson(Map<String, dynamic> value) =>
      SamplingBinding(
        deviceId: value['device_id'] as String,
        workspaceId: value['workspace_id'] as String,
        memberId: value['member_id'] as String,
      );
}

/// A local recording and its frozen prompt; it is not a host reception receipt.
class VoiceSamplingDraft {
  const VoiceSamplingDraft({
    required this.binding,
    required this.task,
    required this.audioPath,
    required this.submissionRequestId,
    this.queryRequired = false,
    this.audioSha256,
  });
  final SamplingBinding binding;
  final VoiceSamplingTask task;
  final String audioPath, submissionRequestId;
  final bool queryRequired;
  final String? audioSha256;
  VoiceSamplingDraft withSubmission({
    required bool queryRequired,
    required String? audioSha256,
  }) => VoiceSamplingDraft(
    binding: binding,
    task: task,
    audioPath: audioPath,
    submissionRequestId: submissionRequestId,
    queryRequired: queryRequired,
    audioSha256: audioSha256,
  );

  Map<String, dynamic> toJson() => {
    'format_version': 1,
    'binding': binding.toJson(),
    'task': task.toJson(),
    'audio_path': audioPath,
    'submission_request_id': submissionRequestId,
    'query_required': queryRequired,
    'audio_sha256': audioSha256,
  };

  factory VoiceSamplingDraft.fromJson(Map<String, dynamic> value) {
    if (value['format_version'] != 1) {
      throw const FormatException('声音草稿版本不受支持，原录音保持保留。');
    }
    return VoiceSamplingDraft(
      binding: SamplingBinding.fromJson(
        value['binding'] as Map<String, dynamic>,
      ),
      task: VoiceSamplingTask.fromJson(value['task'] as Map<String, dynamic>),
      audioPath: value['audio_path'] as String,
      submissionRequestId: value['submission_request_id'] as String,
      queryRequired: value['query_required'] as bool? ?? false,
      audioSha256: value['audio_sha256'] as String?,
    );
  }
}

class VoiceSamplingDraftRepository {
  VoiceSamplingDraftRepository({
    FlutterSecureStorage? storage,
    String Function()? requestId,
  }) : _storage = storage ?? const FlutterSecureStorage(),
       _requestId = requestId ?? newRequestId;
  final FlutterSecureStorage _storage;
  final String Function() _requestId;
  Future<void> _pending = Future.value();

  String _key(SamplingBinding binding, VoiceSamplingTask task) =>
      'yaohe_voice_sample.v1:${[binding.deviceId, binding.workspaceId, binding.memberId, task.id, task.textVersion].map(Uri.encodeComponent).join(':')}';

  Future<VoiceSamplingDraft?> read(
    SamplingBinding binding,
    VoiceSamplingTask currentTask,
  ) async {
    if (binding.workspaceId != currentTask.workspaceId ||
        binding.memberId != currentTask.ownerUserId) {
      throw const FormatException('声音采样任务不属于当前工作区。');
    }
    await _pending;
    final value = await _storage.read(key: _key(binding, currentTask));
    if (value == null) return null;
    final draft = VoiceSamplingDraft.fromJson(
      jsonDecode(value) as Map<String, dynamic>,
    );
    if (jsonEncode(draft.binding.toJson()) != jsonEncode(binding.toJson()) ||
        draft.task.id != currentTask.id ||
        draft.task.textVersion != currentTask.textVersion ||
        draft.task.referenceText != currentTask.referenceText ||
        draft.task.purpose != currentTask.purpose ||
        draft.task.ownerUserId != currentTask.ownerUserId) {
      throw const FormatException('声音草稿与当前采样任务或文本版本不一致，原录音未被改绑。');
    }
    return draft;
  }

  Future<VoiceSamplingDraft> saveRecording(
    SamplingBinding binding,
    VoiceSamplingTask task,
    String audioPath,
  ) async {
    if (binding.workspaceId != task.workspaceId ||
        binding.memberId != task.ownerUserId) {
      throw const FormatException('声音采样任务不属于当前工作区。');
    }
    if (audioPath.isEmpty) throw const FormatException('录音文件不可用。');
    final draft = VoiceSamplingDraft(
      binding: binding,
      task: task,
      audioPath: audioPath,
      submissionRequestId: _requestId(),
    );
    await save(draft);
    return draft;
  }

  Future<void> save(VoiceSamplingDraft draft) async {
    final previous = _pending;
    final operation = previous.then(
      (_) => _storage.write(
        key: _key(draft.binding, draft.task),
        value: jsonEncode(draft.toJson()),
      ),
    );
    // The caller receives the failure; it must not permanently block later saves.
    _pending = operation.catchError((Object _) {});
    await operation;
  }
}
