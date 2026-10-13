import 'package:flutter/material.dart';
import '../data/local_media_draft.dart';
import 'companion_surfaces.dart';

class CompanionProgressPage extends StatefulWidget {
  const CompanionProgressPage({
    super.key,
    required this.files,
    required this.hasConnection,
    required this.connected,
    required this.loading,
    required this.error,
    required this.lastUpdated,
    required this.tasks,
    required this.onRefresh,
    required this.onConnect,
    required this.onPrepare,
    required this.onReview,
  });
  final List<DraftVideo> files;
  final bool hasConnection;
  final bool connected;
  final bool loading;
  final String? error;
  final DateTime? lastUpdated;
  final List<Map<String, dynamic>> tasks;
  final Future<void> Function() onRefresh;
  final VoidCallback onConnect;
  final VoidCallback onPrepare;
  final VoidCallback onReview;
  @override
  State<CompanionProgressPage> createState() => _CompanionProgressPageState();
}

class _CompanionProgressPageState extends State<CompanionProgressPage> {
  int progressTab = 0;
  @override
  Widget build(BuildContext context) => RefreshIndicator(
    onRefresh: widget.onRefresh,
    child: ListView(
      physics: const AlwaysScrollableScrollPhysics(),
      padding: const EdgeInsets.fromLTRB(22, 12, 22, 22),
      children: [
        SegmentedButton<int>(
          segments: const [
            ButtonSegment(value: 0, label: Text('素材传输')),
            ButtonSegment(value: 1, label: Text('制作进度')),
          ],
          selected: {progressTab},
          showSelectedIcon: false,
          style: SegmentedButton.styleFrom(
            backgroundColor: const Color(0xffe9ecf3),
            selectedBackgroundColor: Colors.white,
            selectedForegroundColor: ink,
            foregroundColor: const Color(0xff697183),
            side: BorderSide.none,
            minimumSize: const Size(48, 46),
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(14),
            ),
          ),
          onSelectionChanged: (selected) =>
              setState(() => progressTab = selected.single),
        ),
        const SizedBox(height: 26),
        if (progressTab == 0) ...[
          companionSectionTitle('手机里的素材'),
          if (widget.files.isEmpty)
            companionPanel([
              const Text('还没有准备素材'),
              const SizedBox(height: 8),
              const Text(
                '从首页选择视频，再一起填写说明。',
                style: TextStyle(
                  fontSize: 13,
                  color: Color(0xff697183),
                  height: 1.7,
                ),
              ),
            ])
          else
            LocalVideoList(files: widget.files, onReview: widget.onReview),
          const SizedBox(height: 16),
          const Text(
            '这些素材尚未发送。电脑版的手机接收服务接通后，才会显示真实发送进度和接收结果。',
            style: TextStyle(
              fontSize: 13,
              color: Color(0xff697183),
              height: 1.7,
            ),
          ),
          const SizedBox(height: 18),
          OutlinedButton(
            onPressed: widget.onPrepare,
            child: const Text('返回首页准备素材'),
          ),
        ] else ...[
          Row(
            children: [
              const Expanded(
                child: Text(
                  '可查看的制作进度',
                  style: TextStyle(fontSize: 17, fontWeight: FontWeight.w500),
                ),
              ),
              IconButton(
                onPressed: !widget.hasConnection || widget.loading
                    ? null
                    : widget.onRefresh,
                tooltip: '刷新进度',
                icon: const Icon(Icons.refresh, size: 21),
              ),
            ],
          ),
          const SizedBox(height: 12),
          if (!widget.hasConnection)
            companionPanel([
              const Icon(Icons.wifi, color: blue, size: 28),
              const SizedBox(height: 12),
              const Text('连接曜核后查看制作进度'),
              const SizedBox(height: 12),
              const Text(
                '主机核验身份后才会返回你有权查看的任务。',
                style: TextStyle(
                  fontSize: 13,
                  color: Color(0xff697183),
                  height: 1.7,
                ),
              ),
              const SizedBox(height: 16),
              FilledButton(
                onPressed: widget.onConnect,
                child: const Text('连接曜核'),
              ),
            ])
          else ...[
            if (widget.loading) const LinearProgressIndicator(),
            if (widget.error != null)
              Padding(
                padding: const EdgeInsets.only(bottom: 16),
                child: companionPanel([
                  Text(
                    widget.error!,
                    style: const TextStyle(
                      fontSize: 13,
                      color: Color(0xffa33a3a),
                      height: 1.7,
                    ),
                  ),
                  TextButton(
                    onPressed: widget.loading ? null : widget.onRefresh,
                    child: const Text('重新读取'),
                  ),
                ]),
              ),
            if (widget.lastUpdated != null)
              Padding(
                padding: const EdgeInsets.only(bottom: 16),
                child: Text(
                  '${widget.connected ? '最近更新' : '仅显示最后确认的记录'} ${widget.lastUpdated!.toLocal().toString().substring(0, 16)}',
                  style: const TextStyle(
                    fontSize: 12,
                    color: Color(0xff697183),
                  ),
                ),
              ),
            if (widget.tasks.isEmpty && widget.connected)
              companionPanel([
                const Text('还没有可查看的制作任务'),
                const SizedBox(height: 8),
                const Text(
                  '在电脑端开始创作后，这里会显示主机返回的进度。',
                  style: TextStyle(
                    fontSize: 13,
                    color: Color(0xff697183),
                    height: 1.7,
                  ),
                ),
              ]),
            ...widget.tasks.map(
              (task) => Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: Material(
                  color: Colors.white,
                  borderRadius: BorderRadius.circular(24),
                  clipBehavior: Clip.antiAlias,
                  child: InkWell(
                    onTap: () => _taskDetails(task),
                    child: Padding(
                      padding: const EdgeInsets.all(20),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Row(
                            children: [
                              const Icon(
                                Icons.movie_outlined,
                                size: 22,
                                color: blue,
                              ),
                              const Spacer(),
                              Text(
                                _status(task['status'] as String?),
                                style: const TextStyle(
                                  fontSize: 12,
                                  color: blue,
                                ),
                              ),
                            ],
                          ),
                          const SizedBox(height: 20),
                          Text(
                            task['name'] as String,
                            style: const TextStyle(
                              fontSize: 17,
                              fontWeight: FontWeight.w500,
                            ),
                          ),
                          const SizedBox(height: 10),
                          Text(
                            _stage(task['stage'] as String?),
                            style: const TextStyle(
                              fontSize: 13,
                              color: Color(0xff697183),
                            ),
                          ),
                          const SizedBox(height: 16),
                          const Row(
                            mainAxisAlignment: MainAxisAlignment.end,
                            children: [
                              Text(
                                '查看进度',
                                style: TextStyle(fontSize: 12, color: blue),
                              ),
                              Icon(Icons.chevron_right, size: 17, color: blue),
                            ],
                          ),
                        ],
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ],
        ],
      ],
    ),
  );

  Future<void> _taskDetails(
    Map<String, dynamic> task,
  ) => showModalBottomSheet<void>(
    context: context,
    useSafeArea: true,
    isScrollControlled: true,
    builder: (context) => Padding(
      padding: EdgeInsets.fromLTRB(
        22,
        22,
        22,
        22 + MediaQuery.paddingOf(context).bottom,
      ),
      child: SingleChildScrollView(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              task['name'] as String,
              style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w500),
            ),
            const SizedBox(height: 20),
            Text(
              _status(task['status'] as String?),
              style: const TextStyle(color: blue),
            ),
            const SizedBox(height: 12),
            Text(
              '当前阶段：${_stage(task['stage'] as String?)}',
              style: const TextStyle(fontSize: 14),
            ),
            if (task['stage_reason'] is String)
              Padding(
                padding: const EdgeInsets.only(top: 16),
                child: Text(
                  task['stage_reason'] as String,
                  style: const TextStyle(color: Color(0xff697183), height: 1.7),
                ),
              ),
            const SizedBox(height: 26),
            OutlinedButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('返回进度'),
            ),
          ],
        ),
      ),
    ),
  );

  String _status(String? status) => switch (status) {
    'created' => '已创建',
    'running' => '制作中',
    'waiting_for_user' => '待你处理',
    'completed' => '已完成',
    'failed' => '制作遇到问题',
    _ => '状态尚未确认',
  };
  String _stage(String? stage) => switch (stage) {
    'preparation' => '资料准备',
    'topic' => '选题',
    'script' => '文案',
    'narration' => '配音',
    'person' => '人物口型',
    'montage' => '剪辑',
    'subtitle' => '字幕与封面',
    'review' => '输出',
    'publish' => '发布',
    _ => '阶段尚未确认',
  };
}
