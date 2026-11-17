"""Knowledge discovery delegates access, search and fixed-version reads to SourceRepository."""
from app.materials.candidate_bundle import build_candidate_bundle
from app.materials.profile_discovery import domain_result
from app.source_policy import content_allows_use


class KnowledgeCandidateDiscovery:
    def __init__(self, production, sources):
        self.production, self.sources = production, sources

    def search(self, workspace_id, *, query, user_id, limit, local_diagnostic=False, references=None):
        if self.sources is None:
            return domain_result([], status='unavailable', reason='knowledge_reader_unavailable')
        if references is None:
            hits, total = self.sources.search_knowledge_items(workspace_id, query=query, limit=limit,
                user_id=user_id, local_diagnostic=local_diagnostic)
            refs = [{'knowledge_item_id': hit['id'], 'knowledge_version': hit['version']} for hit in hits]
            search_hits = {hit['id']: hit for hit in hits}
        else:
            refs, total, search_hits = references, 0, {}
        matches, unavailable, invalid = [], False, False
        seen = set()
        for ref in refs:
            if not ref.get('knowledge_item_id') or not ref.get('knowledge_version'):
                invalid = True
                continue
            identity = (ref['knowledge_item_id'], ref['knowledge_version'])
            if identity in seen:
                continue
            seen.add(identity)
            try:
                # Includes current visibility and cross-workspace grant revocation.
                item = self.sources.get_knowledge_reference_readable_by(workspace_id, *identity,
                    user_id=user_id, local_diagnostic=local_diagnostic)
                if not content_allows_use(item.use_state, 'reference'):
                    raise KeyError(identity)
            except (KeyError, PermissionError):
                unavailable = True
                continue
            if references is not None and query not in (item.title + ' ' + (item.body or '')).casefold():
                continue
            matches.append(item)
        if references is not None:
            total = len(matches)
        candidates = [self._candidate(item, search_hits.get(item.id, {}), limit,
            scoped=references is not None) for item in matches[:limit]]
        status = ('matched' if matches else 'unknown' if invalid else
                  'unavailable' if unavailable and references is not None else 'unknown' if unavailable else 'empty')
        return domain_result(candidates, total_count=None if unavailable or invalid else total, status=status,
            reason='fixed_version_required' if invalid else 'reference_unavailable_or_changed' if unavailable else None)

    def _candidate(self, item, hit, limit, *, scoped):
        # Claims belong to the authorized source workspace, including explicit grants.
        claims = self.production.list_knowledge_claims(item.workspace_id, item.id, knowledge_version=item.version)
        bundle = build_candidate_bundle(knowledge_claims=claims[:limit], knowledge_requested=True)
        return {'kind': 'knowledge_item', 'ref': 'knowledge:' + item.id,
            'asset_ref': None, 'status': 'candidate', 'quality': {'status': 'unknown'},
            'source_revision': {'knowledge_version': item.version, 'content_hash': item.content_hash},
            'evidence': {'status': 'available' if any(c['evidence']['status'] == 'available'
                for c in bundle['candidates']) else 'unknown'},
            'authorization': {'status': 'needs_confirmation', 'use_state': item.use_state,
                              'purpose': 'reference'},
            'language': {'status': 'unknown'},
            'details': {'title': item.title, 'relevance': hit.get('relevance'),
                'match_method': 'fixed_version_substring' if scoped else 'owner_search',
                'claims_bundle': bundle, 'claims_truncated': len(claims) > limit},
            'readback': {'domain': 'knowledge', 'object_id': item.id, 'version': item.version,
                'kind': 'knowledge', 'knowledge_item_id': item.id, 'knowledge_version': item.version}}
