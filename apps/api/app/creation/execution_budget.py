"""Cumulative authorization never replaces finite device work-unit limits."""


def finite_unit_limit(unit_limit, total_remaining):
    """An unset task ceiling never removes the configured work-unit ceiling."""
    return unit_limit if total_remaining is None else min(unit_limit, total_remaining)


def exhausted(limits, used_seconds, used_bytes):
    return ((limits['parent_timeout_seconds'] is not None and used_seconds >= limits['parent_timeout_seconds'])
            or (limits['parent_max_output_bytes'] is not None and used_bytes >= limits['parent_max_output_bytes']))


def narration_limits(usage, unit_budget):
    if usage.get('scope') is not None and usage['scope']['authorization']['revoked']:
        raise PermissionError('累计执行授权已撤销，原消耗与未知占用保留')
    budget = usage['budget']
    if budget is not None:
        limits = {key: budget[key] for key in ('parent_timeout_seconds', 'parent_max_output_bytes')}
        if any(value is None for value in limits.values()) and usage.get('scope') is None:
            raise PermissionError('无累计总限需要实际授权范围绑定')
        return limits
    if usage.get('scope') is not None:
        return {'parent_timeout_seconds': None, 'parent_max_output_bytes': None}
    return {'parent_timeout_seconds': min(300.0, unit_budget.max_timeout_seconds),
            'parent_max_output_bytes': min(3 * 1024 * 1024, unit_budget.max_output_bytes)}


def narration_remaining(usage, limits, *, request=None):
    seconds = None if limits['parent_timeout_seconds'] is None else limits['parent_timeout_seconds'] - usage['used_seconds']
    size = None if limits['parent_max_output_bytes'] is None else limits['parent_max_output_bytes'] - usage['used_output_bytes']
    scope = usage.get('scope')
    if scope is not None:
        policy, measured = scope['authorization'], scope['usage']
        total = policy['limits']
        for dimension, settled, reserved in (
                ('execution_seconds', 'execution_seconds', 'reserved_seconds'),
                ('generated_bytes', 'generated_bytes', 'reserved_bytes')):
            if total[dimension] is not None:
                remaining = total[dimension] - measured[settled] - measured[reserved]
                if dimension == 'execution_seconds':
                    seconds = remaining if seconds is None else min(seconds, remaining)
                else:
                    size = remaining if size is None else min(size, remaining)
    if scope is not None and request is not None:
        # Quota changes cannot rewrite a sealed unit; admission reserves its original bounds.
        seconds, size = request.resources['timeout_seconds'], request.resources['max_output_bytes']
    return seconds, size
