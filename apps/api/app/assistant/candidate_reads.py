"""Candidate reads inside the already-authorized scope of a background goal."""
from datetime import datetime
from app.assistant.material_reads import goal_material_ids
from app.assistant.task_business_errors import TaskBusinessRejected


def goal_candidate_scope(production, authorizations, actor, task_id, sealed):
    # Validate frozen selections using the existing owner, but retain their
    # segment boundaries instead of turning every selection into a whole asset.
    goal_material_ids(production, authorizations, actor, task_id, sealed)
    authorized_assets = set(authorizations.material_asset_ids(actor.workspace_id, task_id))
    scope = {'material': [{'asset_id': value} for value in sorted(authorized_assets)],
        'knowledge': [], 'person': [], 'voice': []}
    for ref in sealed.get('selected_references', []):
        if ref.get('kind') != 'material' or ref['selection']['asset_id'] in authorized_assets:
            continue
        selected = ref['selection']
        asset = production.get_asset(actor.workspace_id, selected['asset_id'])
        item = {'asset_id': asset.id, 'asset_version': datetime.fromisoformat(
            selected['expected_updated_at'].replace('Z', '+00:00')).isoformat()}
        if selected['scope'] == 'segment':
            segment = next(part for part in asset.segments if part.id == selected['segment_id'])
            item.update(segment_id=segment.id, segment_version=segment.version,
                        source_revision=segment.source_revision)
        scope['material'].append(item)
    authorization = production.task_authorization_records.get_task_authorization(actor.workspace_id, task_id)
    for domain, key in (('person', 'persons'), ('voice', 'voices')):
        for reference in authorization.resource_scope.get(key, []):
            profile_id, version = reference.rsplit(':', 1)
            scope[domain].append({'profile_id': profile_id, 'version': version})
    for row in sealed.get('source_revisions', {}).get('knowledge', []):
        scope['knowledge'].append({'knowledge_item_id': row[0], 'knowledge_version': str(row[1])})
    refs = [*sealed.get('selected_references', [])]
    refs.extend(sealed[key] for key in ('profile_reference', 'knowledge_reference') if sealed.get(key))
    for ref in refs:
        if ref.get('kind') in {'person', 'voice'}:
            item = {'profile_id': ref['profile_id'], 'version': ref['version']}
            if item not in scope[ref['kind']]:
                scope[ref['kind']].append(item)
        elif ref.get('kind') == 'knowledge':
            item = {'knowledge_item_id': ref['knowledge_item_id'], 'knowledge_version': ref['version']}
            if item not in scope['knowledge']:
                scope['knowledge'].append(item)
    return scope


def read_goal_candidates(case, production, authorizations, actor, task_id, sealed, operation, args):
    if args.get('task_id') not in {None, task_id}:
        raise TaskBusinessRejected('候选反馈不能切换原目标任务')
    common = {'user_id': actor.user_id or actor.member_id, 'local_diagnostic': False,
        'task_id': task_id, 'scope': goal_candidate_scope(production, authorizations, actor, task_id, sealed)}
    if operation == 'search_creation_candidates':
        return case.search(actor.workspace_id, query=args.get('query', ''),
            limit_per_domain=args.get('limit_per_domain', 5), **common)
    return case.read(actor.workspace_id, domain=args['domain'], object_id=args['object_id'],
        version=args['version'], **common)
