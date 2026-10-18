import 'package:http/http.dart' as http;

http.Client trustedHostClient(String caCertificatePem) =>
    throw UnsupportedError('主机专属证书连接需要原生手机 App。');
