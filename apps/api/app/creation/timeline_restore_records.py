"""Original timeline-restore request claims and durable recovery receipts."""
import hashlib
import json
from app.creation.contracts import TaskEvent


class TimelineRestoreRecordStore:
    def __init__(self, connection_factory, *, append_event, get_event, decode_operation, read_readiness):
        self._connect = connection_factory
        self.append_event, self.get_event = append_event, get_event
        self.decode_operation, self.read_readiness = decode_operation, read_readiness

    def latest_operation_restore(self, workspace_id, task_id, operation_id):
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM edit_operations WHERE workspace_id=? AND task_id=? "
                "AND operation_type IN ('UndoTimelineVersion','RedoTimelineVersion') "
                "AND json_extract(payload_json,'$.target_operation_id')=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
                (workspace_id, task_id, operation_id)).fetchone()
        return self.decode_operation(row) if row is not None else None

    @staticmethod
    def request_id(workspace_id, member_id, request_id):
        return 'event_edit_request_' + hashlib.sha256(json.dumps(
            [workspace_id, member_id, str(request_id), 'restore'], sort_keys=True,
            ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()

    def claim(self, task, member_id, request_id, inputs, *, write_guard=None):
        event_id = self.request_id(task.workspace_id, member_id, request_id)
        fingerprint = hashlib.sha256(json.dumps(inputs, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT task_id,payload_json FROM task_events WHERE workspace_id=? AND id=?',
                (task.workspace_id, event_id)).fetchone()
            if row is not None:
                if row['task_id'] != task.id or json.loads(row['payload_json'])['input_digest'] != fingerprint:
                    raise ValueError('原恢复请求已绑定其他操作或输入')
                return event_id, False
            if write_guard is not None:
                write_guard(conn)
            self.append_event(TaskEvent(event_id, task.workspace_id, task.id, None, 'edit_request_claimed',
                {'member_id': member_id, 'request_id': request_id, 'phase': 'restore',
                    'input_digest': fingerprint, 'inputs': inputs}), connection=conn)
        return event_id, True

    def receipt(self, workspace_id, task_id, member_id, request_id):
        event_id = self.request_id(workspace_id, member_id, request_id)
        with self._connect() as conn:
            claim = self.get_event(workspace_id, event_id, connection=conn)
            if claim.task_id != task_id or claim.payload['member_id'] != member_id:
                raise KeyError(request_id)
            row = conn.execute("SELECT * FROM edit_operations WHERE workspace_id=? AND task_id=? "
                "AND json_extract(payload_json,'$.restore_request_id')=? "
                "AND json_extract(payload_json,'$.restore_member_id')=? ORDER BY created_at DESC LIMIT 1",
                (workspace_id, task_id, request_id, member_id)).fetchone()
            refusal = conn.execute('SELECT payload_json FROM task_events WHERE workspace_id=? AND id=?',
                (workspace_id, event_id+'_refusal')).fetchone()
        operation = self.decode_operation(row) if row is not None else None
        return {'request_id': request_id, 'task_id': task_id, 'action': claim.payload['inputs']['action'],
            'target_operation_id': operation.payload['target_operation_id'] if operation else claim.payload['inputs']['target_operation_id'],
            'status': 'applied' if operation else 'refused' if refusal else 'result_unknown',
            'operation_id': operation.id if operation else None, 'result_version_id': operation.result_version_id if operation else None,
            'execution_readiness': self.read_readiness(workspace_id,task_id,operation.id) if operation else None,
            'failure': json.loads(refusal['payload_json']) if refusal is not None and operation is None else None}


    def record_refusal(self, event_id, workspace_id, task_id, reason):
        self.append_event(TaskEvent(event_id+'_refusal', workspace_id, task_id, None,
            'timeline_restore_refused', {'code': 'timeline_restore_refused', 'effect': 'refused',
                'reason': reason[:512], 'retryable': False}))
