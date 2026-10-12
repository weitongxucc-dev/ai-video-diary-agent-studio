import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:yaohe_mobile/data/mobile_store.dart';
import 'package:yaohe_mobile/ui/companion_app.dart';

void main() {
  testWidgets(
    'unpaired user can navigate, gets no invented tasks or successful uploads',
    (tester) async {
      tester.platformDispatcher.accessibilityFeaturesTestValue =
          FakeAccessibilityFeatures(disableAnimations: true);
      addTearDown(
        tester.platformDispatcher.clearAccessibilityFeaturesTestValue,
      );
      final semantics = tester.ensureSemantics();
      FlutterSecureStorage.setMockInitialValues({});
      final store = MobileStore();
      await store.initialize();
      await tester.pumpWidget(CompanionApp(store: store));
      await tester.pumpAndSettle();
      expect(find.text('选择视频'), findsOneWidget);
      expect(find.text('还没有选择视频'), findsOneWidget);
      await tester.tap(find.bySemanticsLabel('进度'));
      await tester.pumpAndSettle();
      expect(find.text('手机里的素材'), findsOneWidget);
      expect(find.text('主机已收到'), findsNothing);
      await tester.tap(find.text('制作进度'));
      await tester.pumpAndSettle();
      expect(find.text('连接曜核后查看制作进度'), findsOneWidget);
      await tester.tap(find.bySemanticsLabel('我的'));
      await tester.pumpAndSettle();
      expect(find.text('我的声音样本'), findsOneWidget);
      await tester.tap(find.text('账号与个人资料'));
      await tester.pumpAndSettle();
      expect(find.text('账号与资料说明'), findsOneWidget);
      expect(find.text('登录'), findsNothing);
      await tester.tap(find.text('知道了'));
      await tester.pumpAndSettle();
      expect(find.text('已发送'), findsNothing);
      expect(find.text('演示用户'), findsNothing);
      expect(find.text('产品演示片'), findsNothing);
      semantics.dispose();
      store.dispose();
    },
  );
}
