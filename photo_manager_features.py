#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
import warnings
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

from photo_manager_index import (
    INTERNAL_DIR_NAMES,
    INTERNAL_FILE_NAMES,
    connect,
    default_index_path,
    normalize_path,
    relative_to_root,
    utc_now,
)
from sort_photos_script import is_media_file


Logger = Callable[[str], None]

THUMB_DIR_NAME = ".photo_manager_cache"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".tif", ".tiff"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".3gp"}


@dataclass
class GalleryItem:
    path: Path
    name: str
    relative_path: str
    year: Optional[int]
    status: str
    size_bytes: int
    width: Optional[int]
    height: Optional[int]
    blur_score: Optional[float]
    blur_status: Optional[str]
    caption: str
    tags: list[str]


@dataclass
class DuplicateCandidate:
    keep: Path
    remove: Path
    reason: str
    group_key: str
    size_bytes: int


class DuplicateScanCancelled(Exception):
    pass


@dataclass
class DeleteQueueItem:
    id: int
    path: Path
    reason: str
    source: str
    status: str
    created_at: str
    updated_at: str
    details_json: str


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row["name"]) for row in rows}


def ensure_feature_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS delete_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL,
            reason TEXT NOT NULL,
            source TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            details_json TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_delete_queue_status
            ON delete_queue(status);
        CREATE INDEX IF NOT EXISTS idx_delete_queue_path
            ON delete_queue(path);
        """
    )

    ai_cols = _table_columns(conn, "ai_metadata")
    if "ocr_text" not in ai_cols:
        conn.execute("ALTER TABLE ai_metadata ADD COLUMN ocr_text TEXT")
    if "backend" not in ai_cols:
        conn.execute("ALTER TABLE ai_metadata ADD COLUMN backend TEXT")
    if "details_json" not in ai_cols:
        conn.execute("ALTER TABLE ai_metadata ADD COLUMN details_json TEXT")
    conn.commit()


def _connect_features(root: Path) -> sqlite3.Connection:
    conn = connect(default_index_path(root))
    ensure_feature_schema(conn)
    return conn


def parse_tags(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except Exception:
        return []
    if isinstance(parsed, list):
        return [str(item) for item in parsed if str(item).strip()]
    return []


def list_gallery_items(
    root: Path,
    *,
    year: str = "",
    status: str = "present",
    blur_max: Optional[float] = None,
    search: str = "",
    limit: int = 400,
) -> list[GalleryItem]:
    where = []
    params: list[object] = []

    if status and status.lower() != "all":
        where.append("mf.status = ?")
        params.append(status)
    if year and year.lower() != "all":
        where.append("mf.year = ?")
        params.append(int(year))
    if blur_max is not None:
        where.append("br.score IS NOT NULL AND br.score <= ?")
        params.append(float(blur_max))
    if search:
        like = f"%{search.lower()}%"
        where.append(
            """
            (
                lower(mf.name) LIKE ?
                OR lower(mf.relative_path) LIKE ?
                OR lower(COALESCE(ai.caption, '')) LIKE ?
                OR lower(COALESCE(ai.tags_json, '')) LIKE ?
                OR lower(COALESCE(ai.ocr_text, '')) LIKE ?
            )
            """
        )
        params.extend([like, like, like, like, like])

    sql = """
        SELECT
            mf.path, mf.name, mf.relative_path, mf.year, mf.status,
            mf.size_bytes, mf.width, mf.height,
            br.score AS blur_score, br.status AS blur_status,
            COALESCE(ai.caption, '') AS caption,
            COALESCE(ai.tags_json, '[]') AS tags_json
        FROM media_files mf
        LEFT JOIN blur_results br ON br.path = mf.path
        LEFT JOIN ai_metadata ai ON ai.path = mf.path
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY mf.mtime_epoch DESC, mf.relative_path ASC LIMIT ?"
    params.append(max(1, int(limit)))

    with closing(_connect_features(root)) as conn:
        rows = conn.execute(sql, params).fetchall()

    items: list[GalleryItem] = []
    for row in rows:
        items.append(
            GalleryItem(
                path=Path(str(row["path"])),
                name=str(row["name"]),
                relative_path=str(row["relative_path"]),
                year=row["year"],
                status=str(row["status"]),
                size_bytes=int(row["size_bytes"] or 0),
                width=row["width"],
                height=row["height"],
                blur_score=row["blur_score"],
                blur_status=row["blur_status"],
                caption=str(row["caption"] or ""),
                tags=parse_tags(row["tags_json"]),
            )
        )
    return items


def gallery_filter_options(root: Path) -> tuple[list[str], list[str]]:
    with closing(_connect_features(root)) as conn:
        years = [
            str(row["year"])
            for row in conn.execute(
                "SELECT DISTINCT year FROM media_files WHERE year IS NOT NULL ORDER BY year DESC"
            ).fetchall()
        ]
        statuses = [
            str(row["status"])
            for row in conn.execute(
                "SELECT DISTINCT status FROM media_files ORDER BY status"
            ).fetchall()
        ]
    return years, statuses


def dashboard_stats(root: Path) -> dict:
    with closing(_connect_features(root)) as conn:
        total = conn.execute("SELECT COUNT(*) AS n FROM media_files").fetchone()["n"]
        present = conn.execute(
            "SELECT COUNT(*) AS n FROM media_files WHERE status = 'present'"
        ).fetchone()["n"]
        ai_count = conn.execute("SELECT COUNT(*) AS n FROM ai_metadata").fetchone()["n"]
        blur_pending = conn.execute(
            "SELECT COUNT(*) AS n FROM blur_results WHERE status = 'candidate'"
        ).fetchone()["n"]
        delete_queued = conn.execute(
            "SELECT COUNT(*) AS n FROM delete_queue WHERE status = 'queued'"
        ).fetchone()["n"]
        year_rows = conn.execute(
            """
            SELECT COALESCE(CAST(year AS TEXT), 'Unknown') AS label, COUNT(*) AS n
            FROM media_files
            GROUP BY label
            ORDER BY label DESC
            LIMIT 20
            """
        ).fetchall()
        folder_rows = conn.execute(
            """
            SELECT parent, COUNT(*) AS n, SUM(size_bytes) AS bytes
            FROM media_files
            WHERE status = 'present'
            GROUP BY parent
            ORDER BY n DESC
            LIMIT 15
            """
        ).fetchall()
        recent_events = conn.execute(
            """
            SELECT ts, mode, src, dst, status
            FROM sync_events
            ORDER BY id DESC
            LIMIT 12
            """
        ).fetchall()
        sync_errors = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM sync_events
            WHERE lower(status) LIKE '%error%'
               OR lower(status) LIKE '%failed%'
               OR lower(status) LIKE '%fail%'
            """
        ).fetchone()["n"]

    folders = []
    for row in folder_rows:
        parent = Path(str(row["parent"]))
        folders.append(
            {
                "folder": relative_to_root(root, parent),
                "count": int(row["n"] or 0),
                "bytes": int(row["bytes"] or 0),
            }
        )

    return {
        "total": int(total or 0),
        "present": int(present or 0),
        "ai_count": int(ai_count or 0),
        "blur_pending": int(blur_pending or 0),
        "delete_queued": int(delete_queued or 0),
        "sync_errors": int(sync_errors or 0),
        "years": [{"year": str(row["label"]), "count": int(row["n"] or 0)} for row in year_rows],
        "folders": folders,
        "recent_events": [dict(row) for row in recent_events],
    }


def _thumbnail_cache_dir(root: Path) -> Path:
    path = root / THUMB_DIR_NAME / "thumbnails"
    path.mkdir(parents=True, exist_ok=True)
    return path


def thumbnail_path(root: Path, path: Path, *, size: int = 160) -> Optional[Path]:
    if not path.exists() or path.suffix.lower() not in IMAGE_SUFFIXES:
        return None
    try:
        stat = path.stat()
    except Exception:
        return None
    key_raw = f"{normalize_path(path)}|{stat.st_size}|{stat.st_mtime_ns}|{size}"
    key = hashlib.blake2b(key_raw.encode("utf-8"), digest_size=16).hexdigest()
    return _thumbnail_cache_dir(root) / f"{key}.jpg"


def build_thumbnail(root: Path, path: Path, *, size: int = 160) -> Optional[Path]:
    out = thumbnail_path(root, path, size=size)
    if out is None:
        return None
    if out.exists():
        return out
    try:
        from PIL import Image, ImageOps

        with warnings.catch_warnings():
            if hasattr(Image, "DecompressionBombWarning"):
                warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            img = Image.open(path)
        with img:
            img = ImageOps.exif_transpose(img)
            img.thumbnail((size, size), Image.Resampling.LANCZOS)
            if img.mode in {"RGBA", "LA"}:
                bg = Image.new("RGBA", img.size, (22, 25, 30, 255))
                bg.alpha_composite(img.convert("RGBA"))
                img = bg.convert("RGB")
            else:
                img = img.convert("RGB")
            canvas = Image.new("RGB", (size, size), (22, 25, 30))
            canvas.paste(img, ((size - img.width) // 2, (size - img.height) // 2))
            canvas.save(out, "JPEG", quality=84, optimize=True)
        return out
    except Exception:
        return None


def enqueue_delete(
    root: Path,
    path: Path,
    *,
    reason: str,
    source: str,
    details: Optional[dict] = None,
) -> int:
    now = utc_now()
    normalized = normalize_path(path)
    with closing(_connect_features(root)) as conn:
        existing = conn.execute(
            "SELECT id FROM delete_queue WHERE path = ? AND status = 'queued' ORDER BY id DESC LIMIT 1",
            (normalized,),
        ).fetchone()
        if existing is not None:
            return int(existing["id"])
        cur = conn.execute(
            """
            INSERT INTO delete_queue (path, reason, source, status, created_at, updated_at, details_json)
            VALUES (?, ?, ?, 'queued', ?, ?, ?)
            """,
            (
                normalized,
                reason,
                source,
                now,
                now,
                json.dumps(details or {}, ensure_ascii=False),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def list_delete_queue(root: Path, *, status: str = "queued", limit: int = 500) -> list[DeleteQueueItem]:
    params: list[object] = []
    sql = "SELECT * FROM delete_queue"
    if status and status.lower() != "all":
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, int(limit)))

    with closing(_connect_features(root)) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        DeleteQueueItem(
            id=int(row["id"]),
            path=Path(str(row["path"])),
            reason=str(row["reason"]),
            source=str(row["source"]),
            status=str(row["status"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            details_json=str(row["details_json"] or ""),
        )
        for row in rows
    ]


def update_delete_items(root: Path, ids: Iterable[int], *, status: str) -> int:
    ids = [int(item_id) for item_id in ids]
    if not ids:
        return 0
    now = utc_now()
    with closing(_connect_features(root)) as conn:
        conn.executemany(
            "UPDATE delete_queue SET status = ?, updated_at = ? WHERE id = ?",
            [(status, now, item_id) for item_id in ids],
        )
        conn.commit()
    return len(ids)


def trash_delete_items(root: Path, ids: Iterable[int], *, log: Optional[Logger] = None) -> int:
    try:
        from send2trash import send2trash  # type: ignore
    except Exception as exc:
        raise RuntimeError("send2trash is required for safe delete queue") from exc

    ids = [int(item_id) for item_id in ids]
    if not ids:
        return 0

    trashed = 0
    now = utc_now()
    with closing(_connect_features(root)) as conn:
        rows = conn.execute(
            f"SELECT * FROM delete_queue WHERE status = 'queued' AND id IN ({','.join('?' for _ in ids)})",
            ids,
        ).fetchall()
        for row in rows:
            path = Path(str(row["path"]))
            try:
                if path.exists():
                    send2trash(str(path))
                    conn.execute(
                        "UPDATE media_files SET status = ?, updated_at = ? WHERE path = ?",
                        ("trashed", now, normalize_path(path)),
                    )
                    status = "trashed"
                    trashed += 1
                else:
                    status = "missing"
                conn.execute(
                    "UPDATE delete_queue SET status = ?, updated_at = ? WHERE id = ?",
                    (status, now, int(row["id"])),
                )
                if log is not None:
                    log(f"delete-queue: {status}: {path.name}")
            except Exception as exc:
                conn.execute(
                    "UPDATE delete_queue SET status = 'error', updated_at = ? WHERE id = ?",
                    (now, int(row["id"])),
                )
                if log is not None:
                    log(f"delete-queue: error for {path}: {exc}")
        conn.commit()
    return trashed


def export_delete_queue(root: Path, out_path: Path) -> Path:
    rows = list_delete_queue(root, status="all", limit=100000)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "path", "reason", "source", "status", "created_at", "updated_at", "details_json"])
        for row in rows:
            writer.writerow(
                [
                    row.id,
                    str(row.path),
                    row.reason,
                    row.source,
                    row.status,
                    row.created_at,
                    row.updated_at,
                    row.details_json,
                ]
            )
    return out_path


def _iter_duplicate_files(
    root: Path,
    *,
    include_nonmedia: bool,
    recursive: bool,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> list[Path]:
    candidates: list[Path] = []
    iterator = root.rglob("*") if recursive else root.glob("*")
    internal_dirs = {name.lower() for name in INTERNAL_DIR_NAMES}
    internal_files = {name.lower() for name in INTERNAL_FILE_NAMES}
    for path in iterator:
        if should_cancel is not None and should_cancel():
            raise DuplicateScanCancelled()
        if any(part.lower() in internal_dirs for part in path.parts):
            continue
        if path.name.lower() in internal_files:
            continue
        if is_media_file(path, include_nonmedia=include_nonmedia):
            candidates.append(path)
    return candidates


def _quick_fingerprint(path: Path) -> str:
    h = hashlib.blake2b(digest_size=16)
    size = path.stat().st_size
    h.update(str(size).encode("utf-8"))
    chunk = 1024 * 1024
    with path.open("rb") as f:
        first = f.read(chunk)
        h.update(first)
        if size > chunk:
            f.seek(max(0, size - chunk))
            h.update(f.read(chunk))
    return h.hexdigest()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _choose_keeper(paths: list[Path]) -> Path:
    def score(path: Path) -> tuple[int, int, float]:
        name = path.name.lower()
        duplicate_penalty = 1 if "__" in name or "__dup" in name or " (1)" in name else 0
        return duplicate_penalty, len(path.name), path.stat().st_mtime

    return sorted(paths, key=score)[0]


def scan_duplicates(
    root: Path,
    *,
    include_nonmedia: bool = False,
    recursive: bool = True,
    log: Optional[Logger] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> list[DuplicateCandidate]:
    def check_cancelled() -> None:
        if should_cancel is not None and should_cancel():
            if log is not None:
                log("duplicates: scan cancelled")
            raise DuplicateScanCancelled()

    files = _iter_duplicate_files(
        root,
        include_nonmedia=include_nonmedia,
        recursive=recursive,
        should_cancel=should_cancel,
    )
    if log is not None:
        log(f"duplicates: scanning {len(files)} media files")

    size_map: dict[int, list[Path]] = {}
    for path in files:
        check_cancelled()
        try:
            size_map.setdefault(path.stat().st_size, []).append(path)
        except Exception:
            pass

    qfp_map: dict[tuple[int, str], list[Path]] = {}
    size_groups = [group for group in size_map.values() if len(group) > 1]
    total_qfp = sum(len(group) for group in size_groups)
    done = 0
    for group in size_groups:
        for path in group:
            check_cancelled()
            done += 1
            try:
                qfp_map.setdefault((path.stat().st_size, _quick_fingerprint(path)), []).append(path)
            except Exception:
                pass
            if log is not None and done % 250 == 0:
                log(f"duplicates: quick hash {done}/{total_qfp}")

    actions: list[DuplicateCandidate] = []
    qfp_groups = [group for group in qfp_map.values() if len(group) > 1]
    total_sha = sum(len(group) for group in qfp_groups)
    done = 0
    for group in qfp_groups:
        sha_map: dict[str, list[Path]] = {}
        for path in group:
            check_cancelled()
            done += 1
            try:
                sha_map.setdefault(_sha256(path), []).append(path)
            except Exception:
                pass
            if log is not None and done % 100 == 0:
                log(f"duplicates: sha256 {done}/{total_sha}")
        for sha, paths in sha_map.items():
            if len(paths) <= 1:
                continue
            keeper = _choose_keeper(paths)
            size = keeper.stat().st_size if keeper.exists() else 0
            for path in paths:
                if path == keeper:
                    continue
                actions.append(
                    DuplicateCandidate(
                        keep=keeper,
                        remove=path,
                        reason="same_sha256",
                        group_key=sha,
                        size_bytes=size,
                    )
                )

    actions.sort(key=lambda item: (item.keep.parent.name, item.keep.name, item.remove.name))
    if log is not None:
        log(f"duplicates: found {len(actions)} duplicate removals")
    return actions


def _optional_ocr(img) -> str:
    try:
        import pytesseract  # type: ignore

        text = pytesseract.image_to_string(img)
    except Exception:
        return ""
    text = " ".join(text.split())
    return text[:1000]


def _face_count(path: Path) -> int:
    try:
        import cv2  # type: ignore

        cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
        face_cascade = cv2.CascadeClassifier(str(cascade_path))
        img = cv2.imread(str(path))
        if img is None:
            return 0
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
        return int(len(faces))
    except Exception:
        return 0


def _blur_score(path: Path) -> Optional[float]:
    try:
        import cv2  # type: ignore

        img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if img is None:
            return None
        if max(img.shape[:2]) > 900:
            scale = 900 / max(img.shape[:2])
            img = cv2.resize(img, (int(img.shape[1] * scale), int(img.shape[0] * scale)))
        return float(cv2.Laplacian(img, cv2.CV_64F).var())
    except Exception:
        return None


def analyze_light_ai(path: Path, *, bad_blur_threshold: float = 120.0) -> dict:
    from PIL import Image, ImageOps, ImageStat

    suffix = path.suffix.lower()
    name = path.name.lower()
    if suffix in VIDEO_SUFFIXES:
        return {
            "caption": f"Video file: {path.name}",
            "tags": ["video"],
            "ocr_text": "",
            "details": {"backend": "local-heuristic-v1"},
        }

    with warnings.catch_warnings():
        if hasattr(Image, "DecompressionBombWarning"):
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
        original = Image.open(path)
    with original:
        img = ImageOps.exif_transpose(original).convert("RGB")
        width, height = img.size
        small = img.resize((128, 128))
        hsv = small.convert("HSV")

    stat_rgb = ImageStat.Stat(small)
    stat_hsv = ImageStat.Stat(hsv)
    brightness = float(stat_hsv.mean[2])
    saturation = float(stat_hsv.mean[1])
    contrast = float(sum(stat_rgb.stddev) / 3.0)

    pixels = list(hsv.getdata())
    total = max(1, len(pixels))
    white_ratio = sum(1 for h, s, v in pixels if s < 35 and v > 185) / total
    dark_ratio = sum(1 for h, s, v in pixels if v < 45) / total
    green_ratio = sum(1 for h, s, v in pixels if 55 <= h <= 105 and s > 45 and v > 45) / total
    blue_ratio = sum(1 for h, s, v in pixels if 120 <= h <= 175 and s > 35 and v > 55) / total
    warm_ratio = sum(1 for h, s, v in pixels if (h <= 30 or h >= 235) and s > 45 and v > 55) / total

    aspect = width / max(1, height)
    tags: set[str] = set()

    screenshot_keywords = ("screenshot", "screen", "zrzut", "capture")
    document_keywords = ("scan", "document", "invoice", "receipt", "pdf", "doc", "notat")
    food_keywords = ("food", "meal", "dinner", "lunch", "breakfast", "pizza", "burger", "kawa")
    people_keywords = ("selfie", "portrait", "people", "person")

    if suffix == ".png" and (any(key in name for key in screenshot_keywords) or (width >= 1000 and height >= 600)):
        tags.add("screenshots")
    if any(key in name for key in document_keywords) or (white_ratio > 0.48 and saturation < 65 and contrast > 18):
        tags.add("documents")
    if any(key in name for key in food_keywords) or (warm_ratio > 0.34 and saturation > 70 and white_ratio < 0.45):
        tags.add("food")
    if (green_ratio + blue_ratio) > 0.34 and aspect >= 1.05 and "documents" not in tags:
        tags.add("landscape")

    faces = _face_count(path)
    if faces > 0 or any(key in name for key in people_keywords):
        tags.add("people")

    blur = _blur_score(path)
    if (blur is not None and blur < bad_blur_threshold) or dark_ratio > 0.58 or brightness < 35 or contrast < 11:
        tags.add("bad_photo")

    ocr_text = ""
    if "screenshots" in tags or "documents" in tags:
        try:
            with warnings.catch_warnings():
                if hasattr(Image, "DecompressionBombWarning"):
                    warnings.simplefilter("ignore", Image.DecompressionBombWarning)
                original = Image.open(path)
            with original:
                ocr_img = ImageOps.exif_transpose(original).convert("RGB")
                ocr_img.thumbnail((1600, 1600))
                ocr_text = _optional_ocr(ocr_img)
        except Exception:
            ocr_text = ""
        if ocr_text:
            tags.add("ocr_text")

    if not tags:
        tags.add("photo")

    priority = ["people", "documents", "screenshots", "landscape", "food", "bad_photo", "photo"]
    primary = next((tag for tag in priority if tag in tags), "photo")
    label = {
        "people": "Photo with people",
        "documents": "Document-like image",
        "screenshots": "Screenshot-like image",
        "landscape": "Landscape or outdoor image",
        "food": "Food-like image",
        "bad_photo": "Potential low-quality photo",
        "photo": "Photo",
    }[primary]
    tag_list = sorted(tags)
    caption = f"{label}; {width}x{height}; tags: {', '.join(tag_list)}."
    if ocr_text:
        caption += f" OCR: {ocr_text[:160]}"

    return {
        "caption": caption,
        "tags": tag_list,
        "ocr_text": ocr_text,
        "details": {
            "backend": "local-heuristic-v1",
            "width": width,
            "height": height,
            "brightness": round(brightness, 2),
            "saturation": round(saturation, 2),
            "contrast": round(contrast, 2),
            "blur_score": None if blur is None else round(blur, 2),
            "faces": faces,
        },
    }


def upsert_ai_metadata(root: Path, path: Path, result: dict) -> None:
    now = utc_now()
    with closing(_connect_features(root)) as conn:
        conn.execute(
            """
            INSERT INTO ai_metadata (
                path, caption, tags_json, embedding_model, embedding_dim, embedding,
                ocr_text, backend, details_json, updated_at
            )
            VALUES (?, ?, ?, NULL, NULL, NULL, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                caption=excluded.caption,
                tags_json=excluded.tags_json,
                ocr_text=excluded.ocr_text,
                backend=excluded.backend,
                details_json=excluded.details_json,
                updated_at=excluded.updated_at
            """,
            (
                normalize_path(path),
                str(result.get("caption", "")),
                json.dumps(result.get("tags", []), ensure_ascii=False),
                str(result.get("ocr_text", "")),
                str(result.get("details", {}).get("backend", "local-heuristic-v1")),
                json.dumps(result.get("details", {}), ensure_ascii=False),
                now,
            ),
        )
        conn.commit()


def run_light_ai(
    root: Path,
    *,
    limit: int = 100,
    only_missing: bool = True,
    bad_blur_threshold: float = 120.0,
    log: Optional[Logger] = None,
) -> int:
    suffix_placeholders = ",".join("?" for _ in IMAGE_SUFFIXES)
    params: list[object] = list(IMAGE_SUFFIXES)
    sql = f"""
        SELECT mf.path
        FROM media_files mf
        LEFT JOIN ai_metadata ai ON ai.path = mf.path
        WHERE mf.status = 'present'
          AND lower(mf.suffix) IN ({suffix_placeholders})
    """
    if only_missing:
        sql += " AND ai.path IS NULL"
    sql += " ORDER BY mf.mtime_epoch DESC LIMIT ?"
    params.append(max(1, int(limit)))

    with closing(_connect_features(root)) as conn:
        paths = [Path(str(row["path"])) for row in conn.execute(sql, params).fetchall()]

    processed = 0
    for path in paths:
        if not path.exists():
            continue
        try:
            result = analyze_light_ai(path, bad_blur_threshold=bad_blur_threshold)
            upsert_ai_metadata(root, path, result)
            processed += 1
            if log is not None and processed % 25 == 0:
                log(f"light-ai: processed {processed}/{len(paths)}")
        except Exception as exc:
            if log is not None:
                log(f"light-ai: skipped {path.name}: {exc}")
    if log is not None:
        log(f"light-ai: processed {processed} files")
    return processed


def search_ai_metadata(root: Path, *, search: str = "", limit: int = 300) -> list[dict]:
    params: list[object] = []
    sql = """
        SELECT mf.path, mf.relative_path, mf.year, ai.caption, ai.tags_json, ai.ocr_text, ai.backend, ai.updated_at
        FROM ai_metadata ai
        LEFT JOIN media_files mf ON mf.path = ai.path
    """
    if search:
        like = f"%{search.lower()}%"
        sql += """
            WHERE lower(COALESCE(ai.caption, '')) LIKE ?
               OR lower(COALESCE(ai.tags_json, '')) LIKE ?
               OR lower(COALESCE(ai.ocr_text, '')) LIKE ?
               OR lower(COALESCE(mf.relative_path, ai.path)) LIKE ?
        """
        params.extend([like, like, like, like])
    sql += " ORDER BY ai.updated_at DESC LIMIT ?"
    params.append(max(1, int(limit)))

    with closing(_connect_features(root)) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def human_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{value} B"
