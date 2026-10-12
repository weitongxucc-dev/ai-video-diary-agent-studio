import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:yaohe_mobile/ui/voice_sample_sheet.dart';

void main() {
  testWidgets(
    'voice controls stay full width and do not jump between record states',
    (tester) async {
      await tester.binding.setSurfaceSize(const Size(390, 844));
      addTearDown(() => tester.binding.setSurfaceSize(null));
      Widget frame({required bool recording, required bool hasSample}) =>
          MaterialApp(
            home: Scaffold(
              body: Align(
                alignment: Alignment.bottomCenter,
                child: VoiceSampleSheet(
                  recording: recording,
                  hasSample: hasSample,
                  busy: false,
                  playing: false,
                  seconds: recording ? 2 : 0,
                  onRecord: () {},
                  onPlay: () {},
                  onClose: () {},
                ),
              ),
            ),
          );
      await tester.pumpWidget(frame(recording: false, hasSample: false));
      final record = tester.getRect(find.widgetWithText(FilledButton, '开始录音'));
      final play = tester.getRect(
        find.widgetWithText(OutlinedButton, '试听这段录音'),
      );
      expect(record.width, play.width);
      expect(record.left, play.left);
      expect(record.width, 346);
      expect(
        tester.widget<OutlinedButton>(find.byType(OutlinedButton)).onPressed,
        isNull,
      );
      await tester.pumpWidget(frame(recording: true, hasSample: true));
      await tester.pump();
      expect(tester.getRect(find.widgetWithText(FilledButton, '结束录音')), record);
      expect(
        tester.getRect(find.widgetWithText(OutlinedButton, '试听这段录音')),
        play,
      );
      expect(find.text('00:02'), findsOneWidget);
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets('short display and large text keep voice actions reachable', (
    tester,
  ) async {
    await tester.binding.setSurfaceSize(const Size(360, 640));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    await tester.pumpWidget(
      MaterialApp(
        home: MediaQuery(
          data: const MediaQueryData(
            size: Size(360, 640),
            textScaler: TextScaler.linear(2),
          ),
          child: Scaffold(
            body: Align(
              alignment: Alignment.bottomCenter,
              child: VoiceSampleSheet(
                recording: false,
                hasSample: true,
                busy: false,
                playing: true,
                seconds: 0,
                onRecord: () {},
                onPlay: () {},
                onClose: () {},
              ),
            ),
          ),
        ),
      ),
    );
    await tester.pump();
    expect(find.text('样本已保留'), findsOneWidget);
    expect(find.text('停止试听'), findsOneWidget);
    expect(
      tester.getRect(find.byType(OutlinedButton)).bottom,
      lessThanOrEqualTo(640),
    );
    expect(tester.takeException(), isNull);
  });
}
