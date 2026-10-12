// Native-only diagnostic entry. It uses no product account, session or files.
import 'package:flutter/material.dart';
import 'package:nsd/nsd.dart' as nsd;
import 'package:yaohe_mobile/data/host_discovery.dart';
import 'package:yaohe_mobile/ui/invitation_scanner.dart';

void main() => runApp(
  MaterialApp(
    debugShowCheckedModeBanner: false,
    theme: ThemeData(
      useMaterial3: true,
      colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xff3864f6)),
    ),
    home: const NativeConnectionSmoke(),
  ),
);

class NativeConnectionSmoke extends StatefulWidget {
  const NativeConnectionSmoke({super.key});
  @override
  State<NativeConnectionSmoke> createState() => _NativeConnectionSmokeState();
}

class _NativeConnectionSmokeState extends State<NativeConnectionSmoke> {
  final discovery = HostDiscovery(serviceType: '_yaohe._tcp');
  nsd.Registration? registration;
  String result = '仅验收扫码和发现，不连接真实账号';
  @override
  void dispose() {
    discovery.dispose();
    if (registration != null) nsd.unregister(registration!);
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('原生连接能力验收')),
    body: Padding(
      padding: const EdgeInsets.all(24),
      child: ListenableBuilder(
        listenable: discovery,
        builder: (context, _) => Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(result),
            const SizedBox(height: 24),
            FilledButton(
              onPressed: () async {
                final raw = await Navigator.push<String>(
                  context,
                  MaterialPageRoute(builder: (_) => const InvitationScanner()),
                );
                if (mounted && raw != null) {
                  setState(
                    () => result = raw == 'yaohe-native-scanner-fixture-v1'
                        ? '测试二维码已正确识别'
                        : '已识别其他二维码，未发起连接',
                  );
                }
              },
              child: const Text('扫描测试二维码'),
            ),
            FilledButton(
              onPressed: discovery.searching
                  ? null
                  : () async {
                      registration ??= await nsd.register(
                        const nsd.Service(
                          name: '曜核原生发现测试',
                          type: '_yaohe._tcp',
                          port: 9443,
                        ),
                      );
                      await discovery.start();
                    },
              child: const Text('查找隔离测试服务'),
            ),
            Text(discovery.searching ? '正在查找' : '查找已结束'),
            if (discovery.error != null) Text(discovery.error!),
            for (final service in discovery.services)
              Text(service.name ?? '未命名的测试服务'),
          ],
        ),
      ),
    ),
  );
}
