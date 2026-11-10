"""Fixed-goal material reads shared by ordinary and draft-generation turns."""
from app.assistant.task_business_errors import TaskBusinessRejected, TaskOperationRejected
from app.materials.evidence import MaterialEvidenceConflict

MATERIAL_READ_OPERATIONS = frozenset({'search_task_materials', 'read_task_material', 'read_material_evidence'})


def read_goal_materials(production, authorizations, actor, task_id, operation, args, *, material_facts):
    ws = actor.workspace_id
    allowed = authorizations.material_asset_ids(ws, task_id)
    if operation in {'read_task_material', 'read_material_evidence'}:
        if args['asset_id'] not in allowed:
            raise TaskBusinessRejected('素材不在原目标固定范围内')
        if operation == 'read_material_evidence':
            try:
                return production.material_assets.read_evidence(ws, args['asset_id'],
                    member_id=actor.user_id or actor.member_id,
                    **{key: value for key, value in args.items() if key in {
                        'kind', 'expected_updated_at', 'start_seconds', 'end_seconds', 'offset', 'limit'}})
            except MaterialEvidenceConflict as exc:
                raise TaskOperationRejected(str(exc), kind='conflict') from exc
        return material_facts(production.get_asset(ws, args['asset_id']))
    query = str(args.get('query', '')).casefold()
    limit = int(args.get('limit', 8))
    if not 1 <= limit <= 20:
        raise ValueError('素材搜索范围须为1至20')
    if args.get('include_eligibility'):
        purpose = args.get('purpose', 'manual')
        if purpose not in {'manual', 'automatic'}:
            raise ValueError('镜头检索用途无效')
        return production.material_search.search_material_segments(ws, query=query, asset_ids=sorted(allowed),
            usage_roles=['a_roll', 'b_roll'], limit=limit, viewer_user_id=actor.user_id or actor.member_id,
            purpose=purpose, include_eligibility=True)
    assets = [asset for asset in production.list_assets(ws, viewer_user_id=actor.user_id or actor.member_id)
              if asset.id in allowed and (not query or query in
                  (asset.file_name + ' ' + asset.user_description + ' ' + ' '.join(asset.user_tags)).casefold())]
    return {"assets": [material_facts(asset) for asset in assets[:limit]],
            "total_count": len(assets), "has_more": len(assets) > limit}
