from dataclasses import replace
from pathlib import Path
import sqlite3

import pytest

import photo_manager_core as core
from test_photo_manager_core import runtime_config


def setup_import(tmp_path, names_and_bytes):
    cfg = replace(runtime_config(tmp_path), root=tmp_path / 'library')
    cfg.source.mkdir()
    for name, data in names_and_bytes:
        path = cfg.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return cfg


def media(root):
    return sorted(root.rglob('*.jpg'))


def test_batch_same_names_keep_both_contents(tmp_path):
    cfg = setup_import(tmp_path, [('a/IMG_20260101.jpg', b'first'), ('b/IMG_20260101.jpg', b'second')])
    core.run_batch_sync(cfg, lambda _: None)
    assert {p.read_bytes() for p in media(cfg.root)} == {b'first', b'second'}


def test_batch_retry_is_idempotent_and_records_sha256(tmp_path):
    cfg = setup_import(tmp_path, [('IMG_20260101.jpg', b'photo')])
    core.run_batch_sync(cfg, lambda _: None)
    core.run_batch_sync(cfg, lambda _: None)
    assert len(media(cfg.root)) == 1
    with sqlite3.connect(cfg.root / 'photo_manager_index.sqlite3') as conn:
        receipt = conn.execute('SELECT sha256, size_bytes FROM import_receipts').fetchone()
        assert len(receipt[0]) == 64
        assert receipt[1] == 5
        statuses = [r[0] for r in conn.execute('SELECT status FROM sync_events ORDER BY id')]
        assert statuses == ['verified', 'already_present']


def test_retry_finds_suffix_after_collision(tmp_path):
    cfg = setup_import(tmp_path, [('a/IMG_20260101.jpg', b'one'), ('b/IMG_20260101.jpg', b'two')])
    for _ in range(2):
        core.run_batch_sync(cfg, lambda _: None)
    assert len(media(cfg.root)) == 2


def test_dry_run_does_not_create_library(tmp_path):
    cfg = replace(setup_import(tmp_path, [('IMG_20260101.jpg', b'photo')]), dry_run=True)
    core.run_batch_sync(cfg, lambda _: None)
    assert not cfg.root.exists()


def test_interrupted_copy_keeps_source_and_retry_recovers(tmp_path, monkeypatch):
    cfg = replace(setup_import(tmp_path, [('IMG_20260101.jpg', b'complete photo')]), delete_after_sync=True)
    import shutil
    original = shutil.copyfile
    def interrupted(src, dst, *args, **kwargs):
        Path(dst).write_bytes(b'partial')
        raise OSError('simulated interrupted copy')
    monkeypatch.setattr(shutil, 'copyfile', interrupted)
    core.run_batch_sync(cfg, lambda _: None)
    assert len(media(cfg.source)) == 1
    assert media(cfg.root) == []
    monkeypatch.setattr(shutil, 'copyfile', original)
    core.run_batch_sync(cfg, lambda _: None)
    assert [p.read_bytes() for p in media(cfg.root)] == [b'complete photo']
    assert media(cfg.source) == []


def test_watch_copy_failure_never_deletes_source(tmp_path, monkeypatch):
    cfg = replace(setup_import(tmp_path, [('IMG_20260101.jpg', b'photo')]), watch_delete=True)
    monkeypatch.setattr(core, 'ensure_file_stable', lambda *a, **kw: True)
    import shutil
    def fail(*a, **kw):
        raise OSError('simulated write failure')
    monkeypatch.setattr(shutil, 'copyfile', fail)
    service = core.BackgroundSyncService(lambda _: None)
    service.cfg = cfg
    service.process_path(cfg.source / 'IMG_20260101.jpg')
    assert len(media(cfg.source)) == 1


def test_changed_destination_is_not_trusted_on_retry(tmp_path):
    cfg = setup_import(tmp_path, [('IMG_20260101.jpg', b'correct')])
    core.run_batch_sync(cfg, lambda _: None)
    media(cfg.root)[0].write_bytes(b'changed')
    core.run_batch_sync(cfg, lambda _: None)
    assert {p.read_bytes() for p in media(cfg.root)} == {b'correct', b'changed'}


def test_concurrent_imports_never_overwrite(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from photo_manager_import import copy_verified
    cfg = setup_import(tmp_path, [('a/IMG_20260101.jpg', b'first'), ('b/IMG_20260101.jpg', b'second')])
    target = cfg.root / '2026' / 'IMG_20260101.jpg'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda src: copy_verified(src, target, cfg.root), media(cfg.source)))
    assert len({result[0] for result in results}) == 2
    assert {p.read_bytes() for p in media(cfg.root)} == {b'first', b'second'}


def test_receipt_failure_keeps_source_and_retry_reuses_copy(tmp_path, monkeypatch):
    import photo_manager_index as index
    cfg = replace(setup_import(tmp_path, [('IMG_20260101.jpg', b'photo')]), delete_after_sync=True)
    original = index.connect
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError('simulated database unavailable')
    monkeypatch.setattr(index, 'connect', fail)
    core.run_batch_sync(cfg, lambda _: None)
    assert len(media(cfg.source)) == 1
    assert len(media(cfg.root)) == 1
    monkeypatch.setattr(index, 'connect', original)
    core.run_batch_sync(cfg, lambda _: None)
    assert len(media(cfg.root)) == 1
    assert media(cfg.source) == []


def test_watch_partial_fanout_failure_keeps_source(tmp_path, monkeypatch):
    import shutil
    cfg = replace(setup_import(tmp_path, [('snapshot_20260101.jpg', b'photo')]), watch_delete=True)
    monkeypatch.setattr(core, 'ensure_file_stable', lambda *a, **kw: True)
    original = shutil.copyfile
    def partial(src, dst, *args, **kwargs):
        if Path(dst).parent.name == 'SnapShots':
            raise OSError('simulated second destination unavailable')
        return original(src, dst, *args, **kwargs)
    monkeypatch.setattr(shutil, 'copyfile', partial)
    service = core.BackgroundSyncService(lambda _: None)
    service.cfg = cfg
    service.process_path(media(cfg.source)[0])
    assert len(media(cfg.source)) == 1
    assert len(media(cfg.root)) == 1


def test_cli_collision_and_retry_use_verified_import(tmp_path):
    from sort_photos_script import plan_action, execute_actions
    cfg = setup_import(tmp_path, [('a/IMG_20260101.jpg', b'one'), ('b/IMG_20260101.jpg', b'two')])
    for _ in range(2):
        # Even independently prepared actions are safe at execution time.
        actions = [plan_action(cfg.root, src, 'exif') for src in media(cfg.source)]
        assert execute_actions(actions, cfg.root, full_hash=False, dry_run=False)[0]
    assert {p.read_bytes() for p in media(cfg.root)} == {b'one', b'two'}
    assert len(media(cfg.root)) == 2


def test_copy_corruption_leaves_no_final_file(tmp_path, monkeypatch):
    import shutil
    cfg = replace(setup_import(tmp_path, [('IMG_20260101.jpg', b'photo')]), delete_after_sync=True)
    monkeypatch.setattr(shutil, 'copyfile', lambda src, dst: Path(dst).write_bytes(b'wrong'))
    core.run_batch_sync(cfg, lambda _: None)
    assert media(cfg.root) == []
    assert len(media(cfg.source)) == 1


def test_cleanup_rechecks_source_after_user_confirmation(tmp_path):
    from sort_photos_script import plan_action, execute_actions, delete_originals
    cfg = setup_import(tmp_path, [('IMG_20260101.jpg', b'photo')])
    action = plan_action(cfg.root, media(cfg.source)[0], 'exif')
    assert execute_actions([action], cfg.root, False, False)[0]
    action.src.write_bytes(b'new photo')
    with pytest.raises(OSError):
        delete_originals([action], recursive=False)
    assert action.src.read_bytes() == b'new photo'


def test_existing_schema_keeps_library_metadata(tmp_path):
    import photo_manager_index as index
    root = tmp_path / 'library'
    root.mkdir()
    with sqlite3.connect(index.default_index_path(root)) as conn:
        conn.execute('CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        conn.execute("INSERT INTO app_meta VALUES ('schema_version', '1')")
        conn.execute("INSERT INTO app_meta VALUES ('custom_setting', 'preserved')")
    conn = index.connect(index.default_index_path(root))
    try:
        assert conn.execute("SELECT value FROM app_meta WHERE key='custom_setting'").fetchone()[0] == 'preserved'
        assert conn.execute("SELECT value FROM app_meta WHERE key='schema_version'").fetchone()[0] == '2'
        assert conn.execute('SELECT COUNT(*) FROM import_receipts').fetchone()[0] == 0
    finally:
        conn.close()


def test_batch_result_separates_copies_and_existing_files(tmp_path):
    cfg = setup_import(tmp_path, [('IMG_20260101.jpg', b'photo')])
    first = core.run_batch_sync(cfg, lambda _: None)
    second = core.run_batch_sync(cfg, lambda _: None)
    assert (first['copied'], first['verified'], first['already_present'], first['errors']) == (1, 1, 0, 0)
    assert (second['copied'], second['verified'], second['already_present'], second['errors']) == (0, 1, 1, 0)


@pytest.mark.skipif(__import__('os').name == 'nt', reason='Creating symlinks requires Windows developer privileges')
def test_dangling_symlink_is_preserved_as_collision(tmp_path):
    from photo_manager_import import copy_verified
    cfg = setup_import(tmp_path, [('IMG_20260101.jpg', b'photo')])
    target = cfg.root / '2026' / 'IMG_20260101.jpg'
    target.parent.mkdir(parents=True)
    target.symlink_to(target.parent / 'missing.jpg')
    actual, status, _ = copy_verified(media(cfg.source)[0], target, cfg.root)
    assert target.is_symlink()
    assert actual != target
    assert actual.read_bytes() == b'photo'
    assert status == 'verified'


def test_read_only_source_can_be_imported(tmp_path):
    import stat
    from photo_manager_import import copy_verified
    cfg = setup_import(tmp_path, [('IMG_20260101.jpg', b'photo')])
    src = media(cfg.source)[0]
    src.chmod(stat.S_IREAD)
    dst = cfg.root / '2026' / src.name
    try:
        assert copy_verified(src, dst, cfg.root)[1] == 'verified'
        assert copy_verified(src, dst, cfg.root)[1] == 'already_present'
        assert dst.read_bytes() == b'photo'
    finally:
        src.chmod(stat.S_IREAD | stat.S_IWRITE)
        if dst.exists():
            dst.chmod(stat.S_IREAD | stat.S_IWRITE)
