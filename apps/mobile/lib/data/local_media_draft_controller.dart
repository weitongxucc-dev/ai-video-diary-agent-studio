import 'dart:async';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/foundation.dart';
import 'package:path_provider/path_provider.dart';

import 'local_media_draft.dart';

Future<List<DraftVideo>> selectLocalVideoCopies() async {
  final result = await FilePicker.pickFiles(type: FileType.video);
  if (result.isEmpty) return [];
  final selected = <DraftVideo>[];
  final folder = kIsWeb ? null : await getApplicationDocumentsDirectory();
  for (final file in result) {
    final size = await file.length();
    String? path;
    if (folder != null) {
      final extension = (file.extension ?? 'video').replaceAll(
        RegExp(r'[^a-zA-Z0-9]'),
        '',
      );
      path =
          '${folder.path}/video-${DateTime.now().microsecondsSinceEpoch}.$extension';
      await file.xFile.saveTo(path);
    }
    selected.add(DraftVideo(name: file.name, size: size, path: path));
  }
  return selected;
}

class LocalMediaDraftController extends ChangeNotifier {
  LocalMediaDraftController({
    LocalMediaDraftRepository? repository,
    Future<List<DraftVideo>> Function()? selectVideos,
  }) : _repository = repository ?? LocalMediaDraftRepository(),
       _selectVideos = selectVideos ?? selectLocalVideoCopies;

  final LocalMediaDraftRepository _repository;
  final Future<List<DraftVideo>> Function() _selectVideos;
  final _files = <DraftVideo>[];
  Future<void> _pendingSave = Future.value();
  bool _disposed = false;
  bool _ready = false, _choosing = false;
  String _notes = '';
  String? _sample, _error;

  bool get ready => _ready;
  bool get choosing => _choosing;
  String get notes => _notes;
  String? get sample => _sample;
  String? get error => _error;
  List<DraftVideo> get files => List.unmodifiable(_files);

  void updateNotes(String value) => _notes = value;

  Future<void> initialize() async {
    try {
      final restored = await _repository.read();
      if (_disposed) return;
      _files.addAll(restored.files);
      _notes = restored.notes;
      _sample = restored.sample;
      if (restored.missingFiles) {
        _error = '部分视频已不在手机缓存中，请重新选择。素材说明仍已保留。';
      }
    } on Exception {
      if (!_disposed) _error = '无法恢复手机上的素材清单，请重新选择。';
    } finally {
      if (!_disposed) {
        _ready = true;
        notifyListeners();
      }
    }
  }

  Future<void> save() async {
    if (_disposed) return;
    final snapshot = LocalMediaDraft(
      files: files,
      notes: _notes,
      sample: _sample,
    );
    final previous = _pendingSave;
    final completion = Completer<void>();
    _pendingSave = completion.future;
    await previous;
    try {
      await _repository.write(snapshot);
    } on Exception {
      if (!_disposed) {
        _error = '手机未能保存清单，请检查手机存储。当前选择仍保留在页面中。';
        notifyListeners();
      }
    } finally {
      completion.complete();
    }
  }

  Future<bool> choose() async {
    if (_choosing || _disposed) return false;
    _choosing = true;
    _error = null;
    notifyListeners();
    try {
      final selected = await _selectVideos();
      if (_disposed || selected.isEmpty) return false;
      _files.addAll(selected);
      notifyListeners();
      await save();
      return !_disposed;
    } on Exception {
      if (!_disposed) _error = '没有读取到视频，请重新选择文件或检查相册访问权限。';
      return false;
    } finally {
      if (!_disposed) {
        _choosing = false;
        notifyListeners();
      }
    }
  }

  Future<void> removeAt(int index) async {
    _files.removeAt(index);
    notifyListeners();
    await save();
  }

  Future<void> setSample(String path) async {
    if (_disposed) return;
    _sample = path;
    notifyListeners();
    await save();
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }
}
