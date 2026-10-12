import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:nsd/nsd.dart' as nsd;

/// Discovery results locate candidates; they never authorize a member or
/// change the destination of a stored credential.
class HostDiscovery extends ChangeNotifier {
  HostDiscovery({required this.serviceType});

  final String serviceType;
  nsd.Discovery? _discovery;
  Timer? _timeout;
  int _epoch = 0;
  bool _disposed = false;
  bool searching = false;
  String? error;
  List<nsd.Service> services = [];

  Future<void> start() async {
    if (searching || _disposed) return;
    await stop();
    if (_disposed) return;
    if (kIsWeb) {
      error = '请在手机 App 中查找电脑上的曜核。';
      notifyListeners();
      return;
    }
    final epoch = ++_epoch;
    searching = true;
    error = null;
    services = [];
    notifyListeners();
    try {
      final discovery = await nsd.startDiscovery(serviceType);
      if (_disposed || epoch != _epoch) {
        await nsd.stopDiscovery(discovery);
        return;
      }
      _discovery = discovery;
      discovery.addListener(_update);
      _update();
      _timeout = Timer(const Duration(seconds: 15), () => unawaited(stop()));
    } on nsd.NsdError catch (failure) {
      if (_disposed || epoch != _epoch) return;
      searching = false;
      error = failure.cause == nsd.ErrorCause.securityIssue
          ? '局域网访问未获允许。请在系统设置中开启权限，然后重新查找。'
          : '暂时无法查找曜核，请确认网络连接后重试。';
      notifyListeners();
    }
  }

  void _update() {
    if (_disposed || _discovery == null) return;
    services = List.unmodifiable(_discovery!.services);
    notifyListeners();
  }

  Future<void> stop() async {
    ++_epoch;
    _timeout?.cancel();
    final discovery = _discovery;
    _discovery = null;
    searching = false;
    if (discovery != null) {
      discovery.removeListener(_update);
      try {
        await nsd.stopDiscovery(discovery);
      } on nsd.NsdError {
        if (!_disposed) error = '局域网查找未能正常结束，请关闭并重新打开 App。';
      }
    }
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    unawaited(stop());
    super.dispose();
  }
}
