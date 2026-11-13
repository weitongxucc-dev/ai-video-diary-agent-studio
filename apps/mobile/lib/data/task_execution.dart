enum TaskRuntimeStatus { running, completed, failed, unknown }

/// A read-only projection of the host's persisted execution state.
///
/// The host may add association fields at different nesting levels as the
/// execution contract evolves. The mobile client keeps the identifiers when
/// present, but never invents them when an older host does not return them.
class TaskExecutionSnapshot {
  const TaskExecutionSnapshot({
    required this.taskId,
    required this.status,
    this.workspaceId,
    this.rawStatus,
    this.jobId,
    this.stage,
    this.progress,
    this.attempt,
    this.reason,
    this.impact,
    this.repairAction,
    this.requestId,
    this.assistantTurnId,
    this.timelineVersionId,
    this.timelineSequence,
    this.outputReady,
  });

  final String taskId;
  final String? workspaceId;
  final TaskRuntimeStatus status;
  final String? rawStatus;
  final String? jobId;
  final String? stage;
  final int? progress;
  final int? attempt;
  final String? reason;
  final String? impact;
  final String? repairAction;
  final String? requestId;
  final String? assistantTurnId;
  final String? timelineVersionId;
  final int? timelineSequence;
  final bool? outputReady;

  factory TaskExecutionSnapshot.unknown(
    String taskId, {
    String? workspaceId,
    String? reason,
  }) => TaskExecutionSnapshot(
    taskId: taskId,
    workspaceId: workspaceId,
    status: TaskRuntimeStatus.unknown,
    reason: reason,
  );

  factory TaskExecutionSnapshot.fromExecution(
    Map<String, dynamic> data, {
    required String expectedTaskId,
    required String expectedWorkspaceId,
  }) {
    final taskId = _string(data['task_id']);
    if (taskId != expectedTaskId) {
      throw const FormatException('执行回执与当前任务不一致。');
    }
    _checkWorkspace(data, expectedWorkspaceId);
    final job = _map(data['job']);
    final taskExecution = _map(data['task_execution']);
    final source = _association(data);
    final jobSource = _association(job);
    _checkTaskAndWorkspace(
      job,
      source,
      jobSource,
      expectedTaskId,
      expectedWorkspaceId,
    );
    final status = _string(job['status']) ?? _string(data['status']);
    final stage = _string(data['current_stage']) ?? _string(data['stage']);
    return TaskExecutionSnapshot(
      taskId: expectedTaskId,
      workspaceId:
          _string(data['workspace_id']) ??
          _string(job['workspace_id']) ??
          expectedWorkspaceId,
      status: _status(status),
      rawStatus: status,
      jobId: _string(job['id']) ?? _string(data['job_id']),
      stage: stage,
      progress: _int(job['progress']) ?? _int(data['progress']),
      attempt: _int(job['attempt']),
      reason:
          _string(job['failure_reason']) ??
          _string(data['waiting_reason']) ??
          _string(data['reason']),
      impact: _string(job['impact']) ?? _string(data['impact']),
      repairAction:
          _string(job['repair_action']) ?? _string(data['repair_action']),
      requestId: _associationValue(source, jobSource, data, job, 'request_id'),
      assistantTurnId: _associationValue(
        source,
        jobSource,
        data,
        job,
        'assistant_turn_id',
      ),
      timelineVersionId:
          _string(data['timeline_version_id']) ??
          _string(data['current_timeline_version_id']) ??
          _string(taskExecution['timeline_version_id']),
      timelineSequence:
          _int(data['timeline_sequence']) ??
          _int(taskExecution['timeline_sequence']),
      outputReady: _bool(data['output_ready']),
    );
  }

  factory TaskExecutionSnapshot.fromJobProgress(
    Map<String, dynamic> data, {
    required String expectedTaskId,
    required String expectedWorkspaceId,
  }) {
    final taskId = _string(data['task_id']);
    if (taskId != expectedTaskId) {
      throw const FormatException('作业进度与当前任务不一致。');
    }
    _checkWorkspace(data, expectedWorkspaceId);
    final current = _map(data['current']);
    final source = _association(data);
    final currentSource = _association(current);
    _checkTaskAndWorkspace(
      current,
      source,
      currentSource,
      expectedTaskId,
      expectedWorkspaceId,
    );
    final status = _string(current['status']);
    return TaskExecutionSnapshot(
      taskId: expectedTaskId,
      workspaceId: _string(data['workspace_id']) ?? expectedWorkspaceId,
      status: _status(status),
      rawStatus: status,
      jobId: _string(current['id']),
      stage: _string(current['kind']),
      progress: _int(current['progress']),
      attempt: _int(current['attempt']),
      reason: _string(current['failure_reason']),
      impact: _string(current['impact']),
      repairAction: _string(current['repair_action']),
      requestId: _associationValue(
        source,
        currentSource,
        data,
        current,
        'request_id',
      ),
      assistantTurnId: _associationValue(
        source,
        currentSource,
        data,
        current,
        'assistant_turn_id',
      ),
    );
  }

  static TaskRuntimeStatus _status(String? value) => switch (value) {
    'queued' || 'running' => TaskRuntimeStatus.running,
    'succeeded' || 'completed' => TaskRuntimeStatus.completed,
    'failed' || 'cancelled' || 'interrupted' => TaskRuntimeStatus.failed,
    _ => TaskRuntimeStatus.unknown,
  };

  static Map<String, dynamic> _map(Object? value) =>
      value is Map<String, dynamic> ? value : const <String, dynamic>{};

  static String? _string(Object? value) =>
      value is String && value.isNotEmpty ? value : null;

  static int? _int(Object? value) => value is int ? value : null;

  static bool? _bool(Object? value) => value is bool ? value : null;

  static Map<String, dynamic> _association(Map<String, dynamic> data) {
    for (final key in const [
      'source',
      'association',
      'request_source',
      'execution_source',
      'binding',
    ]) {
      final value = _map(data[key]);
      if (value.isNotEmpty) return value;
    }
    return const <String, dynamic>{};
  }

  static String? _associationValue(
    Map<String, dynamic> first,
    Map<String, dynamic> second,
    Map<String, dynamic> third,
    Map<String, dynamic> fourth,
    String key,
  ) {
    for (final map in [first, second, third, fourth]) {
      final value = _string(map[key]);
      if (value != null) return value;
    }
    return null;
  }

  static void _checkWorkspace(
    Map<String, dynamic> data,
    String expectedWorkspaceId,
  ) {
    final workspaceId = _string(data['workspace_id']);
    if (workspaceId != null && workspaceId != expectedWorkspaceId) {
      throw const FormatException('执行回执与当前工作区不一致。');
    }
    final jobWorkspaceId = _string(_map(data['job'])['workspace_id']);
    if (jobWorkspaceId != null && jobWorkspaceId != expectedWorkspaceId) {
      throw const FormatException('作业回执与当前工作区不一致。');
    }
  }

  static void _checkTaskAndWorkspace(
    Map<String, dynamic> job,
    Map<String, dynamic> source,
    Map<String, dynamic> jobSource,
    String expectedTaskId,
    String expectedWorkspaceId,
  ) {
    for (final map in [job, source, jobSource]) {
      final taskId = _string(map['task_id']);
      if (taskId != null && taskId != expectedTaskId) {
        throw const FormatException('执行关联与当前任务不一致。');
      }
      final workspaceId = _string(map['workspace_id']);
      if (workspaceId != null && workspaceId != expectedWorkspaceId) {
        throw const FormatException('执行关联与当前工作区不一致。');
      }
    }
  }
}
