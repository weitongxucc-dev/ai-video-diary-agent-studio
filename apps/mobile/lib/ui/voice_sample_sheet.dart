import 'package:flutter/material.dart';

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
  });
  final bool recording, hasSample, busy, playing;
  final int seconds;
  final String? error;
  final VoidCallback onRecord, onPlay, onClose;

  @override
  Widget build(BuildContext context) {
    final duration =
        '${(seconds ~/ 60).toString().padLeft(2, '0')}:${(seconds % 60).toString().padLeft(2, '0')}';
    final state = recording
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
                    Container(
                      padding: const EdgeInsets.all(16),
                      decoration: BoxDecoration(
                        color: const Color(0xfff2f4f9),
                        borderRadius: BorderRadius.circular(16),
                      ),
                      child: const Text(
                        '你好，这是我的声音。今天想和你分享一件有意思的事。我希望视频里的讲解，能像平时的我一样自然。',
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
              onPressed: busy ? null : onRecord,
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
