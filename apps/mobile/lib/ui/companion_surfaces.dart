import 'package:flutter/material.dart';
import '../data/local_media_draft.dart';

const blue = Color(0xff3864f6);
const ink = Color(0xff222632);
const background = Color(0xfff7f8fc);

String formatMediaSize(int? bytes) => bytes == null
    ? '大小暂时无法读取'
    : bytes >= 1024 * 1024
    ? '${(bytes / (1024 * 1024)).toStringAsFixed(1)} MB'
    : '${(bytes / 1024).toStringAsFixed(1)} KB';
Widget companionPanel(List<Widget> children) => Container(
  padding: const EdgeInsets.all(20),
  decoration: BoxDecoration(
    color: Colors.white,
    borderRadius: BorderRadius.circular(24),
  ),
  child: Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: children,
  ),
);
Widget companionSectionTitle(String text) => Padding(
  padding: const EdgeInsets.only(bottom: 12),
  child: Text(
    text,
    style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w500),
  ),
);

class HostConnectionCard extends StatelessWidget {
  const HostConnectionCard({
    super.key,
    required this.hasConnection,
    required this.connected,
    required this.connectionName,
    required this.onConnect,
  });
  final bool hasConnection;
  final bool connected;
  final String? connectionName;
  final VoidCallback onConnect;
  @override
  Widget build(BuildContext context) => Material(
    color: Colors.white,
    borderRadius: BorderRadius.circular(24),
    clipBehavior: Clip.antiAlias,
    child: InkWell(
      onTap: onConnect,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 16),
        child: Row(
          children: [
            Image.asset(
              'assets/images/mini-host.png',
              width: 46,
              height: 60,
              cacheWidth: 184,
              filterQuality: FilterQuality.high,
              excludeFromSemantics: true,
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    !hasConnection ? '连接你的曜核' : '已保存的曜核连接',
                    style: const TextStyle(
                      fontSize: 16,
                      fontWeight: FontWeight.w500,
                    ),
                  ),
                  const SizedBox(height: 6),
                  Text(
                    connected
                        ? '已确认连接 · ${connectionName!}'
                        : !hasConnection
                        ? '在电脑上打开曜核，连接同一网络'
                        : '离线，连接记录已保留',
                    style: const TextStyle(
                      fontSize: 12,
                      color: Color(0xff697183),
                      height: 1.6,
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(width: 6),
            const Icon(Icons.chevron_right, size: 22, color: Color(0xff8f97a6)),
          ],
        ),
      ),
    ),
  );
}

class LocalVideoList extends StatelessWidget {
  const LocalVideoList({
    super.key,
    required this.files,
    required this.onReview,
    this.onRemove,
  });
  final List<DraftVideo> files;
  final VoidCallback onReview;
  final ValueChanged<int>? onRemove;
  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.symmetric(horizontal: 16),
    decoration: BoxDecoration(
      color: Colors.white,
      borderRadius: BorderRadius.circular(24),
    ),
    child: Column(
      children: [
        for (var index = 0; index < files.length; index++) ...[
          if (index > 0) const Divider(height: 1, color: Color(0xfff0f2f7)),
          ListTile(
            contentPadding: const EdgeInsets.symmetric(vertical: 5),
            onTap: onReview,
            leading: Container(
              width: 38,
              height: 42,
              decoration: BoxDecoration(
                color: blue.withValues(alpha: .055),
                borderRadius: BorderRadius.circular(12),
              ),
              child: const Icon(Icons.movie_outlined, color: blue, size: 21),
            ),
            title: Text(
              files[index].name,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(fontSize: 14, height: 1.5),
            ),
            subtitle: Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text(
                '${formatMediaSize(files[index].size)} · 待发送',
                style: const TextStyle(fontSize: 12, color: Color(0xff697183)),
              ),
            ),
            trailing: onRemove != null
                ? IconButton(
                    tooltip: '从清单移除',
                    onPressed: () => onRemove!(index),
                    icon: const Icon(
                      Icons.close,
                      size: 18,
                      color: Color(0xff788090),
                    ),
                  )
                : null,
          ),
        ],
      ],
    ),
  );
}
