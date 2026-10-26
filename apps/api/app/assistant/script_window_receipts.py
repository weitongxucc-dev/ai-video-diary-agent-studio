"""Finite script-request facts and reconciliation against the original receipt."""
import re
from datetime import datetime
from uuid import UUID
from app.assistant.business_failure import _safe_business_failure


def project_script_window_request(details, *, expected_operation=None, is_error=False):
    """Whitelist request facts; original candidate bodies stay behind receipt lookup."""
    if not isinstance(details,dict) or details.get('kind')!='script_window_request':
        return None
    operation=details.get('operation')
    if operation not in {'start','patch','confirm'} or expected_operation is not None and operation!=expected_operation:
        return None
    request_id=details.get('request_id')
    try:
        request_id=str(UUID(request_id))
    except (ValueError,TypeError,AttributeError):
        return None
    identifier=lambda value: value if isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}',value) else None
    task_id=identifier(details.get('task_id'))
    if task_id is None:
        return None
    result={'kind':'script_window_request','operation':operation,'task_id':task_id,'request_id':request_id}
    script_id=identifier(details.get('script_id'))
    if operation!='start' and script_id is None: return None
    if script_id is not None: result['script_id']=script_id
    for key in ('input_id','input_turn_id'):
        value=identifier(details.get(key))
        if value is not None: result[key]=value
    failure=_safe_business_failure(details.get('failure'))
    receipt=details.get('receipt') if 'receipt' in details else details
    if not isinstance(receipt,dict): receipt={}
    matching=(receipt.get('task_id')==task_id and receipt.get('request_id')==request_id
              and (script_id is None or receipt.get('script_id')==script_id))
    status=receipt.get('status') if matching else None
    if matching and receipt.get('error_detail') is not None:
        error=receipt['error_detail']
        failure={**failure,**_safe_business_failure(error if isinstance(error,dict) else {'reason':error})}
    expected_status='confirmed' if operation=='confirm' else 'saved'
    if matching and not is_error and status==expected_status and identifier(receipt.get('script_id')):
        revision=receipt.get('updated_at')
        try:
            valid_revision=isinstance(revision,str) and len(revision)<=80 and datetime.fromisoformat(revision).tzinfo is not None
        except ValueError:
            valid_revision=False
        if valid_revision:
            result.update(status=status,script_id=receipt['script_id'],updated_at=revision)
            count=receipt.get('total_count')
            if type(count) is int and count>=0: result['total_count']=count
            ids=receipt.get('affected_section_ids')
            if isinstance(ids,list) and len(ids)<=20 and all(identifier(value) is not None for value in ids):
                result['affected_section_ids']=ids
        else: result.update(status='unknown',submission_effect='unknown')
    elif matching and status in {'unknown','failed'}:
        result.update(status=status,submission_effect='unknown')
    else:
        effect=failure.get('submission_effect')
        result.update(status=effect if effect in {'not_submitted','rejected'} else 'unknown')
        result['submission_effect']=effect if effect in {'not_submitted','rejected'} else 'unknown'
    if failure and result['status'] not in {'saved','confirmed'}:
        if result['status'] in {'unknown','failed'}: failure['submission_effect']='unknown'
        result['failure']=failure
    return result


def script_window_message(value: dict) -> str:
    return {
        'saved':'原文案窗口保存回执已核对；不代表完整稿已采用。',
        'confirmed':'原请求的完整文案采用回执已核对；不代表成片已完成。',
        'not_submitted':'文案窗口请求明确未提交；原请求定位和拒绝原因已保留。',
        'rejected':'文案窗口请求已被拒绝；原请求定位和拒绝原因已保留。',
        'unknown':'文案原请求结果尚未完整核清；先查询原请求，不重复提交。',
        'failed':'文案原请求有失败记录但没有完整成功回执；保留原请求，不换键重提。',
    }[value['status']]


def resolve_script_window_request(value: dict, *, task_id: str, read_original) -> dict:
    """Reconcile one original request through the caller's authorized reader."""
    current=project_script_window_request(value)
    if current is None or current['task_id']!=task_id:
        raise ValueError('文案原请求定位与当前任务不一致')
    try:
        receipt=read_original(current['request_id'],current['operation'],current.get('script_id'))
    except KeyError:
        # Absence of a receipt does not prove that a previous write was never submitted.
        return current if current.get('submission_effect') in {'not_submitted','rejected'} else {
            **current,'status':'unknown','submission_effect':'unknown'}
    return project_script_window_request({**current,'receipt':receipt})
