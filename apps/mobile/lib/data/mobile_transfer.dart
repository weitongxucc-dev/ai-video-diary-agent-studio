import 'dart:convert';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'voice_sampling_draft.dart';

class TransferReceipt {
  TransferReceipt.fromJson(Map<String, dynamic> value)
    : requestId = value['request_id'] as String,
      workspaceId = value['workspace_id'] as String,
      memberId = value['member_id'] as String,
      state = value['state'] as String,
      receivedBytes = value['received_bytes'] as int,
      byteLength = value['byte_length'] as int,
      sha256 = value['sha256'] as String,
      filename = value['filename'] as String,
      mimeType = value['mime_type'] as String,
      description = value['description'] as String,
      restartFromZero = value['restart_from_zero'] as bool,
      assetId = value['asset_id'] as String?,
      assetRef = value['asset_ref'] as String?,
      error = value['error'] as Map<String, dynamic>?;
  final String requestId,
      workspaceId,
      memberId,
      state,
      sha256,
      filename,
      mimeType,
      description;
  final int receivedBytes, byteLength;
  final bool restartFromZero;
  final String? assetId, assetRef;
  final Map<String, dynamic>? error;
  Map<String, dynamic> toJson() => {
    'request_id': requestId,
    'workspace_id': workspaceId,
    'member_id': memberId,
    'state': state,
    'received_bytes': receivedBytes,
    'byte_length': byteLength,
    'sha256': sha256,
    'filename': filename,
    'mime_type': mimeType,
    'description': description,
    'restart_from_zero': restartFromZero,
    'asset_id': assetId,
    'asset_ref': assetRef,
    'error': error,
  };
}

class TransferDraft {
  const TransferDraft({
    required this.binding,
    required this.requestId,
    required this.path,
    required this.filename,
    required this.mimeType,
    required this.byteLength,
    required this.sha256,
    required this.description,
    this.receipt,
    this.observedAt,
  });
  final SamplingBinding binding;
  final String requestId, path, filename, mimeType, sha256, description;
  final int byteLength;
  final TransferReceipt? receipt;
  final DateTime? observedAt;
  TransferDraft withReceipt(TransferReceipt receipt) => TransferDraft(
    binding: binding,
    requestId: requestId,
    path: path,
    filename: filename,
    mimeType: mimeType,
    byteLength: byteLength,
    sha256: sha256,
    description: description,
    receipt: receipt,
    observedAt: DateTime.now().toUtc(),
  );
  Map<String, dynamic> get declaration => {
    'filename': filename,
    'mime_type': mimeType,
    'byte_length': byteLength,
    'sha256': sha256,
    'description': description,
  };
  Map<String, dynamic> toJson() => {
    'binding': binding.toJson(),
    'request_id': requestId,
    'path': path,
    ...declaration,
    'receipt': receipt?.toJson(),
    'observed_at': observedAt?.toIso8601String(),
  };
  factory TransferDraft.fromJson(Map<String, dynamic> value) => TransferDraft(
    binding: SamplingBinding.fromJson(value['binding'] as Map<String, dynamic>),
    requestId: value['request_id'] as String,
    path: value['path'] as String,
    filename: value['filename'] as String,
    mimeType: value['mime_type'] as String,
    byteLength: value['byte_length'] as int,
    sha256: value['sha256'] as String,
    description: value['description'] as String,
    receipt: value['receipt'] == null
        ? null
        : TransferReceipt.fromJson(value['receipt'] as Map<String, dynamic>),
    observedAt: value['observed_at'] == null
        ? null
        : DateTime.parse(value['observed_at'] as String),
  );
  bool belongsTo(SamplingBinding other) =>
      binding.deviceId == other.deviceId &&
      binding.workspaceId == other.workspaceId &&
      binding.memberId == other.memberId;
  void verify(TransferReceipt value) {
    if (value.requestId != requestId ||
        value.workspaceId != binding.workspaceId ||
        value.memberId != binding.memberId ||
        value.byteLength != byteLength ||
        value.sha256 != sha256 ||
        value.filename != filename ||
        value.mimeType != mimeType ||
        value.description != description ||
        ![
          'declared',
          'receiving',
          'received',
          'completed',
          'cancelled',
          'failed',
        ].contains(value.state) ||
        value.receivedBytes < 0 ||
        value.receivedBytes > byteLength ||
        (value.state == 'completed' && value.assetId == null)) {
      throw const FormatException('素材回执与原发送请求不一致，原文件保持保留。');
    }
  }
}

class TransferDraftRepository {
  TransferDraftRepository({FlutterSecureStorage? storage})
    : _storage = storage ?? const FlutterSecureStorage();
  final FlutterSecureStorage _storage;
  Future<void> _pending = Future.value();
  String _key(SamplingBinding binding) =>
      'yaohe_transfers.v1:${[binding.deviceId, binding.workspaceId, binding.memberId].map(Uri.encodeComponent).join(':')}';
  Future<List<TransferDraft>> read(SamplingBinding binding) async {
    await _pending;
    final raw = await _storage.read(key: _key(binding));
    if (raw == null) return [];
    final values = (jsonDecode(raw) as List)
        .map((value) => TransferDraft.fromJson(value as Map<String, dynamic>))
        .toList();
    if (values.any((value) => !value.belongsTo(binding))) {
      throw const FormatException('本地发送记录的成员或工作区不匹配。');
    }
    return values;
  }

  Future<void> save(SamplingBinding binding, List<TransferDraft> drafts) async {
    if (drafts.any((draft) => !draft.belongsTo(binding))) {
      throw const FormatException('不能保存其他账号的发送记录。');
    }
    final raw = jsonEncode(drafts.map((draft) => draft.toJson()).toList());
    final operation = _pending.then(
      (_) => _storage.write(key: _key(binding), value: raw),
    );
    _pending = operation.catchError((Object _) {});
    await operation;
  }
}
