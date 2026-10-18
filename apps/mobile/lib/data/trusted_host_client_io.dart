import 'dart:convert';
import 'dart:io';
import 'package:http/http.dart' as http;
import 'package:http/io_client.dart';

http.Client trustedHostClient(String caCertificatePem) {
  final context = SecurityContext(withTrustedRoots: false)
    ..setTrustedCertificatesBytes(utf8.encode(caCertificatePem));
  return IOClient(HttpClient(context: context));
}
