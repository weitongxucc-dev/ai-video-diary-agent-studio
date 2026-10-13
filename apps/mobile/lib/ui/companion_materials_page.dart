import 'package:flutter/material.dart';
import '../data/local_media_draft.dart';
import 'companion_surfaces.dart';
import 'pearl_action_button.dart';

class CompanionMaterialsPage extends StatelessWidget {
  const CompanionMaterialsPage({
    super.key,
    required this.connectionCard,
    required this.files,
    required this.choosing,
    required this.active,
    required this.localError,
    required this.onChoose,
    required this.onReview,
    required this.onRemove,
  });
  final Widget connectionCard;
  final List<DraftVideo> files;
  final bool choosing;
  final bool active;
  final String? localError;
  final VoidCallback onChoose;
  final VoidCallback onReview;
  final ValueChanged<int> onRemove;
  @override
  Widget build(BuildContext context) => ListView(
    padding: const EdgeInsets.fromLTRB(22, 5, 22, 14),
    children: [
      connectionCard,
      const SizedBox(height: 10),
      Center(
        child: PearlActionButton(
          label: choosing ? '正在选择…' : '选择视频',
          onPressed: choosing ? null : onChoose,
          animate: active,
        ),
      ),
      const SizedBox(height: 2),
      const Center(
        child: Text(
          '把拍好的素材整理好，交给曜核',
          style: TextStyle(fontSize: 13, color: Color(0xff697183)),
        ),
      ),
      const SizedBox(height: 27),
      Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          const Text(
            '待发送素材',
            style: TextStyle(fontSize: 17, fontWeight: FontWeight.w500),
          ),
          Text(
            '${files.length} 段',
            style: const TextStyle(fontSize: 12, color: Color(0xff697183)),
          ),
        ],
      ),
      const SizedBox(height: 14),
      if (files.isEmpty)
        companionPanel([
          const Icon(Icons.video_library_outlined, color: blue, size: 28),
          const SizedBox(height: 12),
          const Text('还没有选择视频'),
          const SizedBox(height: 8),
          const Text(
            '选择手机里的素材，说明想要保留的重点。',
            style: TextStyle(
              fontSize: 13,
              color: Color(0xff697183),
              height: 1.7,
            ),
          ),
        ])
      else
        LocalVideoList(files: files, onReview: onReview, onRemove: onRemove),
      if (files.isNotEmpty)
        Padding(
          padding: const EdgeInsets.only(top: 8),
          child: Align(
            alignment: Alignment.centerLeft,
            child: TextButton(onPressed: onReview, child: const Text('填写素材说明')),
          ),
        ),
      if (localError != null)
        Padding(
          padding: const EdgeInsets.only(top: 12),
          child: Text(
            localError!,
            style: const TextStyle(
              fontSize: 13,
              color: Color(0xffa33a3a),
              height: 1.7,
            ),
          ),
        ),
    ],
  );
}
