import 'dart:async';
import 'package:flutter/material.dart';
import '../data/connection_invitation.dart';
import '../data/host_discovery.dart';
import '../data/mobile_store.dart';
import 'invitation_scanner.dart';

class ConnectionSheet extends StatefulWidget {
  const ConnectionSheet({super.key, required this.store, this.discovery});
  final MobileStore store;
  final HostDiscovery? discovery;
  @override
  State<ConnectionSheet> createState() => _ConnectionSheetState();
}

class _ConnectionSheetState extends State<ConnectionSheet>
    with WidgetsBindingObserver {
  late final discovery =
      widget.discovery ?? HostDiscovery(serviceType: '_yaohe._tcp');
  late final updates = Listenable.merge([discovery, widget.store]);
  final account = TextEditingController(), password = TextEditingController();
  ConnectionInvitation? invitation;
  String? scanMessage;
  bool scanning = false;
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
    } else if (!scanning &&
        invitation == null &&
        widget.store.session == null) {
      unawaited(discovery.start());
    }
  }

  Future<void> scan() async {
    if (scanning || widget.store.loading) return;
    setState(() => scanning = true);
    await discovery.stop();
    if (!mounted) return;
    final content = await Navigator.push<String>(
      context,
      MaterialPageRoute(builder: (_) => const InvitationScanner()),
    );
    if (!mounted) return;
    ConnectionInvitation? next;
    String? message;
    if (content != null) {
      try {
        next = ConnectionInvitation.parse(content);
      } on FormatException catch (failure) {
        message = failure.message;
      } on TypeError {
        message = '邀请格式不完整，请在电脑重新生成手机邀请。';
      }
    }
    setState(() {
      scanning = false;
      if (content != null) {
        invitation = next;
        scanMessage = message;
        password.clear();
      }
    });
  }

  Future<void> connect() async {
    final selected = invitation;
    if (selected == null) return;
    await widget.store.connect(selected, account.text, password.text);
    password.clear();
    if (!mounted) return;
    final bound = widget.store.session;
    if (widget.store.error == null &&
        bound?.deviceId == selected.deviceId &&
        bound?.workspaceId == selected.workspaceId &&
        bound?.address == selected.endpoint.toString()) {
      Navigator.pop(context);
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    account.dispose();
    password.dispose();
    discovery.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: updates,
    builder: (context, _) => Padding(
      padding: EdgeInsets.fromLTRB(
        22,
        14,
        22,
        22 + MediaQuery.viewInsetsOf(context).bottom,
      ),
      child: SingleChildScrollView(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          mainAxisSize: MainAxisSize.min,
          children: [
            Row(
              children: [
                const Expanded(
                  child: Text(
                    '连接电脑上的曜核',
                    style: TextStyle(fontSize: 20, fontWeight: FontWeight.w500),
                  ),
                ),
                IconButton(
                  tooltip: '关闭连接',
                  onPressed: () => Navigator.pop(context),
                  icon: const Icon(Icons.close),
                ),
              ],
            ),
            const Text('请扫描自己电脑上生成的手机邀请。设备发现仅提供候选，扫描后仍需账号登录与主机核验。'),
            const SizedBox(height: 18),
            if (widget.store.session != null) ...[
              Text('已保存绑定：${widget.store.session!.name}'),
              Text(widget.store.connected ? '成员、设备与工作区已核验' : '尚未重新取得主机回执'),
              TextButton(
                onPressed: widget.store.loading ? null : widget.store.refresh,
                child: const Text('重新连接'),
              ),
            ],
            if (invitation == null) ...[
              if (discovery.searching)
                const Padding(
                  padding: EdgeInsets.symmetric(vertical: 16),
                  child: Row(
                    children: [
                      SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      ),
                      SizedBox(width: 12),
                      Text('正在查找电脑上的曜核'),
                    ],
                  ),
                ),
              for (final service in discovery.services)
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  leading: const Icon(Icons.computer),
                  title: Text(service.name ?? '未命名的曜核服务'),
                  subtitle: const Text('已发现，尚未验证或绑定'),
                  trailing: IconButton(
                    tooltip: '扫描邀请以确认设备',
                    onPressed: scan,
                    icon: const Icon(Icons.qr_code_scanner),
                  ),
                ),
              if (discovery.error != null) Text(discovery.error!),
              TextButton(
                onPressed: discovery.searching ? null : discovery.start,
                child: const Text('重新查找'),
              ),
            ] else ...[
              const SizedBox(height: 12),
              Text('电脑地址：${invitation!.endpoint}'),
              Text('设备：${invitation!.deviceId}'),
              Text('工作区：${invitation!.workspaceId}'),
              Text('账号登录到：${invitation!.auth.url.host}'),
              const SizedBox(height: 16),
              TextField(
                controller: account,
                keyboardType: TextInputType.text,
                enableSuggestions: false,
                textCapitalization: TextCapitalization.none,
                autocorrect: false,
                decoration: const InputDecoration(
                  labelText: '账号',
                  helperText: '与电脑版相同的账号',
                ),
                enabled: !widget.store.loading,
              ),
              const SizedBox(height: 12),
              TextField(
                controller: password,
                obscureText: true,
                autocorrect: false,
                enableSuggestions: false,
                decoration: const InputDecoration(labelText: '密码'),
                enabled: !widget.store.loading,
                onSubmitted: (_) => connect(),
              ),
              const SizedBox(height: 16),
              FilledButton(
                onPressed: widget.store.loading ? null : connect,
                child: Text(widget.store.loading ? '正在登录并核验' : '登录并连接此电脑'),
              ),
            ],
            if (scanMessage != null)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 12),
                child: Text(
                  scanMessage!,
                  style: const TextStyle(color: Colors.red),
                ),
              ),
            if (widget.store.error != null)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 12),
                child: Text(
                  widget.store.error!,
                  style: const TextStyle(color: Colors.red),
                ),
              ),
            const SizedBox(height: 12),
            OutlinedButton.icon(
              onPressed: scanning || widget.store.loading ? null : scan,
              icon: const Icon(Icons.qr_code_scanner),
              label: Text(invitation == null ? '扫描邀请二维码' : '重新扫描电脑邀请'),
            ),
          ],
        ),
      ),
    ),
  );
}
