"""Discover readable profiles, then resolve each exact version through its owner."""
from app.materials.profile_feedback import ProfileCandidateFeedbackReader
from app.materials.profile_candidates import ProfileCandidateReader


def domain_result(candidates, *, total_count=None, status=None, reason=None):
    return {'status': status or ('matched' if candidates else 'empty'),
        'candidates': candidates, 'returned_count': len(candidates), 'total_count': total_count,
        'truncated': total_count > len(candidates) if total_count is not None else None,
        'reason': reason}


class ProfileCandidateDiscovery:
    def __init__(self, repository):
        self.repository = repository
        self.read_candidate = ProfileCandidateFeedbackReader(repository)
        self.metadata = ProfileCandidateReader(repository)

    def search(self, workspace_id, kind, *, query, user_id, limit, local_diagnostic=False,
               task_id=None, references=None):
        repo = self.repository
        if references is None:
            listing = (repo.profile_records.list_person_profiles if kind == 'person'
                       else repo.profile_records.list_versioned_voice_profiles)
            profiles = [p for p in listing(workspace_id) if p.workspace_id == workspace_id
                        and repo.profile_readable(p, user_id, local_diagnostic)]
            refs = [{'profile_id': p.id, 'version': p.version} for p in profiles]
        else:
            refs = references
        getter = (repo.profile_records.get_person_profile if kind == 'person'
                  else repo.profile_records.get_versioned_voice_profile)
        matches, changed, invalid = [], False, False
        seen = set()
        for ref in refs:
            if not ref.get('profile_id') or not ref.get('version'):
                invalid = True
                continue
            identity = (ref['profile_id'], ref['version'])
            if identity in seen:
                continue
            seen.add(identity)
            try:
                profile = getter(workspace_id, *identity)
                if profile.workspace_id != workspace_id or not repo.profile_readable(profile, user_id, local_diagnostic):
                    raise KeyError(identity)
                # Reference audio/text/paths are deliberately not search fields.
                text = ' '.join([profile.id, profile.display_name,
                    getattr(profile, 'language', ''), *getattr(profile, 'background_capabilities', ()),
                    *getattr(profile, 'shot_capabilities', ())]).casefold()
                if query not in text:
                    continue
                candidate = self.metadata(workspace_id, kind, *identity, user_id, local_diagnostic)
            except (KeyError, PermissionError):
                changed = True
                continue
            candidate['readback'] = {'domain': kind, 'object_id': identity[0],
                                    'kind': kind, 'profile_id': identity[0], 'version': identity[1],
                                    **({'task_id': task_id} if task_id is not None else {})}
            matches.append(candidate)
        matches.sort(key=lambda c: (c['ref'], c['source_revision']['profile_version']))
        selected = matches[:limit]
        for candidate in selected:
            locator = candidate['readback']
            # Production history is read only for the bounded result page.
            if task_id is not None:
                candidate['feedback'] = self.read_candidate(workspace_id, kind,
                    locator['profile_id'], locator['version'], user_id, local_diagnostic,
                    task_id=task_id)['feedback']
            else:
                candidate['feedback'] = {'status': 'not_requested'}
        status = ('matched' if matches else 'unknown' if invalid else
                  'unavailable' if changed and references is not None else 'unknown' if changed else 'empty')
        return domain_result(selected, total_count=None if changed or invalid else len(matches), status=status,
            reason='fixed_version_required' if invalid else 'reference_unavailable_or_changed' if changed else None)
