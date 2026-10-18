import 'package:flutter/material.dart';
import '../data/transfer_controller.dart';

class TransferStatusList extends StatelessWidget {
  const TransferStatusList({super.key, required this.controller});
  final TransferController controller;
  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.stretch,
    children: [
      if (controller.restoring || controller.busy)
        const LinearProgressIndicator(),
      if (controller.error != null)
        Padding(
          padding: const EdgeInsets.symmetric(vertical: 10),
          child: Text(
            controller.error!,
            style: const TextStyle(color: Color(0xffa33a3a)),
          ),
        ),
      ...controller.drafts.map((draft) {
        final receipt = draft.receipt;
        final active = controller.activeId == draft.requestId;
        final label = switch (receipt?.state) {
          'completed' => '电脑已入库',
          'received' => '电脑已核验完整文件，待入库',
          'receiving' => '电脑接收未封存，重试从头发送',
          'declared' => '电脑已登记接收请求',
          'cancelled' => '已取消接收',
          'failed' =>
            receipt?.restartFromZero == true ? '接收失败，可沿原请求从头重发' : '接收失败',
          _ => '本地已保存，待查询或发送',
        };
        return Padding(
          padding: const EdgeInsets.symmetric(vertical: 10),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                draft.filename,
                style: const TextStyle(fontWeight: FontWeight.w500),
              ),
              const SizedBox(height: 5),
              Text(
                label,
                style: const TextStyle(fontSize: 12, color: Color(0xff697183)),
              ),
              if (receipt?.error?['message'] is String)
                Text(
                  receipt!.error!['message'] as String,
                  style: const TextStyle(
                    fontSize: 12,
                    color: Color(0xffa33a3a),
                  ),
                ),
              if (active && controller.sentBytes > 0)
                Text(
                  '本次已发送 ${controller.sentBytes} / ${draft.byteLength} 字节，等待电脑核验',
                  style: const TextStyle(fontSize: 12),
                ),
              if (draft.observedAt != null)
                Text(
                  '最近回执 ${draft.observedAt!.toLocal().toString().substring(0, 16)}',
                  style: const TextStyle(
                    fontSize: 11,
                    color: Color(0xff697183),
                  ),
                ),
              if (!['completed', 'cancelled'].contains(receipt?.state))
                Align(
                  alignment: Alignment.centerRight,
                  child: TextButton(
                    onPressed: controller.busy && !active
                        ? null
                        : () => controller.cancel(draft),
                    child: Text(controller.busy ? '停止传输' : '取消此请求'),
                  ),
                ),
            ],
          ),
        );
      }),
      if (controller.drafts.isNotEmpty)
        OutlinedButton(
          onPressed: controller.busy || controller.restoring
              ? null
              : controller.resumeAll,
          child: const Text('查询原请求并继续发送'),
        ),
    ],
  );
}
