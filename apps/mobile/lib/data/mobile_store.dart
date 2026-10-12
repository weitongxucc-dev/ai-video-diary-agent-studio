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
    try {
      final value = await _storage.read(key: 'yaohe_session');
      if (value != null) {
        session = HostSession.fromJson(
          jsonDecode(value) as Map<String, dynamic>,
        );
      }
      if (session != null) {
        final cached = await _storage.read(key: _cacheKey);
        if (cached != null) {
          final data = jsonDecode(cached) as Map<String, dynamic>;
          tasks = (data['tasks'] as List).cast<Map<String, dynamic>>();
          lastUpdated = DateTime.parse(data['updated_at'] as String);
        }
      }
    } on Exception {
      error = '无法读取手机上的连接记录，请重新连接主机。';
    }
    if (_disposed) return;
    ready = true;
    notifyListeners();
    if (_foreground) setForeground(true, refreshNow: false);
    if (session != null) await refresh();
  }

  String get _cacheKey => 'tasks:${session!.address}:${session!.memberId}';
  Future<void> join(String address, String code, String name) async {
    if (loading) return;
    final epoch = ++_connectionEpoch;
    loading = true;
    error = null;
    notifyListeners();
    try {
      final joined = await api.join(address, code, name);
      if (_disposed || epoch != _connectionEpoch) return;
      await _storage.write(
        key: 'yaohe_session',
        value: jsonEncode(joined.toJson()),
      );
      if (_disposed || epoch != _connectionEpoch) return;
      session = joined;
      _retryable = true;
      tasks = [];
      lastUpdated = null;
      connected = false;
    } on AppFailure catch (failure) {
      if (_disposed || epoch != _connectionEpoch) return;
      error = failure.message;
    } on Exception {
      if (_disposed || epoch != _connectionEpoch) return;
      error = '手机未能保存连接，请检查手机存储后重试。';
    } finally {
      if (!_disposed && epoch == _connectionEpoch) {
        loading = false;
        notifyListeners();
      }
    }
    if (!_disposed &&
        epoch == _connectionEpoch &&
        session != null &&
        error == null) {
      await refresh();
      if (_foreground) setForeground(true, refreshNow: false);
    }
  }

  Future<void> refresh() async {
    if (session == null || loading) return;
    final activeSession = session!;
    final epoch = _connectionEpoch;
    loading = true;
    error = null;
    notifyListeners();
    try {
      final result = await api.tasks(
        activeSession,
        connectionIsCurrent: () => !_disposed && epoch == _connectionEpoch,
      );
      if (_disposed || epoch != _connectionEpoch) return;
      final now = DateTime.now();
      await _storage.write(
        key: 'tasks:${activeSession.address}:${activeSession.memberId}',
        value: jsonEncode({
          'tasks': result,
          'updated_at': now.toIso8601String(),
        }),
      );
      if (_disposed || epoch != _connectionEpoch) return;
      tasks = result;
      lastUpdated = now;
      connected = true;
      _retryable = true;
    } on AppFailure catch (failure) {
      if (_disposed || epoch != _connectionEpoch) return;
      connected = false;
      _retryable = failure.retryable;
      error = failure.message;
    } on Exception {
      if (_disposed || epoch != _connectionEpoch) return;
      connected = false;
      _retryable = false;
      error = '无法保存这次进度，请检查手机存储后重试。';
    } finally {
      if (!_disposed && epoch == _connectionEpoch) {
        loading = false;
        notifyListeners();
      }
    }
  }

  Future<void> disconnect() async {
    ++_connectionEpoch;
    _connectionTimer?.cancel();
    await _storage.delete(key: 'yaohe_session');
    session = null;
    tasks = [];
    lastUpdated = null;
    connected = false;
    loading = false;
    _retryable = true;
    error = null;
    notifyListeners();
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
