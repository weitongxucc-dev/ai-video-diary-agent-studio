import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import '../data/host_discovery.dart';
import '../data/mobile_store.dart';
import 'invitation_scanner.dart';

class ConnectionSheet extends StatefulWidget {
  const ConnectionSheet({
    super.key,
    required this.store,
    required this.debugBuilder,
    this.discovery,
  });
  final MobileStore store;
  final WidgetBuilder debugBuilder;
  final HostDiscovery? discovery;
  @override
  State<ConnectionSheet> createState() => _ConnectionSheetState();
}

class _ConnectionSheetState extends State<ConnectionSheet>
    with WidgetsBindingObserver {
  late final discovery =
      widget.discovery ?? HostDiscovery(serviceType: '_yaohe._tcp');
  late final updates = Listenable.merge([discovery, widget.store]);
  bool scanning = false;
  String? scanMessage;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted && widget.store.session == null) unawaited(discovery.start());
    });
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state != AppLifecycleState.resumed) {
      unawaited(discovery.stop());
    } else if (!scanning && widget.store.session == null) {
      unawaited(discovery.start());
    }
  }

  Future<void> scan() async {
    if (scanning) return;
    setState(() => scanning = true);
    await discovery.stop();
    if (!mounted) return;
    final content = await Navigator.push<String>(
      context,
      MaterialPageRoute(builder: (_) => const InvitationScanner()),
    );
    if (!mounted) return;
    setState(() {
      scanning = false;
      if (content != null) {
        // The desktop has not frozen an invitation envelope or authentication
        // bootstrap yet. Never guess a URL/token, display the raw credential,
        // or send a stored member credential to an advertised endpoint.
        scanMessage = '二维码已读取，当前版本还不能完成配对。电脑版的手机配对服务接通后再试。';
      }
    });
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    discovery.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: updates,
    builder: (context, _) => Padding(
      padding: EdgeInsets.fromLTRB(
        22,
        8,
        22,
        22 +
            MediaQuery.paddingOf(context).bottom +
            MediaQuery.viewInsetsOf(context).bottom,
      ),
      child: SingleChildScrollView(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          mainAxisSize: MainAxisSize.min,
          children: [
            Center(
              child: Container(
                width: 36,
                height: 4,
                decoration: BoxDecoration(
                  color: const Color(0xffdce2ed),
                  borderRadius: BorderRadius.circular(4),
                ),
              ),
            ),
            const SizedBox(height: 12),
            Row(
              children: [
                const Expanded(
                  child: Text(
                    '连接我的曜核',
                    style: TextStyle(fontSize: 20, fontWeight: FontWeight.w500),
                  ),
                ),
                IconButton(
                  tooltip: '关闭连接界面',
                  onPressed: () => Navigator.pop(context),
                  icon: const Icon(Icons.close, size: 20),
                ),
              ],
            ),
            const SizedBox(height: 8),
            const Text(
              '在电脑上打开曜核，并让手机连接同一网络。首次连接需要扫描主账号的邀请。',
              style: TextStyle(color: Color(0xff697183), height: 1.7),
            ),
            const SizedBox(height: 22),
            if (widget.store.session != null) ...[
              _deviceTile(
                title: '已保存的曜核连接',
                subtitle: widget.store.connected
                    ? '已核验使用者：${widget.store.session!.name}'
                    : '连接记录已保留，等待重新核验',
                trailing: widget.store.loading
                    ? const SizedBox(
                        width: 20,
                        height: 20,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(
                        Icons.wifi,
                        size: 20,
                        color: Color(0xff697183),
                      ),
              ),
              const SizedBox(height: 12),
              OutlinedButton.icon(
                onPressed: widget.store.loading ? null : widget.store.refresh,
                icon: const Icon(Icons.refresh, size: 19),
                label: const Text('重新连接'),
              ),
              if (widget.store.error != null)
                Padding(
                  padding: const EdgeInsets.only(top: 12),
                  child: Text(
                    widget.store.error!,
                    style: const TextStyle(
                      color: Color(0xffa33a3a),
                      height: 1.7,
                    ),
                  ),
                ),
            ] else ...[
              if (discovery.searching)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 20),
                  child: Row(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      const SizedBox(
                        height: 18,
                        width: 18,
                        child: CircularProgressIndicator(strokeWidth: 1.7),
                      ),
                      const SizedBox(width: 12),
                      Text(
                        discovery.services.isEmpty
                            ? '正在查找同一网络里的曜核…'
                            : '找到 ${discovery.services.length} 个服务，仍在查找',
                        style: const TextStyle(
                          fontSize: 13,
                          color: Color(0xff697183),
                        ),
                      ),
                    ],
                  ),
                ),
              for (final service in discovery.services)
                Padding(
                  padding: const EdgeInsets.only(bottom: 10),
                  child: _deviceTile(
                    title: service.name ?? '未命名的曜核服务',
                    subtitle: '已发现，尚未验证或绑定',
                    trailing: IconButton(
                      tooltip: '扫描邀请以确认设备',
                      onPressed: scan,
                      icon: const Icon(
                        Icons.qr_code_scanner,
                        color: Color(0xff3864f6),
                      ),
                    ),
                  ),
                ),
              if (discovery.error != null)
                _information(discovery.error!)
              else if (!discovery.searching && discovery.services.isEmpty)
                _information('暂未找到曜核。请确认电脑版已打开、手机与电脑连接同一网络，再重新查找。'),
              const SizedBox(height: 8),
              TextButton.icon(
                onPressed: discovery.searching ? null : discovery.start,
                icon: const Icon(Icons.refresh, size: 18),
                label: const Text('重新查找'),
              ),
            ],
            const SizedBox(height: 12),
            FilledButton.icon(
              onPressed: scanning ? null : scan,
              icon: const Icon(Icons.qr_code_scanner, size: 21),
              label: Text(widget.store.session == null ? '扫描邀请二维码' : '扫描新的邀请'),
            ),
            if (scanMessage != null)
              Padding(
                padding: const EdgeInsets.only(top: 16),
                child: _information(scanMessage!),
              ),
            const SizedBox(height: 16),
            _information('手机配对服务仍在电脑版开发中。当前可真实查找和识码，尚不能确认绑定；选择的视频和录音仍保留在手机。'),
            if (kDebugMode)
              ExpansionTile(
                tilePadding: EdgeInsets.zero,
                title: const Text(
                  '开发调试连接',
                  style: TextStyle(fontSize: 13, color: Color(0xff697183)),
                ),
                children: [widget.debugBuilder(context)],
              ),
          ],
        ),
      ),
    ),
  );

  Widget _information(String message) => Container(
    padding: const EdgeInsets.all(15),
    decoration: BoxDecoration(
      color: const Color(0xfff0f3fa),
      borderRadius: BorderRadius.circular(16),
    ),
    child: Text(
      message,
      style: const TextStyle(
        fontSize: 13,
        color: Color(0xff697183),
        height: 1.7,
      ),
    ),
  );

  Widget _deviceTile({
    required String title,
    required String subtitle,
    required Widget trailing,
  }) => Container(
    padding: const EdgeInsets.all(15),
    decoration: BoxDecoration(
      color: Colors.white,
      borderRadius: BorderRadius.circular(20),
    ),
    child: Row(
      children: [
        Image.asset(
          'assets/images/mini-host.png',
          width: 42,
          height: 56,
          excludeFromSemantics: true,
        ),
        const SizedBox(width: 12),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                title,
                style: const TextStyle(
                  fontSize: 15,
                  fontWeight: FontWeight.w500,
                ),
              ),
              const SizedBox(height: 5),
              Text(
                subtitle,
                style: const TextStyle(
                  fontSize: 12,
                  color: Color(0xff697183),
                  height: 1.6,
                ),
              ),
            ],
          ),
        ),
        trailing,
      ],
    ),
  );
}
