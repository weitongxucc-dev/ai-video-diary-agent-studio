"""One read use case over existing material, knowledge and profile owners.

No new index or candidate persistence. None scope means visible discovery;
an explicit scope is closed: omitted domains cannot search the library.
"""
from app.materials.candidate_bundle import build_candidate_bundle
from app.materials.knowledge_discovery import KnowledgeCandidateDiscovery
from app.materials.profile_discovery import ProfileCandidateDiscovery, domain_result
from app.source_policy import knowledge_item_readable


DOMAINS = ('material', 'knowledge', 'person', 'voice')


class CrossDomainCandidateQuery:
    def __init__(self, production, sources):
        self.production = production
        self.profiles = ProfileCandidateDiscovery(production)
        self.knowledge = KnowledgeCandidateDiscovery(production, sources)

    def _authorize(self, workspace_id, user_id, local_diagnostic, task_id, scope):
        if not workspace_id or not user_id and not local_diagnostic:
            raise ValueError('候选查询必须携带工作区及已验证成员身份')
        if scope is not None and (not isinstance(scope, dict) or set(scope) - set(DOMAINS)
                or any(not isinstance(refs, list) or any(not isinstance(ref, dict) for ref in refs)
                       for refs in scope.values())):
            raise ValueError('候选范围必须是按域声明的固定引用列表')
        if task_id is not None:
            task = self.production.task_records.get_creation_task(workspace_id, task_id)
            if task.workspace_id != workspace_id or not knowledge_item_readable(user_id=user_id,
                    local_diagnostic=local_diagnostic, visibility=task.visibility, owner_user_id=task.owner_user_id):
                raise KeyError(task_id)

    def search(self, workspace_id, *, query, user_id, limit_per_domain=5,
               task_id=None, local_diagnostic=False, scope=None):
        self._authorize(workspace_id, user_id, local_diagnostic, task_id, scope)
        if (not isinstance(query, str) or not query.strip() or len(query) > 120
                or type(limit_per_domain) is not int or not 1 <= limit_per_domain <= 20):
            raise ValueError('候选查询需要1–120字查询和1–20条每域上限')
        query = query.strip().casefold()
        domains = {}
        for domain in DOMAINS:
            refs = scope.get(domain, []) if scope is not None else None
            if refs == []:
                domains[domain] = domain_result([], total_count=0, status='not_in_goal_scope')
            elif domain == 'material':
                domains[domain] = self._materials(workspace_id, query=query, user_id=user_id,
                    limit=limit_per_domain, local_diagnostic=local_diagnostic, references=refs)
            elif domain == 'knowledge':
                domains[domain] = self.knowledge.search(workspace_id, query=query, user_id=user_id,
                    limit=limit_per_domain, local_diagnostic=local_diagnostic, references=refs)
            else:
                domains[domain] = self.profiles.search(workspace_id, domain, query=query, user_id=user_id,
                    limit=limit_per_domain, local_diagnostic=local_diagnostic, task_id=task_id, references=refs)
        return {'schema': 'candidate-discovery/v1', 'query': query, 'limit_per_domain': limit_per_domain,
            'scope': 'goal_references' if scope is not None else 'member_visible', 'domains': domains,
            'instruction': '各域独立有界检索，匹配不代表授权采用；未命中不代表空库。固定版本、unknown、不可用及任务反馈须按原回执解释。'}

    def _asset(self, workspace_id, identity, user_id, local_diagnostic):
        asset = self.production.get_asset(workspace_id, identity)
        if asset.workspace_id != workspace_id or not knowledge_item_readable(user_id=user_id,
                local_diagnostic=local_diagnostic, visibility=asset.visibility, owner_user_id=asset.owner_user_id):
            raise KeyError(identity)
        return asset

    @staticmethod
    def _segment_in_scope(asset, segment, ref):
        return (asset.id == ref.get('asset_id')
            and ('asset_version' not in ref or ref['asset_version'] == asset.updated_at.isoformat())
            and ('segment_id' not in ref or ref['segment_id'] == segment.id)
            and ('segment_version' not in ref or ref['segment_version'] == segment.version)
            and ('source_revision' not in ref or ref['source_revision'] == segment.source_revision))

    def _material_candidate(self, workspace_id, match, user_id, local_diagnostic):
        asset = self._asset(workspace_id, match.asset_ref.removeprefix('asset:'), user_id, local_diagnostic)
        segment = next((item for item in asset.segments if 'segment:' + item.id == match.segment_ref), None)
        if segment is None:
            raise KeyError(match.segment_ref)
        revision = segment.source_revision or {'asset_updated_at': asset.updated_at.isoformat(),
                                               'modified_at': asset.modified_at.isoformat()}
        qualities = self.production.material_data_governance.list_quality_profiles(workspace_id, asset.id)
        quality = next((profile for profile in qualities
                        if segment.source_revision and profile.source_revision == segment.source_revision), None)
        candidate = build_candidate_bundle([match], material_asset=asset,
            material_segment_for=lambda _: segment, material_source_revision_for=lambda _: dict(revision),
            material_quality_profile=quality)['candidates'][0]
        candidate['readback'] = {'domain': 'material', 'kind': 'material', 'object_id': asset.id,
            'version': asset.updated_at.isoformat(), 'asset_id': asset.id,
            'segment_id': segment.id, 'segment_version': segment.version, 'source_revision': dict(segment.source_revision)}
        return candidate, asset, segment

    def _materials(self, workspace_id, *, query, user_id, limit, local_diagnostic, references=None):
        assets, eligible, unavailable, invalid = None, None, False, False
        if references is not None:
            assets, eligible = set(), set()
            for ref in references:
                if not ref.get('asset_id'):
                    invalid = True
                    continue
                try:
                    asset = self._asset(workspace_id, ref['asset_id'], user_id, local_diagnostic)
                except (KeyError, PermissionError):
                    unavailable = True
                    continue
                selected = [segment for segment in asset.segments if self._segment_in_scope(asset, segment, ref)]
                if not selected:
                    unavailable = True
                for segment in selected:
                    assets.add(asset.id)
                    eligible.add((asset.id, segment.id))
        result = self.production.material_search.search_material_segments(workspace_id, query=query,
            asset_ids=sorted(assets) if assets is not None else None, eligible_segment_refs=eligible,
            viewer_user_id=user_id, local_diagnostic=local_diagnostic, purpose='manual',
            usage_roles=None, limit=limit, include_eligibility=True)
        candidates = []
        for match in result['matches']:
            try:
                candidate, asset, segment = self._material_candidate(workspace_id, match, user_id, local_diagnostic)
                if references is not None and not any(self._segment_in_scope(asset, segment, ref) for ref in references):
                    raise KeyError(match.segment_ref)
                candidates.append(candidate)
            except (KeyError, PermissionError):
                unavailable = True
        status = ('matched' if candidates else 'unknown' if invalid else
                  'unavailable' if unavailable and references is not None else 'unknown' if unavailable else 'empty')
        response = domain_result(candidates, total_count=None if unavailable or invalid else result['total_count'],
            status=status, reason='fixed_reference_required' if invalid else
            'reference_unavailable_or_changed' if unavailable else None)
        response['qualification_message'] = result['qualification_message']
        return response

    def read(self, workspace_id, *, domain, object_id, version, user_id, task_id=None,
             local_diagnostic=False, scope=None):
        self._authorize(workspace_id, user_id, local_diagnostic, task_id, scope)
        if domain not in DOMAINS or not isinstance(object_id, str) or not object_id or not isinstance(version, str) or not version:
            raise ValueError('候选回读必须指定域、对象ID和固定字符串版本')
        refs = scope.get(domain, []) if scope is not None else None
        if refs is not None:
            identity_key = {'material': 'asset_id', 'knowledge': 'knowledge_item_id',
                            'person': 'profile_id', 'voice': 'profile_id'}[domain]
            version_key = {'material': 'asset_version', 'knowledge': 'knowledge_version',
                           'person': 'version', 'voice': 'version'}[domain]
            refs = [ref for ref in refs if ref.get(identity_key) == object_id
                    and (version_key not in ref and domain == 'material' or ref.get(version_key) == version)]
            if not refs:
                raise KeyError(object_id)
        if domain == 'material':
            asset = self._asset(workspace_id, object_id, user_id, local_diagnostic)
            if asset.updated_at.isoformat() != version:
                raise KeyError(object_id)
            result = self._materials(workspace_id, query='', user_id=user_id, limit=20,
                local_diagnostic=local_diagnostic, references=refs if refs is not None else [{'asset_id': object_id}])
            candidates = result['candidates']
            bundle = build_candidate_bundle()
            bundle['candidates'] = candidates
            bundle['summary'].update(total=len(candidates), material_candidates=len(candidates),
                needs_confirmation=sum(c['authorization']['status'] == 'needs_confirmation' for c in candidates))
        elif domain == 'knowledge':
            result = self.knowledge.search(workspace_id, query='', user_id=user_id, limit=20,
                local_diagnostic=local_diagnostic, references=[{'knowledge_item_id': object_id, 'knowledge_version': version}])
            candidates = result['candidates']
            if not candidates:
                raise KeyError(object_id)
            bundle = candidates[0]['details']['claims_bundle']
        else:
            candidate = self.profiles.read_candidate(workspace_id, domain, object_id, version, user_id,
                local_diagnostic, task_id=task_id)
            candidate['readback'] = {'domain': domain, 'object_id': object_id,
                                    'kind': domain, 'profile_id': object_id, 'version': version,
                                    **({'task_id': task_id} if task_id is not None else {})}
            candidates = [candidate]
            bundle = build_candidate_bundle(profile_candidates=candidates)
            result = domain_result(candidates, total_count=1)
        return {'schema': 'candidate-readback/v1', 'domain': domain, 'object_id': object_id, 'version': version,
                'candidates': candidates, 'bundle': bundle, 'status': result['status'],
                'truncated': result['truncated']}
