import 'dart:async';
import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'host_api.dart';

class MobileStore extends ChangeNotifier {
  MobileStore({HostApi? api, FlutterSecureStorage? storage})
    : api = api ?? HostApi(allowLoopbackHttp: kDebugMode),
      _storage = storage ?? const FlutterSecureStorage();
  final HostApi api;
  final FlutterSecureStorage _storage;
  HostSession? session;
  List<Map<String, dynamic>> tasks = [];
  DateTime? lastUpdated;
  bool connected = false, loading = false, ready = false;
  String? error;
  bool _foreground = false, _retryable = true, _disposed = false;
  int _connectionEpoch = 0;
  Timer? _connectionTimer;
  Future<void> _pendingStorageChange = Future<void>.value();

  bool _connectionIsCurrent(int epoch) =>
      !_disposed && epoch == _connectionEpoch;

  // A disconnect must finish deleting an earlier credential before a new join
  // saves its credential. Task-cache writes follow that same connection order.
  Future<void> _persistConnectionChange(Future<void> Function() change) async {
    final preceding = _pendingStorageChange;
    final completion = Completer<void>();
    _pendingStorageChange = completion.future;
    try {
      await preceding;
      await change();
    } finally {
      // Release the next change even if this caller receives a storage error.
      completion.complete();
    }
  }

  void setForeground(bool foreground, {bool refreshNow = true}) {
    _foreground = foreground;
    _connectionTimer?.cancel();
    if (foreground && ready && session != null && !_disposed && _retryable) {
      _connectionTimer = Timer.periodic(const Duration(seconds: 30), (_) {
        if (_retryable && !loading) unawaited(refresh());
      });
      if (refreshNow && _retryable) unawaited(refresh());
    }
  }

  Future<void> initialize() async {
    final epoch = _connectionEpoch;
    try {
      final value = await _storage.read(key: 'yaohe_session');
      if (!_connectionIsCurrent(epoch)) return;
      if (value != null) {
        session = HostSession.fromJson(
          jsonDecode(value) as Map<String, dynamic>,
        );
      }
      if (session != null) {
        final cached = await _storage.read(key: _cacheKey);
        if (!_connectionIsCurrent(epoch)) return;
        if (cached != null) {
          final data = jsonDecode(cached) as Map<String, dynamic>;
          tasks = (data['tasks'] as List).cast<Map<String, dynamic>>();
          lastUpdated = DateTime.parse(data['updated_at'] as String);
        }
      }
    } on Exception {
      if (_connectionIsCurrent(epoch)) {
        error = '无法读取手机上的连接记录，请重新连接主机。';
      }
    } finally {
      if (!_disposed) {
        ready = true;
        notifyListeners();
        if (_foreground) setForeground(true, refreshNow: false);
      }
    }
    if (_connectionIsCurrent(epoch) && session != null) await refresh();
  }

  String get _cacheKey => 'tasks:${session!.address}:${session!.memberId}';
  Future<void> join(String address, String code, String name) async {
    if (_disposed || loading) return;
    final epoch = ++_connectionEpoch;
    loading = true;
    error = null;
    notifyListeners();
    try {
      final joined = await api.join(address, code, name);
      if (!_connectionIsCurrent(epoch)) return;
      await _persistConnectionChange(() async {
        if (!_connectionIsCurrent(epoch)) return;
        await _storage.write(
          key: 'yaohe_session',
          value: jsonEncode(joined.toJson()),
        );
      });
      if (!_connectionIsCurrent(epoch)) return;
      session = joined;
      _retryable = true;
      tasks = [];
      lastUpdated = null;
      connected = false;
    } on AppFailure catch (failure) {
      if (!_connectionIsCurrent(epoch)) return;
      error = failure.message;
    } on Exception {
      if (!_connectionIsCurrent(epoch)) return;
      error = '手机未能保存连接，请检查手机存储后重试。';
    } finally {
      if (_connectionIsCurrent(epoch)) {
        loading = false;
        notifyListeners();
      }
    }
    if (_connectionIsCurrent(epoch) && session != null && error == null) {
      await refresh();
      if (_foreground) setForeground(true, refreshNow: false);
    }
  }

  Future<void> refresh() async {
    if (_disposed || session == null || loading) return;
    final activeSession = session!;
    final epoch = _connectionEpoch;
    loading = true;
    error = null;
    notifyListeners();
    try {
      final result = await api.tasks(
        activeSession,
        connectionIsCurrent: () => _connectionIsCurrent(epoch),
      );
      if (!_connectionIsCurrent(epoch)) return;
      final now = DateTime.now();
      await _persistConnectionChange(() async {
        if (!_connectionIsCurrent(epoch)) return;
        await _storage.write(
          key: 'tasks:${activeSession.address}:${activeSession.memberId}',
          value: jsonEncode({
            'tasks': result,
            'updated_at': now.toIso8601String(),
          }),
        );
      });
      if (!_connectionIsCurrent(epoch)) return;
      tasks = result;
      lastUpdated = now;
      connected = true;
      _retryable = true;
    } on AppFailure catch (failure) {
      if (!_connectionIsCurrent(epoch)) return;
      connected = false;
      _retryable = failure.retryable;
      error = failure.message;
    } on Exception {
      if (!_connectionIsCurrent(epoch)) return;
      connected = false;
      _retryable = false;
      error = '无法保存这次进度，请检查手机存储后重试。';
    } finally {
      if (_connectionIsCurrent(epoch)) {
        loading = false;
        notifyListeners();
      }
    }
  }

  Future<void> disconnect() async {
    if (_disposed) return;
    ++_connectionEpoch;
    _connectionTimer?.cancel();
    session = null;
    tasks = [];
    lastUpdated = null;
    connected = false;
    loading = false;
    _retryable = true;
    error = null;
    notifyListeners();
    await _persistConnectionChange(() => _storage.delete(key: 'yaohe_session'));
  }

  @override
  void dispose() {
    _disposed = true;
    ++_connectionEpoch;
    _connectionTimer?.cancel();
    api.close();
    super.dispose();
  }
}
