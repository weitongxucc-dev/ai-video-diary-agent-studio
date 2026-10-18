/// Read-only projection of the host's member-owned voice-sampling task.
class VoiceSamplingTask {
  const VoiceSamplingTask({
    required this.id,
    required this.workspaceId,
    required this.ownerUserId,
    required this.requestId,
    required this.purpose,
    required this.displayName,
    required this.language,
    required this.referenceText,
    required this.textVersion,
    required this.status,
    this.submissionRequestId,
    this.audioSha256,
    this.voiceProfileId,
    this.voiceVersion,
    this.lastError,
  });

  final String id, workspaceId, ownerUserId, requestId, purpose;
  final String displayName, language, referenceText, textVersion, status;
  final String? submissionRequestId, audioSha256, voiceProfileId, voiceVersion;
  final String? lastError;

  factory VoiceSamplingTask.fromJson(Map<String, dynamic> value) {
    String required(String key) {
      final field = value[key];
      if (field is! String || field.trim().isEmpty) {
        throw FormatException('声音采样任务缺少 $key');
      }
      return field;
    }

    return VoiceSamplingTask(
      id: required('id'),
      workspaceId: required('workspace_id'),
      ownerUserId: required('owner_user_id'),
      requestId: required('request_id'),
      purpose: required('purpose'),
      displayName: required('display_name'),
      language: required('language'),
      referenceText: required('reference_text'),
      textVersion: required('text_version'),
      status: required('status'),
      submissionRequestId: value['submission_request_id'] as String?,
      audioSha256: value['audio_sha256'] as String?,
      voiceProfileId: value['voice_profile_id'] as String?,
      voiceVersion: value['voice_version'] as String?,
      lastError: value['last_error'] as String?,
    );
  }

  Map<String, dynamic> toJson() => {
    'id': id,
    'workspace_id': workspaceId,
    'owner_user_id': ownerUserId,
    'request_id': requestId,
    'purpose': purpose,
    'display_name': displayName,
    'language': language,
    'reference_text': referenceText,
    'text_version': textVersion,
    'status': status,
    'submission_request_id': submissionRequestId,
    'audio_sha256': audioSha256,
    'voice_profile_id': voiceProfileId,
    'voice_version': voiceVersion,
    'last_error': lastError,
  };
}
