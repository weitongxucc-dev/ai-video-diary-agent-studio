from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
import app.main as main
from app.local_members import LocalMemberService
from app.production import MaterialVersionConflict
from test_material_tag_navigation import make_repo, register, WORKSPACE


def prepared(tmp_path):
    repo, library = make_repo(tmp_path)
    asset_id = register(repo, library, "cas.mp4", user_tags=["原标签"], segments=[{"user_tags":["原片段"]}])
    return repo, repo.get_asset(WORKSPACE,asset_id)


@pytest.mark.parametrize("scope", ["asset","segment"])
def test_same_frozen_revision_only_one_atomic_material_writer_wins(tmp_path, scope):
    repo, original = prepared(tmp_path)
    barrier=Barrier(2)
    def write(label):
        barrier.wait()
        try:
            if scope == "asset":
                saved=repo.update_asset_tags(WORKSPACE,original.id,[label],expected_asset_updated_at=original.updated_at)
            else:
                saved=repo.update_segment(WORKSPACE,original.id,original.segments[0].id,
                    description=label,transcript="保持",review_status="raw",user_tags=[label],
                    expected_asset_updated_at=original.updated_at,expected_segment_updated_at=original.segments[0].updated_at)
            return label,saved
        except MaterialVersionConflict:
            return label,None
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes=list(pool.map(write,["甲","乙"]))
    winners=[label for label,saved in outcomes if saved is not None]
    assert len(winners)==1
    rebuilt=type(repo)(tmp_path / "production.sqlite",tmp_path / "曜核AI IP一体机",duration_reader=lambda _:1.0,scene_reader=lambda *_:[])
    saved=rebuilt.get_asset(WORKSPACE,original.id)
    if scope=="asset":
        assert saved.user_tags==winners and saved.segments==original.segments
    else:
        assert saved.segments[0].user_tags==winners and saved.segments[0].description==winners[0]
        assert saved.user_tags==original.user_tags


def test_segment_revision_conflict_rolls_back_all_fields_and_api_is_409(tmp_path, monkeypatch):
    repo, original=prepared(tmp_path)
    segment=original.segments[0]
    current=repo.update_segment(WORKSPACE,original.id,segment.id,description="并发修订",transcript="新记录",review_status="raw",user_tags=["其他人的新标签"])
    monkeypatch.setattr(main,"production_repo",repo)
    monkeypatch.setattr(main,"current_workspace_id",lambda _:WORKSPACE)
    members=LocalMemberService(tmp_path / "members.sqlite3")
    _owner,token=members.whoami(None)
    monkeypatch.setattr(main,"local_members_service",members)
    repo.set_asset_visibility(WORKSPACE,original.id,"shared")
    current=repo.get_asset(WORKSPACE,original.id)
    client=TestClient(main.app,headers={"X-Local-Member-Token":token})
    rejected=client.put(f"/api/production/materials/{original.id}/segments/{segment.id}",json={
        "description":"过期不能写","transcript":"过期不能写","review_status":"rejected","user_tags":["过期"],
        "expected_asset_updated_at":current.updated_at.isoformat(),"expected_segment_updated_at":segment.updated_at.isoformat(),
    })
    assert rejected.status_code==409
    assert repo.get_asset(WORKSPACE,original.id)==current
    rejected=client.put(f"/api/production/materials/{original.id}/tags",json={"user_tags":["过期"],"expected_asset_updated_at":original.updated_at.isoformat()})
    assert rejected.status_code==409 and repo.get_asset(WORKSPACE,original.id)==current
