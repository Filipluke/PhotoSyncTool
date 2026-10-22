"""Real locking and spawn-based regression coverage for library initialization."""
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from contextlib import closing
import hashlib
import multiprocessing
from pathlib import Path
import sqlite3
import threading
import time

import pytest

import photo_manager_index as index


def _import_at_barrier(root, source, barrier):
    from photo_manager_import import copy_verified
    barrier.wait(timeout=20)
    return copy_verified(Path(source), Path(root) / '2026' / 'same.jpg', Path(root))


def _features_at_barrier(root, barrier):
    from photo_manager_features import _connect_features
    barrier.wait(timeout=20)
    with closing(_connect_features(Path(root))) as conn:
        return [row[1] for row in conn.execute('PRAGMA table_info(ai_metadata)')]


def test_processes_initialize_and_record_every_import(tmp_path):
    context = multiprocessing.get_context('spawn')
    root = tmp_path / 'library'
    sources = []
    for n in range(8):
        path = tmp_path / str(n) / 'same.jpg'
        path.parent.mkdir()
        path.write_bytes(f'photo {n}'.encode())
        sources.append(str(path))
    with context.Manager() as manager:
        barrier = manager.Barrier(len(sources))
        with ProcessPoolExecutor(len(sources), mp_context=context) as pool:
            futures = [pool.submit(_import_at_barrier, str(root), src, barrier) for src in sources]
            results = [future.result(timeout=40) for future in futures]
    assert len({str(result[0]) for result in results}) == len(sources)
    with closing(index.connect(index.default_index_path(root))) as conn:
        assert conn.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
        assert conn.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        assert conn.execute('PRAGMA busy_timeout').fetchone()[0] == 30000
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        receipts = conn.execute('SELECT * FROM import_receipts').fetchall()
    assert len(receipts) == len(sources)
    for receipt in receipts:
        data = Path(receipt['dst']).read_bytes()
        assert data == Path(receipt['src']).read_bytes()
        assert hashlib.sha256(data).hexdigest() == receipt['sha256']
        assert len(data) == receipt['size_bytes']
    assert not list(root.rglob('*.part'))


def test_concurrent_feature_migrations(tmp_path):
    barrier = threading.Barrier(8)
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: _features_at_barrier(str(tmp_path), barrier), range(8)))
    assert all({'ocr_text', 'backend', 'details_json'} <= set(cols) for cols in results)


def test_busy_initialization_waits_then_recovers(tmp_path, monkeypatch):
    path = tmp_path / 'index.sqlite3'
    observed_busy = threading.Event()
    original = index._is_busy
    def observe(exc):
        result = original(exc)
        if result:
            observed_busy.set()
        return result
    monkeypatch.setattr(index, '_is_busy', observe)
    # A reader in rollback-journal mode prevents the WAL transition. The retry
    # uses a real SQLite lock; the event coordinates its release without sleeps.
    with closing(sqlite3.connect(path)) as blocker:
        blocker.execute('CREATE TABLE existing (id INTEGER)')
        blocker.execute('BEGIN')
        blocker.execute('SELECT * FROM existing').fetchall()
        def initialize():
            with closing(index.connect(path, timeout=0.3)) as conn:
                return conn.execute('PRAGMA journal_mode').fetchone()[0]
        # Force immediate busy responses while retaining connect's retry deadline.
        raw_connect = sqlite3.connect
        class NoWaitConnection(sqlite3.Connection):
            def execute(self, sql, *args, **kwargs):
                if sql.startswith('PRAGMA busy_timeout='):
                    sql = 'PRAGMA busy_timeout=0'
                return super().execute(sql, *args, **kwargs)
        monkeypatch.setattr(index.sqlite3, 'connect', lambda *a, **kw: raw_connect(*a, **kw, factory=NoWaitConnection))
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(initialize)
            assert observed_busy.wait(timeout=2)
            blocker.rollback()
            assert future.result(timeout=2) == 'wal'


def test_persistent_lock_has_bounded_failure_and_can_retry(tmp_path):
    path = tmp_path / 'index.sqlite3'
    with closing(sqlite3.connect(path)) as blocker:
        blocker.execute('BEGIN EXCLUSIVE')
        start = time.monotonic()
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            index.connect(path, timeout=0.08)
        assert time.monotonic() - start < 2
        blocker.rollback()
    with closing(index.connect(path)) as conn:
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


def test_schema_failure_rolls_back_and_is_not_retried(tmp_path, monkeypatch):
    path = tmp_path / 'index.sqlite3'
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('CREATE TABLE media_files (path TEXT)')
    monkeypatch.setattr(index.time, 'sleep', lambda _: pytest.fail('Non-lock errors must not be retried'))
    with pytest.raises(sqlite3.OperationalError, match='no such column'):
        index.connect(path)
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='app_meta'").fetchall() == []
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
