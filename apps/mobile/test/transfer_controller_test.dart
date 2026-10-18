import 'dart:convert';
import 'dart:io';
import 'package:crypto/crypto.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:yaohe_mobile/data/host_api.dart';
import 'package:yaohe_mobile/data/local_media_draft.dart';
import 'package:yaohe_mobile/data/mobile_store.dart';
import 'package:yaohe_mobile/data/transfer_controller.dart';
import 'fixtures.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  for (final lostAt in ['content', 'complete']) {
    test(
      'lost $lostAt reply queries the same request after restart and never repeats committed bytes',
      () async {
        FlutterSecureStorage.setMockInitialValues({});
        final directory = await Directory.systemTemp.createTemp(
          'yaohe-transfer-test-',
        );
        addTearDown(() => directory.delete(recursive: true));
        final file = await File(
          '${directory.path}/sample.mp4',
        ).writeAsBytes(List.generate(1024, (i) => i % 256));
        Map<String, dynamic>? declared;
        String? requestId;
        var state = 'declared', contentPuts = 0, completions = 0;
        final seenIds = <String>[];
        http.Client client() => MockClient((request) async {
          if (request.url.path.endsWith('/companion/session')) {
            return http.Response(jsonEncode(identityFixture()), 200);
          }
          final parts = request.url.pathSegments;
          final id = parts[2];
          seenIds.add(id);
          expect(request.headers['Authorization'], 'Bearer token');
          expect(request.headers['X-Workspace-Id'], 'workspace-a');
          if (request.method == 'GET' && declared == null) {
            return http.Response('', 404);
          }
          if (request.method == 'PUT' && parts.length == 3) {
            declared = jsonDecode(request.body) as Map<String, dynamic>;
            requestId = id;
          }
          if (parts.last == 'content') {
            contentPuts++;
            expect(
              sha256.convert(request.bodyBytes).toString(),
              declared!['sha256'],
            );
            state = 'received';
            if (lostAt == 'content' && contentPuts == 1) {
              return http.Response('', 503);
            }
          }
          if (parts.last == 'complete') {
            completions++;
            state = 'completed';
            if (lostAt == 'complete' && completions == 1) {
              return http.Response('', 503);
            }
          }
          return http.Response(
            jsonEncode({
              ...declared!,
              'request_id': requestId,
              'workspace_id': 'workspace-a',
              'member_id': 'member-a',
              'state': state,
              'received_bytes': ['received', 'completed'].contains(state)
                  ? 1024
                  : 0,
              'restart_from_zero': !['received', 'completed'].contains(state),
              'asset_id': state == 'completed' ? 'asset-1' : null,
              'asset_ref': null,
              'error': null,
            }),
            200,
            headers: {'content-type': 'application/json; charset=utf-8'},
          );
        });
        final firstStore = MobileStore(api: HostApi(client: client()))
          ..session = sessionFixture();
        final first = TransferController(firstStore);
        await Future<void>.delayed(Duration.zero);
        await first.prepareAndSend([
          DraftVideo(name: 'sample.mp4', size: 1024, path: file.path),
        ], '真实说明');
        expect(first.error, isNotNull);
        expect(contentPuts, 1);
        expect(requestId, isNotNull);
        first.dispose();
        firstStore.dispose();
        final nextStore = MobileStore(api: HostApi(client: client()))
          ..session = sessionFixture();
        final reopened = TransferController(nextStore);
        await Future<void>.delayed(Duration.zero);
        expect(reopened.drafts.single.requestId, requestId);
        await reopened.resumeAll();
        expect(reopened.error, isNull);
        expect(reopened.drafts.single.receipt!.state, 'completed');
        expect(contentPuts, 1);
        expect(completions, 1);
        expect(seenIds.toSet(), {requestId});
        expect(await file.exists(), true);
        reopened.dispose();
        nextStore.dispose();
      },
    );
  }
}
