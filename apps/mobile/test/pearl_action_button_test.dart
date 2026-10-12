import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:yaohe_mobile/ui/pearl_action_button.dart';

void main() {
  testWidgets('Reduce Motion leaves the pearl button still and operable', (
    tester,
  ) async {
    var calls = 0;
    await tester.pumpWidget(
      MaterialApp(
        home: MediaQuery(
          data: const MediaQueryData(disableAnimations: true),
          child: Scaffold(
            body: PearlActionButton(
              label: '选择视频',
              animate: true,
              onPressed: () => calls++,
            ),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(tester.binding.transientCallbackCount, 0);
    await tester.tap(find.text('选择视频'));
    await tester.pumpAndSettle();
    expect(calls, 1);
  });
}
