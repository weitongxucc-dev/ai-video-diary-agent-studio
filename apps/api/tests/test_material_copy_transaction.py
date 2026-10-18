"""The public reception confirmation joins the receiver's own transaction."""
import pytest

from app.production import ProductionRepository


@pytest.mark.parametrize('commit', [False, True])
def test_copy_confirmation_never_commits_independently(tmp_path, commit):
    library = tmp_path / 'library'
    library.mkdir()
    repository = ProductionRepository(tmp_path / 'app.sqlite3', library)
    repository.bind_material_ingestion('workspace', expected_directory=str(library))
    path = library / 'received.mp4'
    path.write_bytes(b'isolated file, no media processing')
    with repository._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        repository.confirm_material_copy_complete('workspace', relative_path=path.name,
            expected_revision=repository.material_copy_revision(path.stat()), confirmation_source='app_import',
            verified_owner_user_id='member', connection=conn)
        assert conn.in_transaction
        assert conn.execute('SELECT copy_uploader_user_id FROM material_ingestion_files').fetchone()[0] == 'member'
        if not commit:
            conn.rollback()
    with repository._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM material_ingestion_files').fetchone()[0] == int(commit)
