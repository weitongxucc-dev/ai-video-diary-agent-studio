import 'package:flutter/material.dart';
import '../data/mobile_store.dart';

class DebugConnectionSheet extends StatefulWidget {
  const DebugConnectionSheet({super.key, required this.store});
  final MobileStore store;
  @override
  State<DebugConnectionSheet> createState() => _DebugConnectionSheetState();
}

class _DebugConnectionSheetState extends State<DebugConnectionSheet> {
  late final address = TextEditingController(
    text: widget.store.session?.address ?? '',
  );
  final code = TextEditingController();
  late final name = TextEditingController(
    text: widget.store.session?.name ?? '',
  );
  @override
  void dispose() {
    address.dispose();
    code.dispose();
    name.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: widget.store,
    builder: (context, _) => Padding(
      padding: EdgeInsets.fromLTRB(
        24,
        24,
        24,
        24 +
            MediaQuery.viewInsetsOf(context).bottom +
            MediaQuery.paddingOf(context).bottom,
      ),
      child: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              '连接我的曜核',
              style: TextStyle(fontSize: 22, fontWeight: FontWeight.w500),
            ),
            const SizedBox(height: 8),
            const Text(
              '在电脑端生成邀请，手机连接同一网络。',
              style: TextStyle(color: Color(0xff697183)),
            ),
            const SizedBox(height: 24),
            TextField(
              controller: address,
              keyboardType: TextInputType.url,
              autocorrect: false,
              decoration: const InputDecoration(
                labelText: '主机连接地址',
                hintText: 'https://…',
              ),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: code,
              autocorrect: false,
              decoration: const InputDecoration(labelText: '邀请代码'),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: name,
              decoration: const InputDecoration(labelText: '你的称呼'),
            ),
            if (widget.store.error != null)
              Padding(
                padding: const EdgeInsets.only(top: 16),
                child: Text(
                  widget.store.error!,
                  style: const TextStyle(color: Color(0xffa33a3a)),
                ),
              ),
            const SizedBox(height: 24),
            SizedBox(
              width: double.infinity,
              child: FilledButton(
                onPressed: widget.store.loading
                    ? null
                    : () async {
                        await widget.store.join(
                          address.text,
                          code.text,
                          name.text,
                        );
                        if (context.mounted &&
                            widget.store.session != null &&
                            widget.store.error == null) {
                          Navigator.pop(context);
                        }
                      },
                child: Text(widget.store.loading ? '正在连接…' : '连接主机'),
              ),
            ),
          ],
        ),
      ),
    ),
  );
}
