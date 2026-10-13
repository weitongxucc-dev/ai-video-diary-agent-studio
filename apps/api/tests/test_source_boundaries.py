"""Exercise import and public-call boundaries without host services or vault reads."""

from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from app import source_policy, source_storage_schema, source_sync, source_types, sources
from app.knowledge_grants import KnowledgeGrantTarget, grant_to_verified_target
from app.source_repository import SourceRepository


def test_public_sources_export_one_implementation():
    for name in (
        "CONTENT_USE_STATES", "ContentUsePurpose", "ContentUseState", "SourceConnector",
        "SourceItem", "KnowledgeItemVersion", "KnowledgeItem", "KnowledgeGrant",
        "SourceRunEvidence", "TopicSourceItem", "ObsidianNote",
    ):
        assert getattr(sources, name) is getattr(source_types, name)
    for name in (
        "CONTENT_VISIBILITY_PRIVATE", "CONTENT_VISIBILITY_SHARED", "content_allows_use",
        "knowledge_item_readable",
    ):
        assert getattr(sources, name) is getattr(source_policy, name)
    for name in (
        "SourceBoundaryError", "SourceSyncFailedError", "source_error_from_http_status",
        "sync_source_connector",
    ):
        assert getattr(sources, name) is getattr(source_sync, name)
    from app.knowledge_grants import KNOWLEDGE_GRANTS_DDL

    assert KNOWLEDGE_GRANTS_DDL is source_storage_schema.KNOWLEDGE_GRANTS_DDL


@pytest.mark.parametrize("module", ["app.source_types", "app.source_policy", "app.source_sync", "app.source_repository"])
def test_source_boundaries_load_and_work_without_upstream_adapters(module, tmp_path):
    # A clean interpreter exposes runtime imports that an already-loaded test process masks.
    script = textwrap.dedent("""
        import importlib
        import importlib.abc
        from pathlib import Path
        import sys

        class RejectUpstream(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path, target=None):
                blocked = {
                    'app.sources', 'app.knowledge_grants', 'app.repository',
                    'app.main', 'app.production', 'fastapi',
                }
                if sys.argv[1] != 'app.source_repository':
                    blocked.add('app.source_repository')
                if sys.argv[1] != 'app.source_sync':
                    blocked.add('app.source_sync')
                if fullname in blocked:
                    raise AssertionError(f'Unexpected upstream runtime import: {fullname}')

        sys.meta_path.insert(0, RejectUpstream())
        Path.rglob = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('Vault scan forbidden'))
        importing = True
        def reject_import_side_effects(event, args):
            if importing and event in {'sqlite3.connect', 'subprocess.Popen', 'socket.connect'}:
                raise AssertionError(f'Import started a runtime side effect: {event}')
        sys.addaudithook(reject_import_side_effects)
        importlib.import_module(sys.argv[1])
        importing = False
        if sys.argv[1] == 'app.source_repository':
            from app.source_repository import SourceRepository
            from app.source_types import KnowledgeItem
            repo = SourceRepository(sys.argv[2])
            item = repo.create_knowledge_item_owned(KnowledgeItem(
                id='fixture-item', workspace_id='fixture-space', content_type='text',
                title='资料', body='测试原文', file_reference=None,
                authorization='本人资料', ownership='本人', source_item_id=None, version='1',
            ), verified_user_id='uploader')
            reopened = SourceRepository(sys.argv[2])
            assert reopened.get_knowledge_item_readable_by('fixture-space', item.id, user_id='uploader') == item
            assert reopened.list_knowledge_items_readable_by('fixture-space', user_id='other-member') == []
        assert not Path(sys.argv[2]).exists() or sys.argv[1] == 'app.source_repository'
    """)
    result = subprocess.run(
        [sys.executable, "-c", script, module, str(tmp_path / "fixture.sqlite3")],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_target_adapter_cannot_grant_an_unverified_space_or_another_uploaders_item(tmp_path):
    repo = SourceRepository(tmp_path / "sources.sqlite3")
    item = repo.create_knowledge_item_owned(source_types.KnowledgeItem(
        id="private-item", workspace_id="source-space", content_type="text",
        title="资料", body="原文", file_reference=None,
        authorization="自有", ownership="甲", source_item_id=None, version="1",
    ), verified_user_id="uploader")
    targets = [KnowledgeGrantTarget("verified-target", "测试空间")]
    for actor, target in (("uploader", "invented-target"), ("other-member", "verified-target")):
        with pytest.raises(KeyError):
            grant_to_verified_target(
                repo, source_workspace_id=item.workspace_id, item_id=item.id,
                target_workspace_id=target, user_id=actor, targets=targets,
            )
        assert repo.list_knowledge_grants(item.workspace_id, item.id, user_id="uploader") == []
    grant = grant_to_verified_target(
        repo, source_workspace_id=item.workspace_id, item_id=item.id,
        target_workspace_id="verified-target", user_id="uploader", targets=targets,
    )
    assert grant.granted_by == "uploader"
    assert repo.get_knowledge_item_readable_by("verified-target", item.id, user_id="reader").id == item.id
