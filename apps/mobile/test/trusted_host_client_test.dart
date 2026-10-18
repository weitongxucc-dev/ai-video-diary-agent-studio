import 'dart:io';
import 'package:flutter_test/flutter_test.dart';
import 'package:yaohe_mobile/data/trusted_host_client_io.dart';

void main() {
  test(
    'native host trust validates CA, hostname and certificate expiry',
    () async {
      const fixture = 'test/fixtures/companion_tls';
      Future<HttpServer> serve(String certificate) async {
        final context = SecurityContext()
          ..useCertificateChain('$fixture/$certificate')
          ..usePrivateKey('$fixture/test-only-server.key');
        final server = await HttpServer.bindSecure(
          InternetAddress.loopbackIPv4,
          0,
          context,
        );
        server.listen((request) {
          request.response.write('verified');
          request.response.close();
        });
        return server;
      }

      final server = await serve('server.pem'),
          expired = await serve('expired.pem');
      addTearDown(() => server.close(force: true));
      addTearDown(() => expired.close(force: true));
      final client = trustedHostClient(
        await File('$fixture/ca.pem').readAsString(),
      );
      addTearDown(client.close);
      expect(
        (await client.get(Uri.parse('https://localhost:${server.port}'))).body,
        'verified',
      );
      await expectLater(
        client.get(Uri.parse('https://127.0.0.1:${server.port}')),
        throwsA(isA<Exception>()),
      );
      await expectLater(
        client.get(Uri.parse('https://localhost:${expired.port}')),
        throwsA(isA<Exception>()),
      );
      final wrong = trustedHostClient(
        await File('$fixture/other.pem').readAsString(),
      );
      addTearDown(wrong.close);
      await expectLater(
        wrong.get(Uri.parse('https://localhost:${server.port}')),
        throwsA(isA<Exception>()),
      );
    },
  );
}
