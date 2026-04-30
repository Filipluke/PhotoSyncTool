#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

from sort_photos_script import detect_year, is_media_file, quick_fingerprint


INDEX_FILE_NAME = "photo_manager_index.sqlite3"
SCHEMA_VERSION = 1
INTERNAL_FILE_NAMES = {
    INDEX_FILE_NAME,
    f"{INDEX_FILE_NAME}-wal",
    f"{INDEX_FILE_NAME}-shm",
    "photo_manager_config.json",
    "photo_manager_sync_log.csv",
    "photo_manager_service.log",
}

Logger = Callable[[str], None]


@dataclass
class IndexStats:
    scanned: int = 0
    indexed: int = 0
    skipped: int = 0
    failed: int = 0


def default_index_path(root: Path) -> Path:
    return root / INDEX_FILE_NAME


def utc_now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


def normalize_path(path: Path) -> str:
    try:
        return str(path.expanduser().resolve())
    except Exception:
        return str(path.expanduser().absolute())


def relative_to_root(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except Exception:
        return path.name


def connect(index_path: Path) -> sqlite3.Connection:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(index_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    ensure_schema(conn)
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS app_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS media_files (
            path TEXT PRIMARY KEY,
            root TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            name TEXT NOT NULL,
            suffix TEXT NOT NULL,
            parent TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'library',
            status TEXT NOT NULL DEFAULT 'present',
            size_bytes INTEGER NOT NULL DEFAULT 0,
            mtime_epoch REAL NOT NULL DEFAULT 0,
            ctime_epoch REAL NOT NULL DEFAULT 0,
            year INTEGER,
            width INTEGER,
            height INTEGER,
            quick_hash TEXT,
            indexed_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_media_files_relative_path
            ON media_files(relative_path);
        CREATE INDEX IF NOT EXISTS idx_media_files_year
            ON media_files(year);
        CREATE INDEX IF NOT EXISTS idx_media_files_quick_hash
            ON media_files(quick_hash);
        CREATE INDEX IF NOT EXISTS idx_media_files_status
            ON media_files(status);

        CREATE TABLE IF NOT EXISTS blur_results (
            path TEXT PRIMARY KEY,
            score REAL NOT NULL,
            threshold REAL,
            status TEXT NOT NULL DEFAULT 'candidate',
            width INTEGER,
            height INTEGER,
            filesize_bytes INTEGER,
            mtime_epoch REAL,
            source_csv TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_blur_results_score
            ON blur_results(score);
        CREATE INDEX IF NOT EXISTS idx_blur_results_status
            ON blur_results(status);

        CREATE TABLE IF NOT EXISTS sync_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            mode TEXT NOT NULL,
            src TEXT,
            dst TEXT,
            year INTEGER,
            flags TEXT,
            status TEXT NOT NULL,
            details_json TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_sync_events_ts
            ON sync_events(ts);
        CREATE INDEX IF NOT EXISTS idx_sync_events_status
            ON sync_events(status);

        CREATE TABLE IF NOT EXISTS ai_metadata (
            path TEXT PRIMARY KEY,
            caption TEXT,
            tags_json TEXT,
            embedding_model TEXT,
            embedding_dim INTEGER,
            embedding BLOB,
            updated_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT OR REPLACE INTO app_meta(key, value) VALUES (?, ?)",
        ("schema_version", str(SCHEMA_VERSION)),
    )
    conn.commit()


def image_size(path: Path) -> tuple[Optional[int], Optional[int]]:
    try:
        from PIL import Image  # type: ignore
    except Exception:
        return None, None

    try:
        with Image.open(path) as img:
            return int(img.width), int(img.height)
    except Exception:
        return None, None


def safe_year(path: Path, date_source: str) -> Optional[int]:
    try:
        return int(detect_year(path, date_source=date_source))
    except Exception:
        return None


def safe_quick_hash(path: Path) -> Optional[str]:
    try:
        return quick_fingerprint(path)
    except Exception:
        return None


def upsert_file(
    conn: sqlite3.Connection,
    root: Path,
    path: Path,
    *,
    role: str = "library",
    status: str = "present",
    date_source: str = "exif",
    compute_hash: bool = False,
    year: Optional[int] = None,
) -> bool:
    if not path.exists() or not path.is_file():
        mark_file_status(conn, path, status="missing")
        return False

    root = root.resolve()
    path = path.resolve()
    stat = path.stat()
    width, height = image_size(path)
    now = utc_now()
    resolved_year = year if year is not None else safe_year(path, date_source)
    quick_hash = safe_quick_hash(path) if compute_hash else None

    conn.execute(
        """
        INSERT INTO media_files (
            path, root, relative_path, name, suffix, parent, role, status,
            size_bytes, mtime_epoch, ctime_epoch, year, width, height,
            quick_hash, indexed_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            root=excluded.root,
            relative_path=excluded.relative_path,
            name=excluded.name,
            suffix=excluded.suffix,
            parent=excluded.parent,
            role=excluded.role,
            status=excluded.status,
            size_bytes=excluded.size_bytes,
            mtime_epoch=excluded.mtime_epoch,
            ctime_epoch=excluded.ctime_epoch,
            year=COALESCE(excluded.year, media_files.year),
            width=excluded.width,
            height=excluded.height,
            quick_hash=COALESCE(excluded.quick_hash, media_files.quick_hash),
            updated_at=excluded.updated_at
        """,
        (
            normalize_path(path),
            normalize_path(root),
            relative_to_root(root, path),
            path.name,
            path.suffix.lower(),
            normalize_path(path.parent),
            role,
            status,
            int(stat.st_size),
            float(stat.st_mtime),
            float(stat.st_ctime),
            resolved_year,
            width,
            height,
            quick_hash,
            now,
            now,
        ),
    )
    return True


def mark_file_status(conn: sqlite3.Connection, path: Path, *, status: str) -> None:
    now = utc_now()
    conn.execute(
        """
        UPDATE media_files
        SET status = ?, updated_at = ?
        WHERE path = ?
        """,
        (status, now, normalize_path(path)),
    )


def record_sync_event(conn: sqlite3.Connection, row: dict) -> None:
    details = {
        key: value
        for key, value in row.items()
        if key not in {"mode", "src", "dst", "year", "flags", "status"}
    }
    year_raw = row.get("year", "")
    try:
        year = int(year_raw) if year_raw not in ("", None) else None
    except Exception:
        year = None
    conn.execute(
        """
        INSERT INTO sync_events (ts, mode, src, dst, year, flags, status, details_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            utc_now(),
            str(row.get("mode", "")),
            str(row.get("src", "")),
            str(row.get("dst", "")),
            year,
            str(row.get("flags", "")),
            str(row.get("status", "")),
            json.dumps(details, ensure_ascii=False),
        ),
    )


def index_sync_records(
    root: Path,
    records: Iterable[dict],
    *,
    date_source: str = "exif",
    compute_hash: bool = False,
    log: Optional[Logger] = None,
) -> IndexStats:
    stats = IndexStats()
    index_path = default_index_path(root)
    with closing(connect(index_path)) as conn:
        for row in records:
            stats.scanned += 1
            record_sync_event(conn, row)
            status = str(row.get("status", ""))
            src_raw = str(row.get("src", ""))
            if src_raw and status in {"copied", "dry_run", "copy_error"}:
                try:
                    src = Path(src_raw)
                    if not src.is_absolute():
                        src = root / src
                    if src.exists() and upsert_file(
                        conn,
                        root,
                        src,
                        role="source",
                        status="present",
                        date_source=date_source,
                        compute_hash=compute_hash,
                    ):
                        stats.indexed += 1
                except Exception:
                    stats.failed += 1
            dst_raw = str(row.get("dst", ""))
            if status == "copied" and dst_raw:
                try:
                    dst = Path(dst_raw)
                    if not dst.is_absolute():
                        dst = root / dst
                    year_raw = row.get("year", None)
                    year = int(year_raw) if year_raw not in ("", None) else None
                    if upsert_file(
                        conn,
                        root,
                        dst,
                        role="library",
                        status="present",
                        date_source=date_source,
                        compute_hash=compute_hash,
                        year=year,
                    ):
                        stats.indexed += 1
                    else:
                        stats.skipped += 1
                except Exception:
                    stats.failed += 1
        conn.commit()
    if log is not None:
        log(
            "index updated: "
            f"{index_path} "
            f"(events={stats.scanned}, files={stats.indexed}, failed={stats.failed})"
        )
    return stats


def import_blur_csv(
    root: Path,
    csv_path: Path,
    *,
    threshold: Optional[float] = None,
    log: Optional[Logger] = None,
) -> IndexStats:
    stats = IndexStats()
    index_path = default_index_path(root)
    with closing(connect(index_path)) as conn:
        with csv_path.open("r", newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                stats.scanned += 1
                raw_path = str(row.get("path", "")).strip()
                if not raw_path:
                    stats.skipped += 1
                    continue

                path = Path(raw_path)
                if not path.is_absolute():
                    path = (root / path).resolve()
                else:
                    path = path.resolve()

                try:
                    score = float(row.get("score", "0") or 0)
                    width = int(float(row.get("width", "0") or 0))
                    height = int(float(row.get("height", "0") or 0))
                    filesize = int(float(row.get("filesize_bytes", "0") or 0))
                    mtime = float(row.get("mtime_epoch", "0") or 0)
                    status = "candidate" if threshold is None or score <= threshold else "measured"
                    conn.execute(
                        """
                        INSERT INTO blur_results (
                            path, score, threshold, status, width, height,
                            filesize_bytes, mtime_epoch, source_csv, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(path) DO UPDATE SET
                            score=excluded.score,
                            threshold=excluded.threshold,
                            status=excluded.status,
                            width=excluded.width,
                            height=excluded.height,
                            filesize_bytes=excluded.filesize_bytes,
                            mtime_epoch=excluded.mtime_epoch,
                            source_csv=excluded.source_csv,
                            updated_at=excluded.updated_at
                        """,
                        (
                            normalize_path(path),
                            score,
                            threshold,
                            status,
                            width,
                            height,
                            filesize,
                            mtime,
                            normalize_path(csv_path),
                            utc_now(),
                        ),
                    )
                    if path.exists():
                        upsert_file(conn, root, path, role="library", status="present")
                    stats.indexed += 1
                except Exception:
                    stats.failed += 1
        conn.commit()
    if log is not None:
        log(
            "blur index imported: "
            f"{csv_path} "
            f"(rows={stats.scanned}, indexed={stats.indexed}, failed={stats.failed})"
        )
    return stats


def update_blur_status(root: Path, path: Path, *, status: str, score: Optional[float] = None) -> None:
    with closing(connect(default_index_path(root))) as conn:
        conn.execute(
            """
            INSERT INTO blur_results (path, score, status, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                score=COALESCE(excluded.score, blur_results.score),
                status=excluded.status,
                updated_at=excluded.updated_at
            """,
            (normalize_path(path), float(score or 0.0), status, utc_now()),
        )
        if status in {"deleted", "trashed", "missing"}:
            mark_file_status(conn, path, status=status)
        conn.commit()


def update_file_status(root: Path, path: Path, *, status: str) -> None:
    with closing(connect(default_index_path(root))) as conn:
        mark_file_status(conn, path, status=status)
        conn.commit()


def iter_library_files(root: Path, include_nonmedia: bool = False) -> Iterable[Path]:
    for path in root.rglob("*"):
        name = path.name.lower()
        if name in INTERNAL_FILE_NAMES:
            continue
        if name.startswith("blur_candidates") and name.endswith(".csv"):
            continue
        if name.endswith(".decisions.csv"):
            continue
        if is_media_file(path, include_nonmedia=include_nonmedia):
            yield path


def rebuild_index(
    root: Path,
    *,
    include_nonmedia: bool = False,
    compute_hash: bool = False,
    date_source: str = "exif",
    log: Optional[Logger] = None,
) -> IndexStats:
    stats = IndexStats()
    index_path = default_index_path(root)
    with closing(connect(index_path)) as conn:
        for path in iter_library_files(root, include_nonmedia=include_nonmedia):
            stats.scanned += 1
            try:
                if upsert_file(
                    conn,
                    root,
                    path,
                    role="library",
                    status="present",
                    date_source=date_source,
                    compute_hash=compute_hash,
                ):
                    stats.indexed += 1
            except Exception as exc:
                stats.failed += 1
                if log is not None:
                    log(f"index skipped: {path} ({exc})")
            if stats.scanned % 500 == 0:
                conn.commit()
                if log is not None:
                    log(f"index progress: scanned={stats.scanned}, indexed={stats.indexed}")
        conn.commit()
    if log is not None:
        log(
            "index rebuild complete: "
            f"{index_path} "
            f"(scanned={stats.scanned}, indexed={stats.indexed}, failed={stats.failed})"
        )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and maintain the Photo Manager SQLite index.")
    sub = parser.add_subparsers(dest="command", required=True)

    rebuild = sub.add_parser("rebuild", help="Scan the library and rebuild/update file metadata.")
    rebuild.add_argument("--root", required=True, help="Photo library root folder.")
    rebuild.add_argument("--include-nonmedia", action="store_true", help="Index non-media files too.")
    rebuild.add_argument("--compute-hash", action="store_true", help="Compute quick content hashes.")
    rebuild.add_argument("--date-source", default="exif", choices=["exif", "mtime", "ctime"])

    blur = sub.add_parser("import-blur", help="Import blur_tool CSV results into the index.")
    blur.add_argument("--root", required=True, help="Photo library root folder.")
    blur.add_argument("--csv", required=True, help="Blur CSV path.")
    blur.add_argument("--threshold", type=float, default=None)

    args = parser.parse_args()

    if args.command == "rebuild":
        rebuild_index(
            Path(args.root).expanduser().resolve(),
            include_nonmedia=args.include_nonmedia,
            compute_hash=args.compute_hash,
            date_source=args.date_source,
            log=print,
        )
    elif args.command == "import-blur":
        import_blur_csv(
            Path(args.root).expanduser().resolve(),
            Path(args.csv).expanduser().resolve(),
            threshold=args.threshold,
            log=print,
        )


if __name__ == "__main__":
    main()
