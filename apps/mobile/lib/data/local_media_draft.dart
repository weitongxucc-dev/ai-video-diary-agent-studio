import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

class DraftVideo {
  const DraftVideo({required this.name, required this.size, this.path});
  final String name;
  final int? size;
  final String? path;

  Map<String, Object?> toJson() => {'name': name, 'size': size, 'path': path};
}

class LocalMediaDraft {
  const LocalMediaDraft({
    this.files = const [],
    this.notes = '',
    this.sample,
    this.missingFiles = false,
  });

  final List<DraftVideo> files;
  final String notes;
  final String? sample;
  final bool missingFiles;
}

class LocalMediaDraftRepository {
  LocalMediaDraftRepository({
    FlutterSecureStorage? storage,
    bool? isWeb,
    Future<bool> Function(String)? fileExists,
  }) : _storage = storage ?? const FlutterSecureStorage(),
       _isWeb = isWeb ?? kIsWeb,
       _fileExists = fileExists ?? ((path) => File(path).exists());

  static const storageKey = 'yaohe_local_draft';
  final FlutterSecureStorage _storage;
  final bool _isWeb;
  final Future<bool> Function(String) _fileExists;

  Future<LocalMediaDraft> read() async {
    final value = await _storage.read(key: storageKey);
    if (value == null) return const LocalMediaDraft();
    final data = jsonDecode(value) as Map<String, dynamic>;
    final files = <DraftVideo>[];
    var missing = false;
    if (!_isWeb) {
      for (final item in data['files'] as List) {
        final path = item['path'] as String?;
        if (path != null && await _fileExists(path)) {
          files.add(
            DraftVideo(
              name: item['name'] as String,
              size: item['size'] as int?,
              path: path,
            ),
          );
        } else {
          missing = true;
        }
      }
    }
    return LocalMediaDraft(
      files: files,
      notes: data['notes'] as String,
      sample: _isWeb ? null : data['sample'] as String?,
      missingFiles: missing,
    );
  }

  Future<void> write(LocalMediaDraft draft) => _storage.write(
    key: storageKey,
    value: jsonEncode({
      'notes': draft.notes,
      'sample': _isWeb ? null : draft.sample,
      'files': _isWeb ? [] : draft.files.map((file) => file.toJson()).toList(),
    }),
  );
}
