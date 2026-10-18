"""Actual original bytes, real SQLite transactions, permissions and restart reads."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
from pathlib import Path
import sqlite3
from uuid import uuid4

import pytest

from app.knowledge_file_store import (
    KnowledgeFileStore, KnowledgeOriginalUnavailable, KnowledgeOriginalIntegrityError,
    KnowledgeOriginalStorageError, MAX_ORIGINAL_FILE_BYTES,
)
from app.knowledge_files import parse_knowledge_file
from app.source_repository import SourceRepository
from app.source_types import SourceConnector, SourceItem, KnowledgeItem
from tests.test_knowledge_office_ingestion import word_file, excel_file


def source(repo, data, filename="产品.docx", *, workspace="ws", source_id=None):
    parsed = parse_knowledge_file(filename, data)
    connector = repo.upsert_connector(SourceConnector(id="files-" + workspace, workspace_id=workspace,
        platform="knowledge_files", protocol="local_file", account_or_rule="知识文件导入", frequency="manual", permissions=()))
    return SourceItem(id=source_id or uuid4().hex, workspace_id=workspace, connector_id=connector.id,
        source_label="file_import", source_url=None, content_type=parsed.content_type,
        author=None, published_at=None, body=parsed.full_text, subtitles=None, media_references=(),
        license="user_provided", title=parsed.title, file_reference=parsed.filename,
        content_hash=parsed.content_hash, sections=parsed.sections, parameters=parsed.parameters)


def uploaded(repo, data, filename="产品.docx", *, owner="alice", workspace="ws"):
    item = source(repo, data, filename, workspace=workspace)
    stored = repo.save_source_file(workspace, item, data, verified_owner_user_id=owner)
    knowledge = repo.save_source_item_to_knowledge(workspace, stored.id, verified_owner_user_id=owner)
    return stored, knowledge


def files(tmp_path):
    return [p for p in (tmp_path / "originals").rglob("*") if p.is_file()]


@pytest.mark.parametrize("data,filename", [(word_file(),"参数.docx"),(excel_file(),"参数.xlsx")])
def test_real_bytes_and_fixed_receipt_survive_repository_restart(tmp_path, data, filename):
    repo = SourceRepository(tmp_path / "app.sqlite3")
    stored, saved = uploaded(repo, data, filename)
    original = repo.read_knowledge_original("ws", saved.id, "1", user_id="alice")
    assert original.content == data
    assert original.filename == filename and original.content_type == stored.content_type
    assert original.content_hash == stored.content_hash == hashlib.sha256(data).hexdigest()
    assert original.workspace_id == "ws" and original.owner_user_id == "alice"
    assert original.version == "1" and original.source_item_id == stored.id
    assert original.size_bytes == len(data)
    assert len(files(tmp_path)) == 1
    reopened = SourceRepository(tmp_path / "app.sqlite3")
    assert reopened.read_knowledge_original("ws", saved.id, "1", user_id="alice") == original
    assert "content=b" not in repr(original)


def test_same_name_changed_bytes_get_distinct_originals_and_manual_edit_retains_source(tmp_path):
    repo = SourceRepository(tmp_path / "app.sqlite3")
    first_data, second_data = word_file("1200"), word_file("1600")
    first, saved = uploaded(repo, first_data)
    second, current = uploaded(repo, second_data)
    assert current.id == saved.id and current.version == "2"
    assert first.id != second.id
    assert repo.read_knowledge_original("ws", saved.id, "1", user_id="alice").content == first_data
    assert repo.read_knowledge_original("ws", saved.id, "2", user_id="alice").content == second_data
    edited = repo.edit_knowledge_item("ws", saved.id, expected_version="2", title="人工纠正", body="修订正文，原文件仍可查", verified_user_id="alice")
    original = repo.read_knowledge_original("ws", saved.id, edited.version, user_id="alice")
    assert edited.version == "3" and original.source_item_id == second.id and original.content == second_data
    assert original.content_hash != edited.content_hash  # Original bytes and corrected body have distinct identities.
    old_source, old_result = uploaded(repo, first_data)
    assert old_source.id == first.id and old_result.version == "3"
    assert len(files(tmp_path)) == 2
    with pytest.raises(KeyError):
        repo.read_knowledge_original("ws", saved.id, "missing-version", user_id="alice")


def test_duplicate_upload_keeps_one_source_original_and_restriction(tmp_path):
    repo = SourceRepository(tmp_path / "app.sqlite3")
    data = word_file()
    first, saved = uploaded(repo, data)
    repo.set_source_item_use_state("ws", first.id, "reference_only")
    second = source(repo, data, "改名.docx")
    reused = repo.save_source_file("ws", second, data, verified_owner_user_id="alice")
    assert reused.id == first.id and reused.file_reference == "产品.docx" and reused.use_state == "reference_only"
    assert len(repo.list_source_items("ws")) == 1 and len(files(tmp_path)) == 1
    original = repo.read_knowledge_original("ws", saved.id, "1", user_id="alice")
    assert original.filename == "产品.docx" and original.content == data


def test_duplicate_concurrent_uploads_share_the_committed_receipt(tmp_path):
    path = tmp_path / "app.sqlite3"
    first = SourceRepository(path)
    second = SourceRepository(path)
    data = word_file()
    items = [source(first, data), source(second, data)]
    def save(index):
        return (first,second)[index].save_source_file("ws", items[index], data, verified_owner_user_id="alice")
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(save,[0,1]))
    assert results[0].id == results[1].id
    assert len(first.list_source_items("ws")) == 1 and len(files(tmp_path)) == 1


def test_owner_workspace_sharing_and_current_revocation_gate_bytes(tmp_path, monkeypatch):
    repo = SourceRepository(tmp_path / "app.sqlite3")
    data = word_file()
    _, saved = uploaded(repo, data)
    for workspace, user in [("ws","bob"),("other","alice")]:
        with pytest.raises(KeyError): repo.read_knowledge_original(workspace, saved.id, "1", user_id=user)
    with pytest.raises(ValueError, match="已验证"):
        repo.read_knowledge_original("ws", saved.id, "1", user_id=None)
    repo.set_knowledge_item_visibility("ws", saved.id, "shared")
    assert repo.read_knowledge_original("ws", saved.id, "1", user_id="bob").content == data
    repo.set_knowledge_item_visibility("ws", saved.id, "private")
    grant = repo.grant_knowledge_item("ws", saved.id, "target", user_id="alice")
    assert repo.read_knowledge_original("target", saved.id, "1", user_id="bob").workspace_id == "ws"
    repo.revoke_knowledge_grant("ws", saved.id, grant.id, user_id="alice")
    # Authorization must fail before the filesystem sees an attempted read.
    monkeypatch.setattr(repo._original_files, "read", lambda *args: pytest.fail("revoked original must not be read"))
    with pytest.raises(KeyError): repo.read_knowledge_original("target", saved.id, "1", user_id="bob")
    with pytest.raises(KeyError): repo.read_knowledge_original("ws", saved.id, "1", user_id="bob")
    repo.set_knowledge_item_use_state("ws", saved.id, "excluded")
    with pytest.raises(KeyError): repo.read_knowledge_original("ws", saved.id, "1", user_id="alice")


def test_other_uploaders_and_workspaces_never_share_original_receipts(tmp_path):
    repo = SourceRepository(tmp_path / "app.sqlite3")
    data = word_file()
    first, saved = uploaded(repo, data)
    other, other_saved = uploaded(repo, data, owner="bob")
    third, third_saved = uploaded(repo, data, workspace="other")
    assert len({first.id,other.id,third.id}) == 3 and len(files(tmp_path)) == 3
    assert saved.id != other_saved.id != third_saved.id
    with pytest.raises(PermissionError):
        repo.save_source_item_to_knowledge("ws", first.id, verified_owner_user_id="bob")
    spoof = KnowledgeItem(id="spoof", workspace_id="ws", content_type="file", title="越权关联",
        body="假的关联", file_reference="产品.docx", authorization="", ownership="", source_item_id=first.id, version="1")
    with pytest.raises(PermissionError):
        repo.create_knowledge_item_owned(spoof, verified_user_id="bob")


def test_legacy_filename_and_version_without_receipt_cannot_recover_original(tmp_path):
    repo = SourceRepository(tmp_path / "app.sqlite3")
    data = word_file()
    old = source(repo, data)
    repo.save_source_items("ws",[old])
    saved = repo.save_source_item_to_knowledge("ws", old.id, verified_owner_user_id="alice")
    (tmp_path / "产品.docx").write_bytes(data)
    with pytest.raises(KnowledgeOriginalUnavailable, match="未保存上传原件"):
        repo.read_knowledge_original("ws", saved.id, "1", user_id="alice")
    assert not (tmp_path / "originals").exists()
    with pytest.raises(ValueError,match="新来源"):
        repo.save_source_file("ws", old, data, verified_owner_user_id="alice")


def test_source_with_an_original_cannot_be_overwritten_by_generic_refresh(tmp_path):
    repo = SourceRepository(tmp_path / "app.sqlite3")
    data = word_file()
    stored, saved = uploaded(repo, data)
    with pytest.raises(ValueError,match="不可覆盖"):
        repo.save_source_items("ws",[replace(stored,body="覆盖正文",content_hash="a"*64)])
    assert repo.read_knowledge_original("ws",saved.id,"1",user_id="alice").content == data
    assert repo.get_source_item("ws",stored.id) == stored


def test_failed_receipt_transaction_exposes_no_half_object_and_reuses_published_file(tmp_path):
    path = tmp_path / "app.sqlite3"
    repo = SourceRepository(path)
    data = word_file(); item = source(repo,data)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TRIGGER reject_receipt BEFORE INSERT ON knowledge_original_files BEGIN SELECT RAISE(ABORT, 'isolated receipt failure'); END")
    with pytest.raises(sqlite3.IntegrityError,match="isolated receipt failure"):
        repo.save_source_file("ws",item,data,verified_owner_user_id="alice")
    assert repo.list_source_items("ws") == [] and repo.list_knowledge_items("ws") == []
    published = files(tmp_path)
    assert len(published) == 1 and published[0].read_bytes() == data
    identity = (published[0].stat().st_ino,published[0].stat().st_mtime_ns)
    with sqlite3.connect(path) as conn: conn.execute("DROP TRIGGER reject_receipt")
    rebuilt = SourceRepository(path)
    stored = rebuilt.save_source_file("ws",item,data,verified_owner_user_id="alice")
    saved = rebuilt.save_source_item_to_knowledge("ws",stored.id,verified_owner_user_id="alice")
    assert rebuilt.read_knowledge_original("ws",saved.id,"1",user_id="alice").content == data
    assert (published[0].stat().st_ino,published[0].stat().st_mtime_ns) == identity


def test_copy_failure_cleans_temporary_file_and_does_not_register_source(tmp_path, monkeypatch):
    repo = SourceRepository(tmp_path / "app.sqlite3")
    data = word_file(); item = source(repo,data)
    def fail_link(*args): raise PermissionError("isolated destination publication denied")
    monkeypatch.setattr("app.knowledge_file_store.os.link",fail_link)
    with pytest.raises(PermissionError):
        repo.save_source_file("ws",item,data,verified_owner_user_id="alice")
    assert files(tmp_path) == [] and repo.list_source_items("ws") == []


def test_missing_corrupt_and_symlinked_originals_cannot_be_returned_or_overwritten(tmp_path):
    repo = SourceRepository(tmp_path / "app.sqlite3")
    data = word_file(); stored,saved = uploaded(repo,data)
    path = files(tmp_path)[0]
    bad = b"X" + data[1:]; path.write_bytes(bad)
    with pytest.raises(KnowledgeOriginalIntegrityError): repo.read_knowledge_original("ws",saved.id,"1",user_id="alice")
    with pytest.raises(KnowledgeOriginalIntegrityError): repo.save_source_file("ws",replace(stored,id="retry"),data,verified_owner_user_id="alice")
    assert path.read_bytes() == bad
    path.unlink()
    with pytest.raises(KnowledgeOriginalUnavailable): repo.read_knowledge_original("ws",saved.id,"1",user_id="alice")
    # Only a new upload of actual matching bytes can restore a previously received file.
    repo.save_source_file("ws",replace(stored,id="retry"),data,verified_owner_user_id="alice")
    assert repo.read_knowledge_original("ws",saved.id,"1",user_id="alice").content == data
    path.unlink();outside=tmp_path/"outside";outside.write_bytes(data);path.symlink_to(outside)
    with pytest.raises(KnowledgeOriginalStorageError): repo.read_knowledge_original("ws",saved.id,"1",user_id="alice")


def test_missing_disk_and_replaced_storage_directory_never_choose_another_root(tmp_path):
    missing = tmp_path / "missing-volume"
    repo = SourceRepository(tmp_path / "app.sqlite3",file_store=KnowledgeFileStore(missing))
    data=word_file();item=source(repo,data)
    with pytest.raises(KnowledgeOriginalStorageError): repo.save_source_file("ws",item,data,verified_owner_user_id="alice")
    assert not missing.exists() and repo.list_source_items("ws") == []
    outside = tmp_path / "other-root";outside.mkdir()
    (tmp_path / "originals").symlink_to(outside,target_is_directory=True)
    repo = SourceRepository(tmp_path / "app.sqlite3")
    with pytest.raises(KnowledgeOriginalStorageError): repo.save_source_file("ws",item,data,verified_owner_user_id="alice")
    assert list(outside.iterdir()) == []


def test_filename_hash_size_and_verified_identity_are_enforced(tmp_path):
    repo=SourceRepository(tmp_path / "app.sqlite3")
    data=word_file();item=source(repo,data)
    for changed,content,owner in [(item,data,None),(replace(item,content_hash="a"*64),data,"alice"),
                                   (replace(item,file_reference="../escape.docx"),data,"alice"),
                                   (item,b"","alice"),(item,b"x"*(MAX_ORIGINAL_FILE_BYTES+1),"alice")]:
        with pytest.raises(ValueError): repo.save_source_file("ws",changed,content,verified_owner_user_id=owner)
    assert repo.list_source_items("ws") == [] and files(tmp_path) == []


def test_read_only_storage_refuses_new_original_without_creating_a_source(tmp_path):
    repo=SourceRepository(tmp_path / "app.sqlite3")
    data=word_file();item=source(repo,data)
    tmp_path.chmod(0o500)
    try:
        with pytest.raises(OSError): repo.save_source_file("ws",item,data,verified_owner_user_id="alice")
    finally:
        tmp_path.chmod(0o700)
    assert repo.list_source_items("ws") == [] and files(tmp_path) == []


def test_only_an_actual_matching_reupload_adds_missing_fixed_version_original_evidence(tmp_path):
    repo=SourceRepository(tmp_path / "app.sqlite3")
    data=word_file();old=source(repo,data)
    repo.save_source_items("ws",[old])
    saved=repo.save_source_item_to_knowledge("ws",old.id,verified_owner_user_id="alice")
    before=repo.list_knowledge_item_versions("ws",saved.id)[0]
    assert before.original_source_item_id is None
    uploaded(repo,data,owner="bob")
    with pytest.raises(KnowledgeOriginalUnavailable): repo.read_knowledge_original("ws",saved.id,"1",user_id="alice")
    actual,current=uploaded(repo,data,filename="实际重传.docx")
    assert current.id==saved.id and current.version=="1" and current.source_item_id==old.id
    after=repo.list_knowledge_item_versions("ws",saved.id)[0]
    assert after == replace(before,original_source_item_id=actual.id)
    original=repo.read_knowledge_original("ws",saved.id,"1",user_id="alice")
    assert original.source_item_id==old.id and original.original_source_item_id==actual.id
    assert original.content==data and original.filename=="实际重传.docx"
    corrected=repo.edit_knowledge_item("ws",saved.id,expected_version="1",title="人工修订",body="保持原来源",verified_user_id="alice")
    assert repo.read_knowledge_original("ws",saved.id,corrected.version,user_id="alice").content==data


def test_actual_historical_reupload_never_substitutes_for_a_different_current_version(tmp_path):
    repo=SourceRepository(tmp_path / "app.sqlite3")
    first_data,second_data=word_file("1200"),word_file("1600")
    for data in (first_data,second_data):
        old=source(repo,data)
        repo.save_source_items("ws",[old])
        saved=repo.save_source_item_to_knowledge("ws",old.id,verified_owner_user_id="alice")
    assert saved.version=="2"
    actual,current=uploaded(repo,first_data)
    assert current.id==saved.id and current.version=="2"
    assert repo.read_knowledge_original("ws",saved.id,"1",user_id="alice").content==first_data
    with pytest.raises(KnowledgeOriginalUnavailable): repo.read_knowledge_original("ws",saved.id,"2",user_id="alice")


def test_existing_schema_upgrade_keeps_old_bytes_missing_and_body_unchanged(tmp_path):
    path=tmp_path / "app.sqlite3"
    repo=SourceRepository(path)
    data=word_file();old=source(repo,data)
    repo.save_source_items("ws",[old])
    saved=repo.save_source_item_to_knowledge("ws",old.id,verified_owner_user_id="alice")
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE knowledge_item_versions DROP COLUMN original_source_item_id")
        conn.execute("DROP TABLE knowledge_original_files")
    reopened=SourceRepository(path)
    assert reopened.get_knowledge_item("ws",saved.id).body==saved.body
    assert reopened.list_knowledge_item_versions("ws",saved.id)[0].original_source_item_id is None
    with pytest.raises(KnowledgeOriginalUnavailable,match="未保存上传原件"):
        reopened.read_knowledge_original("ws",saved.id,"1",user_id="alice")
    assert files(tmp_path)==[]


@pytest.mark.skipif(not hasattr(__import__('os'), 'mkfifo'), reason='FIFO is a POSIX filesystem boundary')
def test_nonregular_original_is_rejected_without_waiting_for_a_fifo_writer(tmp_path):
    import os
    repo=SourceRepository(tmp_path / "app.sqlite3")
    _,saved=uploaded(repo,word_file())
    path=files(tmp_path)[0];path.unlink();os.mkfifo(path)
    with pytest.raises(KnowledgeOriginalIntegrityError,match="类型已变化"):
        repo.read_knowledge_original("ws",saved.id,"1",user_id="alice")


def test_fresh_python_process_reads_committed_original_version(tmp_path):
    import json
    import os
    import subprocess
    import sys
    path=tmp_path / "app.sqlite3"
    repo=SourceRepository(path)
    data=excel_file();_,saved=uploaded(repo,data,"重启.xlsx")
    code="""
import hashlib,json,sys
from app.source_repository import SourceRepository
original=SourceRepository(sys.argv[1]).read_knowledge_original('ws',sys.argv[2],'1',user_id='alice')
print(json.dumps({'hash':hashlib.sha256(original.content).hexdigest(),'size':len(original.content),'version':original.version,'filename':original.filename}))
"""
    result=subprocess.run([sys.executable,"-c",code,str(path),saved.id],cwd=tmp_path,
        env={**os.environ,"PYTHONPATH":str(Path(__file__).resolve().parents[1])},
        capture_output=True,text=True,check=True,timeout=10)
    assert json.loads(result.stdout)=={"hash":hashlib.sha256(data).hexdigest(),"size":len(data),"version":"1","filename":"重启.xlsx"}
