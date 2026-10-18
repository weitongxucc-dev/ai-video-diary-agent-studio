import 'package:flutter/material.dart';
import '../data/voice_sampling_controller.dart';

class VoiceSampleSheet extends StatelessWidget {
  const VoiceSampleSheet({
    super.key,
    required this.recording,
    required this.hasSample,
    required this.busy,
    required this.playing,
    required this.seconds,
    required this.onRecord,
    required this.onPlay,
    required this.onClose,
    this.error,
    this.sampling,
  });
  final VoiceSamplingController? sampling;
  final bool recording, hasSample, busy, playing;
  final int seconds;
  final String? error;
  final VoidCallback onRecord, onPlay, onClose;

  @override
  Widget build(BuildContext context) {
    final duration =
        '${(seconds ~/ 60).toString().padLeft(2, '0')}:${(seconds % 60).toString().padLeft(2, '0')}';
    final state = sampling != null && !recording && !playing
        ? sampling!.status
        : recording
        ? '正在录音，结束后可试听'
        : playing
        ? '正在试听，点击下方可停止'
        : hasSample
        ? '样本保存在手机，尚未发送'
        : '结束录音并试听后，再确认是否保留';
    return SizedBox(
      height: (MediaQuery.sizeOf(context).height * .76).clamp(0, 640),
      child: Padding(
        padding: EdgeInsets.fromLTRB(
          22,
          10,
          22,
          16 + MediaQuery.paddingOf(context).bottom,
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
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
                    '我的声音样本',
                    style: TextStyle(fontSize: 20, fontWeight: FontWeight.w500),
                  ),
                ),
                IconButton(
                  tooltip: '关闭声音样本',
                  onPressed: onClose,
                  icon: const Icon(Icons.close, size: 20),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Expanded(
              child: SingleChildScrollView(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    const Text(
                      '找一个安静的地方，用日常说话的语气读一段。',
                      style: TextStyle(
                        fontSize: 13,
                        color: Color(0xff697183),
                        height: 1.7,
                      ),
                    ),
                    const SizedBox(height: 16),
                    if (sampling != null) ...[
                      Row(
                        children: [
                          Expanded(
                            child: Text(
                              sampling!.task?.displayName ?? '电脑采样任务',
                              style: const TextStyle(fontSize: 14),
                            ),
                          ),
                          IconButton(
                            tooltip: '读取电脑采样任务',
                            onPressed: recording || busy || sampling!.busy
                                ? null
                                : sampling!.load,
                            icon: const Icon(Icons.refresh),
                          ),
                        ],
                      ),
                      if (sampling!.tasks.isNotEmpty)
                        DropdownButtonFormField<String>(
                          initialValue: sampling!.task?.id,
                          key: ValueKey(sampling!.task?.id),
                          isExpanded: true,
                          items: sampling!.tasks
                              .map(
                                (task) => DropdownMenuItem(
                                  value: task.id,
                                  child: Text(
                                    task.displayName,
                                    overflow: TextOverflow.ellipsis,
                                  ),
                                ),
                              )
                              .toList(),
                          onChanged: recording || busy || sampling!.busy
                              ? null
                              : (id) {
                                  if (id != null) sampling!.select(id);
                                },
                        ),
                      if (sampling!.busy) const LinearProgressIndicator(),
                      const SizedBox(height: 12),
                    ],
                    Container(
                      padding: const EdgeInsets.all(16),
                      decoration: BoxDecoration(
                        color: const Color(0xfff2f4f9),
                        borderRadius: BorderRadius.circular(16),
                      ),
                      child: Text(
                        sampling?.task?.referenceText ??
                            '连接电脑并读取采样任务后，这里显示需要朗读的原文。',
                        style: TextStyle(fontSize: 14, height: 1.8),
                      ),
                    ),
                    const SizedBox(height: 22),
                    Center(
                      child: Container(
                        width: 70,
                        height: 70,
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          gradient: const LinearGradient(
                            begin: Alignment.topLeft,
                            end: Alignment.bottomRight,
                            colors: [Color(0xfffffefb), Color(0xffedf1f8)],
                          ),
                          border: Border.all(color: Colors.white),
                          boxShadow: const [
                            BoxShadow(
                              color: Color(0x22a0b1d2),
                              blurRadius: 18,
                              offset: Offset(0, 5),
                            ),
                          ],
                        ),
                        child: Icon(
                          recording
                              ? Icons.mic
                              : playing
                              ? Icons.volume_up_outlined
                              : Icons.mic_none,
                          size: 28,
                          color: const Color(0xff6e88bd),
                        ),
                      ),
                    ),
                    const SizedBox(height: 12),
                    SizedBox(
                      height: 46,
                      child: Center(
                        child: Text(
                          hasSample && seconds == 0 && !recording
                              ? '样本已保留'
                              : duration,
                          style: TextStyle(
                            fontSize: hasSample && seconds == 0 && !recording
                                ? 20
                                : 29,
                            fontWeight: FontWeight.w400,
                            color: const Color(0xff30333b),
                          ),
                        ),
                      ),
                    ),
                    Text(
                      state,
                      textAlign: TextAlign.center,
                      style: TextStyle(
                        fontSize: 12,
                        height: 1.7,
                        color: recording
                            ? const Color(0xffa33a3a)
                            : const Color(0xff697183),
                      ),
                    ),
                    if (error != null)
                      Padding(
                        padding: const EdgeInsets.only(top: 16),
                        child: Text(
                          error!,
                          style: const TextStyle(
                            fontSize: 13,
                            color: Color(0xffa33a3a),
                            height: 1.7,
                          ),
                        ),
                      ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 16),
            FilledButton.icon(
              onPressed:
                  busy ||
                      (sampling != null &&
                          !recording &&
                          (sampling!.busy ||
                              sampling!.task?.status != 'awaiting_sample' ||
                              sampling!.queryRequired))
                  ? null
                  : onRecord,
              style: FilledButton.styleFrom(
                minimumSize: const Size(double.infinity, 50),
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(16),
                ),
              ),
              icon: Icon(
                recording ? Icons.stop_rounded : Icons.mic_none,
                size: 20,
              ),
              label: Text(
                busy
                    ? '正在处理…'
                    : recording
                    ? '结束录音'
                    : hasSample
                    ? '重新录一段'
                    : '开始录音',
              ),
            ),
            if (sampling != null) ...[
              const SizedBox(height: 10),
              FilledButton.tonalIcon(
                onPressed:
                    !hasSample ||
                        recording ||
                        busy ||
                        sampling!.busy ||
                        sampling!.received
                    ? null
                    : sampling!.submitOrQuery,
                icon: Icon(
                  sampling!.queryRequired
                      ? Icons.refresh
                      : Icons.upload_outlined,
                ),
                label: Text(
                  sampling!.received
                      ? '电脑已接收'
                      : sampling!.queryRequired
                      ? '查询原发送结果'
                      : '发送此任务的录音',
                ),
              ),
            ],
            const SizedBox(height: 10),
            OutlinedButton.icon(
              onPressed: !hasSample || recording || busy ? null : onPlay,
              style: OutlinedButton.styleFrom(
                minimumSize: const Size(double.infinity, 50),
                side: const BorderSide(color: Color(0xffd7dfed)),
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(16),
                ),
              ),
              icon: Icon(
                playing ? Icons.stop_rounded : Icons.play_arrow_rounded,
                size: 21,
              ),
              label: Text(playing ? '停止试听' : '试听这段录音'),
            ),
          ],
        ),
      ),
    );
  }
}
