"""Fixed-goal material reads shared by ordinary and draft-generation turns."""
from datetime import datetime

from app.assistant.task_business_errors import TaskBusinessRejected, TaskOperationRejected
from app.materials.evidence import MaterialEvidenceConflict

MATERIAL_READ_OPERATIONS = frozenset({'search_task_materials', 'read_task_material', 'read_material_evidence', 'read_clip_candidates'})


def goal_material_ids(production, authorizations, actor, task_id, sealed):
    """Explicit frozen references permit reads, never enrollment of the whole library."""
    allowed = set(authorizations.material_asset_ids(actor.workspace_id, task_id))
    for item in sealed.get('selected_references', []):
        if item['kind'] != 'material':
            continue
        selection = item['selection']
        try:
            evidence = production.material_assets.read_evidence(actor.workspace_id, selection['asset_id'],
                member_id=actor.user_id or actor.member_id, expected_updated_at=selection['expected_updated_at'], limit=1)
            if evidence.get('content_withheld'):
                raise TaskBusinessRejected('原目标引用的素材已排除')
            if selection['scope'] == 'segment':
                asset = production.get_asset(actor.workspace_id, selection['asset_id'])
                segment = next((part for part in asset.segments if part.id == selection['segment_id']), None)
                if segment is None or segment.updated_at != datetime.fromisoformat(selection['expected_segment_updated_at'].replace('Z', '+00:00')):
                    raise MaterialEvidenceConflict('原目标引用的素材片段已变化')
        except MaterialEvidenceConflict as exc:
            raise TaskOperationRejected(str(exc), kind='conflict') from exc
        allowed.add(selection['asset_id'])
    return allowed


def read_goal_materials(production, authorizations, actor, task_id, operation, args, *, material_facts, sealed=None, montage_edits=None):
    ws = actor.workspace_id
    allowed = goal_material_ids(production, authorizations, actor, task_id, sealed or {})
    if operation == 'read_clip_candidates':
        from app.montage import creation_task_montage_plan_from_payload
        from app.source_policy import knowledge_item_readable
        plan = creation_task_montage_plan_from_payload(production.get_creation_task_montage_plan(ws, task_id))
        clip = next((item for item in plan.clips if item.id == args['clip_id']), None)
        if clip is None:
            raise TaskBusinessRejected('当前任务没有该镜头')
        assets = [production.get_asset(ws, key) for key in sorted(allowed)]
        assets = [asset for asset in assets if knowledge_item_readable(
            user_id=actor.user_id or actor.member_id, local_diagnostic=False,
            visibility=asset.visibility, owner_user_id=asset.owner_user_id)]
        return [item.model_dump(mode='json') for item in montage_edits.montage_candidates(
            ws, plan, clip, automation_only=True, assets=assets)]
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
