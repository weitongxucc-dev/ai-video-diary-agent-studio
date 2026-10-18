import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:yaohe_mobile/data/host_discovery.dart';
import 'package:yaohe_mobile/data/mobile_store.dart';
import 'package:yaohe_mobile/ui/connection_sheet.dart';

class PermissionDeniedDiscovery extends HostDiscovery {
  PermissionDeniedDiscovery() : super(serviceType: '_yaohe._tcp');
  @override
  Future<void> start() async {
    error = '局域网访问未获允许。请在系统设置中开启权限，然后重新查找。';
    notifyListeners();
  }

  @override
  Future<void> stop() async {}
}

void main() {
  testWidgets(
    'permission refusal remains actionable and never becomes a paired device',
    (tester) async {
      FlutterSecureStorage.setMockInitialValues({});
      final store = MobileStore();
      await store.initialize();
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: ConnectionSheet(
              store: store,
              discovery: PermissionDeniedDiscovery(),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.textContaining('局域网访问未获允许'), findsOneWidget);
      expect(find.text('扫描邀请二维码'), findsOneWidget);
      expect(find.text('重新查找'), findsOneWidget);
      expect(store.session, isNull);
      expect(store.connected, false);
      expect(find.text('已绑定'), findsNothing);
      expect(find.text('legacy debug input'), findsNothing);
      await tester.pumpWidget(const SizedBox.shrink());
      store.dispose();
    },
  );
}
