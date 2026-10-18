import 'dart:async';
import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'auth_session.dart';
import 'connection_invitation.dart';
import 'host_api.dart';

class MobileStore extends ChangeNotifier {
  MobileStore({
    HostApi? api,
    CloudAuthApi? auth,
    FlutterSecureStorage? storage,
    DateTime Function()? now,
  }) : api = api ?? HostApi(),
       auth = auth ?? CloudAuthApi(),
       _storage = storage ?? const FlutterSecureStorage(),
       _now = now ?? DateTime.now;
  final HostApi api;
  final CloudAuthApi auth;
  final FlutterSecureStorage _storage;
  final DateTime Function() _now;
  HostSession? session;
  List<Map<String, dynamic>> tasks = [];
  DateTime? lastUpdated;
  bool connected = false, loading = false, ready = false;
  String? error;
  bool _foreground = false, _retryable = true, _disposed = false;
  int _connectionEpoch = 0, _refreshEpoch = -1;
  Timer? _connectionTimer;
  Future<void> _pendingStorageChange = Future.value();
  Future<HostSession>? _refreshing;
  bool _connectionIsCurrent(int epoch) =>
      !_disposed && epoch == _connectionEpoch;

  Future<void> _persistConnectionChange(Future<void> Function() change) async {
    final previous = _pendingStorageChange;
    final completion = Completer<void>();
    _pendingStorageChange = completion.future;
    try {
      await previous;
      await change();
    } finally {
      completion.complete();
    }
  }

  String _cacheKey(HostSession value) =>
      'tasks:v1:${[value.address, value.deviceId, value.workspaceId, value.memberId].map(Uri.encodeComponent).join(':')}';

  void setForeground(bool value, {bool refreshNow = true}) {
    _foreground = value;
    _connectionTimer?.cancel();
    if (value && ready && session != null && !_disposed && _retryable) {
      _connectionTimer = Timer.periodic(const Duration(seconds: 30), (_) {
        if (_retryable && !loading) unawaited(refresh());
      });
      if (refreshNow && _retryable) unawaited(refresh());
    }
  }

  Future<void> initialize() async {
    final epoch = _connectionEpoch;
    try {
      final saved = await _storage.read(key: 'yaohe_session');
      if (!_connectionIsCurrent(epoch)) return;
      if (saved != null) {
        session = HostSession.fromJson(
          jsonDecode(saved) as Map<String, dynamic>,
        );
      }
      if (session != null) {
        final cached = await _storage.read(key: _cacheKey(session!));
        if (!_connectionIsCurrent(epoch)) return;
        if (cached != null) {
          final value = jsonDecode(cached) as Map<String, dynamic>;
          tasks = List<Map<String, dynamic>>.from(value['tasks'] as List);
          lastUpdated = DateTime.parse(value['updated_at'] as String);
        }
      }
    } on FormatException catch (failure) {
      if (_connectionIsCurrent(epoch)) {
        session = null;
        error = failure.message;
      }
    } on TypeError {
      if (_connectionIsCurrent(epoch)) {
        session = null;
        error = '保存的连接格式不可读取，请重新扫描电脑邀请。';
      }
    } on Exception {
      if (_connectionIsCurrent(epoch)) error = '无法读取手机连接记录，请重新扫描电脑邀请。';
    } finally {
      if (!_disposed) {
        ready = true;
        notifyListeners();
        if (_foreground) setForeground(true, refreshNow: false);
      }
    }
    if (_connectionIsCurrent(epoch) && session != null) await refresh();
  }

  Future<void> connect(
    ConnectionInvitation invitation,
    String email,
    String password,
  ) async {
    if (_disposed || loading) return;
    final epoch = ++_connectionEpoch;
    loading = true;
    error = null;
    connected = false;
    notifyListeners();
    try {
      final credentials = await auth.signIn(invitation.auth, email, password);
      if (!_connectionIsCurrent(epoch)) return;
      final bound = await api.acceptInvitation(invitation, credentials);
      if (!_connectionIsCurrent(epoch)) return;
      await _persistConnectionChange(() async {
        if (_connectionIsCurrent(epoch)) {
          await _storage.write(
            key: 'yaohe_session',
            value: jsonEncode(bound.toJson()),
          );
        }
      });
      if (!_connectionIsCurrent(epoch)) return;
      session = bound;
      tasks = [];
      lastUpdated = null;
      _retryable = true;
    } on AppFailure catch (failure) {
      if (_connectionIsCurrent(epoch)) error = failure.message;
    } on AuthenticationFailure catch (failure) {
      if (_connectionIsCurrent(epoch)) error = failure.message;
    } on Exception {
      if (_connectionIsCurrent(epoch)) error = '未能保存可信连接，请检查手机存储后重新连接。';
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

  Future<HostSession> _authorizedSession() {
    final current = session;
    if (_disposed || current == null) throw const AppFailure('请先扫描电脑邀请并登录。');
    if (current.credentials.expiresAt.isAfter(
      _now().toUtc().add(const Duration(seconds: 30)),
    )) {
      return Future.value(current);
    }
    final epoch = _connectionEpoch;
    if (_refreshEpoch == epoch && _refreshing != null) return _refreshing!;
    late final Future<HostSession> operation;
    operation =
        (() async {
          final tokens = await auth.refresh(
            current.authConfiguration,
            current.credentials,
          );
          if (!_connectionIsCurrent(epoch)) throw const AppFailure('连接已取消。');
          final next = current.withCredentials(tokens);
          await _persistConnectionChange(() async {
            if (_connectionIsCurrent(epoch)) {
              await _storage.write(
                key: 'yaohe_session',
                value: jsonEncode(next.toJson()),
              );
            }
          });
          if (!_connectionIsCurrent(epoch)) throw const AppFailure('连接已取消。');
          session = next;
          notifyListeners();
          return next;
        })().whenComplete(() {
          if (identical(_refreshing, operation)) _refreshing = null;
        });
    _refreshEpoch = epoch;
    _refreshing = operation;
    return operation;
  }

  Future<T> withSession<T>(Future<T> Function(HostSession) action) async {
    final epoch = _connectionEpoch;
    final current = await _authorizedSession();
    await api.verifySession(current);
    if (!_connectionIsCurrent(epoch)) throw const AppFailure('连接已取消。');
    return action(current);
  }

  Future<void> refresh() async {
    if (_disposed || session == null || loading) return;
    final epoch = _connectionEpoch;
    loading = true;
    error = null;
    notifyListeners();
    try {
      final current = await _authorizedSession();
      final values = await api.tasks(
        current,
        connectionIsCurrent: () => _connectionIsCurrent(epoch),
      );
      if (!_connectionIsCurrent(epoch)) return;
      final observed = _now();
      await _persistConnectionChange(() async {
        if (_connectionIsCurrent(epoch)) {
          await _storage.write(
            key: _cacheKey(current),
            value: jsonEncode({
              'tasks': values,
              'updated_at': observed.toIso8601String(),
            }),
          );
        }
      });
      if (!_connectionIsCurrent(epoch)) return;
      tasks = values;
      lastUpdated = observed;
      connected = true;
      _retryable = true;
    } on AppFailure catch (failure) {
      if (_connectionIsCurrent(epoch)) {
        connected = false;
        _retryable = failure.retryable;
        error = failure.message;
      }
    } on AuthenticationFailure catch (failure) {
      if (_connectionIsCurrent(epoch)) {
        connected = false;
        _retryable = failure.retryable;
        error = failure.message;
      }
    } on Exception {
      if (_connectionIsCurrent(epoch)) {
        connected = false;
        _retryable = false;
        error = '无法保存这次进度，请检查手机存储后重试。';
      }
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
    auth.close();
    super.dispose();
  }
}
