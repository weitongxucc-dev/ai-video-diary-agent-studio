import 'package:flutter/material.dart';
import '../data/local_media_draft.dart';
import '../data/transfer_controller.dart';
import 'transfer_status_list.dart';
import 'companion_surfaces.dart';

class MaterialReviewSheet extends StatelessWidget {
  const MaterialReviewSheet({
    super.key,
    required this.files,
    required this.notes,
    required this.hasConnection,
    required this.onNotesChanged,
    this.transfers,
  });
  final TransferController? transfers;
  final List<DraftVideo> files;
  final TextEditingController notes;
  final bool hasConnection;
  final ValueChanged<String> onNotesChanged;
  @override
  Widget build(BuildContext context) => Padding(
    padding: EdgeInsets.fromLTRB(
      22,
      12,
      22,
      22 +
          MediaQuery.viewInsetsOf(context).bottom +
          MediaQuery.paddingOf(context).bottom,
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
                  '确认素材与说明',
                  style: TextStyle(fontSize: 20, fontWeight: FontWeight.w500),
                ),
              ),
              IconButton(
                tooltip: '关闭素材说明',
                onPressed: () => Navigator.pop(context),
                icon: const Icon(Icons.close, size: 20),
              ),
            ],
          ),
          const SizedBox(height: 12),
          Container(
            padding: const EdgeInsets.all(15),
            decoration: BoxDecoration(
              color: const Color(0xfff0f3fa),
              borderRadius: BorderRadius.circular(17),
            ),
            child: Row(
              children: [
                Image.asset(
                  'assets/images/mini-host.png',
                  width: 34,
                  height: 46,
                  excludeFromSemantics: true,
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Text(
                    !hasConnection ? '素材保存在手机，连接后再交给曜核' : '发送后由电脑核验完整文件并登记素材',
                    style: const TextStyle(
                      fontSize: 13,
                      color: Color(0xff697183),
                      height: 1.6,
                    ),
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 16),
          ...files.map(
            (file) => Padding(
              padding: const EdgeInsets.symmetric(vertical: 9),
              child: Row(
                children: [
                  const Icon(
                    Icons.movie_outlined,
                    size: 19,
                    color: Color(0xff6c86bd),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text(
                      file.name,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontSize: 13),
                    ),
                  ),
                  const SizedBox(width: 10),
                  Text(
                    formatMediaSize(file.size),
                    style: const TextStyle(
                      fontSize: 12,
                      color: Color(0xff697183),
                    ),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 18),
          const Text('素材说明', style: TextStyle(fontSize: 14)),
          const SizedBox(height: 10),
          TextField(
            controller: notes,
            enabled: !(transfers?.busy ?? false),
            onChanged: onNotesChanged,
            maxLines: 3,
            decoration: const InputDecoration(hintText: '想保留哪些重点？'),
          ),
          const SizedBox(height: 16),
          const Text(
            '视频和说明会按当前连接发送给电脑。接收成功后进入素材库，原手机文件继续保留。',
            style: TextStyle(
              fontSize: 13,
              color: Color(0xff697183),
              height: 1.7,
            ),
          ),
          if (transfers != null) ...[
            const SizedBox(height: 16),
            TransferStatusList(controller: transfers!),
            const SizedBox(height: 12),
            FilledButton.icon(
              onPressed:
                  !hasConnection ||
                      files.isEmpty ||
                      transfers!.busy ||
                      transfers!.restoring
                  ? null
                  : () => transfers!.prepareAndSend(files, notes.text),
              icon: const Icon(Icons.upload_outlined),
              label: const Text('发送这些素材与说明'),
            ),
          ],
          const SizedBox(height: 20),
          OutlinedButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('保存素材与说明'),
          ),
        ],
      ),
    ),
  );
}
