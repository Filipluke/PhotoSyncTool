#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import csv
import hashlib
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Tuple, Iterable

# ---------------- EXIF (optional via Pillow) ----------------
def try_get_year_from_exif(path: Path) -> Optional[int]:
    """
    Attempts to read DateTimeOriginal from EXIF using Pillow (if available).
    Returns year or None.
    """
    try:
        from PIL import Image, ExifTags  # type: ignore
    except Exception:
        return None

    try:
        with Image.open(path) as img:
            exif = getattr(img, "_getexif", None)
            if not exif:
                return None
            exif_data = exif()
            if not exif_data:
                return None

            tag_map = {v: k for k, v in ExifTags.TAGS.items()}
            dto_key = tag_map.get("DateTimeOriginal")
            if not dto_key:
                return None

            dto = exif_data.get(dto_key)
            if not dto:
                return None

            # Typical format: "2019:07:21 12:34:56"
            m = re.match(r"^\s*(\d{4}):(\d{2}):(\d{2})\s+(\d{2}):(\d{2}):(\d{2})\s*$", str(dto))
            if not m:
                return None

            year = int(m.group(1))
            if 1900 <= year <= 2100:
                return year
            return None
    except Exception:
        return None


def detect_year(path: Path, date_source: str = "exif") -> int:
    """
    date_source:
      - exif: try EXIF DateTimeOriginal, fallback to mtime
      - mtime: use modified time (Windows "Zmodyfikowany")
      - ctime: use ctime (Windows often "Utworzony", on Unix metadata change time)
    """
    st = path.stat()

    if date_source == "exif":
        y = try_get_year_from_exif(path)
        if y is not None:
            return y
        return datetime.fromtimestamp(st.st_mtime).year

    if date_source == "mtime":
        return datetime.fromtimestamp(st.st_mtime).year

    return datetime.fromtimestamp(st.st_ctime).year


# ---------------- Verification / hashing ----------------
def quick_fingerprint(path: Path) -> str:
    """
    Fast fingerprint: BLAKE2b of (size + first 1MB + last 1MB).
    Great for verifying copies quickly.
    """
    h = hashlib.blake2b(digest_size=16)
    size = path.stat().st_size
    h.update(str(size).encode("utf-8"))

    chunk = 1024 * 1024  # 1MB
    with path.open("rb") as f:
        first = f.read(chunk)
        h.update(first)
        if size > chunk:
            try:
                f.seek(max(0, size - chunk), os.SEEK_SET)
                last = f.read(chunk)
                h.update(last)
            except Exception:
                pass
    return h.hexdigest()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def verify_copy(src: Path, dst: Path, full_hash: bool = False) -> Tuple[bool, str]:
    if not dst.exists():
        return False, "dst_missing"
    if src.stat().st_size != dst.stat().st_size:
        return False, "size_mismatch"

    if full_hash:
        return (sha256(src) == sha256(dst)), "sha256"
    return (quick_fingerprint(src) == quick_fingerprint(dst)), "quick_fp"


# ---------------- Media detection ----------------
MEDIA_EXTS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp",
    ".heic", ".heif",
    ".mp4", ".mov", ".m4v", ".avi", ".mkv", ".3gp",
}

TEMP_NAME_HINTS = (".tmp", ".part", ".crdownload")  # ignore common partial downloads


def is_media_file(p: Path, include_nonmedia: bool) -> bool:
    if not p.is_file():
        return False
    name = p.name.lower()
    if name.endswith(TEMP_NAME_HINTS):
        return False
    if include_nonmedia:
        return True
    return p.suffix.lower() in MEDIA_EXTS


# ---------------- Copy planning ----------------
def safe_destination_path(dst_dir: Path, filename: str) -> Path:
    """
    If file exists, create unique name by appending __1, __2, ...
    """
    dst_dir.mkdir(parents=True, exist_ok=True)
    base = Path(filename).stem
    ext = Path(filename).suffix
    candidate = dst_dir / (base + ext)
    if not candidate.exists():
        return candidate
    i = 1
    while True:
        candidate = dst_dir / f"{base}__{i}{ext}"
        if not candidate.exists():
            return candidate
        i += 1


@dataclass
class CopyAction:
    src: Path
    dsts: List[Path]
    year: int
    flags: List[str]


def plan_action(root: Path, src: Path, date_source: str) -> CopyAction:
    name_lower = src.name.lower()
    flags: List[str] = []
    if "snapshot" in name_lower:
        flags.append("snapshot")
    if "snapchat" in name_lower:
        flags.append("snapchat")

    year = detect_year(src, date_source=date_source)
    year_dir = (root / str(year)).resolve()

    snapshots_dir = (root / "SnapShots").resolve()
    snapchat_dir = (root / "Snapchat").resolve()

    dsts = [safe_destination_path(year_dir, src.name)]
    if "snapshot" in flags:
        dsts.append(safe_destination_path(snapshots_dir, src.name))
    if "snapchat" in flags:
        dsts.append(safe_destination_path(snapchat_dir, src.name))

    return CopyAction(src=src, dsts=dsts, year=year, flags=flags)


def iter_source_files(source: Path, recursive: bool) -> Iterable[Path]:
    if recursive:
        yield from (p for p in source.rglob("*") if p.is_file())
    else:
        yield from (p for p in source.iterdir() if p.is_file())


def progress_line(i: int, n: int, current: str) -> None:
    width = 28
    done = int(width * (i / max(1, n)))
    bar = "█" * done + "░" * (width - done)
    msg = f"[{bar}] {i}/{n}  {current}"
    if len(msg) > 120:
        msg = msg[:117] + "..."
    sys.stdout.write("\r" + msg.ljust(120))
    sys.stdout.flush()
    if i == n:
        sys.stdout.write("\n")


def ensure_file_stable(path: Path, settle_seconds: float, stable_checks: int, poll_interval: float) -> bool:
    """
    Wait until file size doesn't change for `stable_checks` consecutive polls.
    Additionally requires file age >= settle_seconds since last modification.
    Returns False if file disappears.
    """
    stable = 0
    last_size = None
    start = time.time()

    while True:
        if not path.exists():
            return False

        try:
            st = path.stat()
            size = st.st_size
            mtime = st.st_mtime
        except Exception:
            time.sleep(poll_interval)
            continue

        if last_size is not None and size == last_size:
            stable += 1
        else:
            stable = 0
            last_size = size

        if stable >= stable_checks:
            age = time.time() - mtime
            if age >= settle_seconds:
                return True

        time.sleep(poll_interval)

        # safety: don't hang forever
        if time.time() - start > 60 * 30:  # 30 min
            return stable > 0


def scan_source_with_progress(source: Path, recursive: bool, include_nonmedia: bool) -> List[Path]:
    """
    Scans source for files (optionally recursive) and returns media-matching files.
    Shows progress during scanning (useful for OneDrive).
    """
    print(f"Scanning source: {source} (recursive={recursive}) ...")
    files: List[Path] = []
    seen = 0
    matched = 0
    t0 = time.time()
    last_print = 0.0

    for p in iter_source_files(source, recursive=recursive):
        seen += 1
        if is_media_file(p, include_nonmedia=include_nonmedia):
            files.append(p)
            matched += 1

        # update every 0.5s (time-based so it feels responsive)
        now = time.time()
        if now - last_print >= 0.5:
            dt = now - t0
            sys.stdout.write(f"\rScanned: {seen} items | matched: {matched} | elapsed: {dt:.1f}s")
            sys.stdout.flush()
            last_print = now

    dt = time.time() - t0
    sys.stdout.write(f"\rScanned: {seen} items | matched: {matched} | elapsed: {dt:.1f}s\n")
    sys.stdout.flush()
    return files


def execute_actions(actions: List[CopyAction], root: Path, full_hash: bool, dry_run: bool) -> Tuple[bool, Path]:
    log_path = root / "sort_photos_log.csv"
    copied_records = []

    n = len(actions)
    for i, act in enumerate(actions, start=1):
        progress_line(i, n, act.src.name)
        for dst in act.dsts:
            if dry_run:
                copied_records.append((str(act.src), str(dst), act.year, ",".join(act.flags), "dry_run"))
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(act.src, dst)
            copied_records.append((str(act.src), str(dst), act.year, ",".join(act.flags), "copied"))

    try:
        with log_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["src", "dst", "year", "flags", "status"])
            w.writerows(copied_records)
    except Exception as e:
        print(f"\nWARNING: Could not write log {log_path}: {e}")

    if dry_run:
        print("\nDry-run finished. Nothing copied/deleted.")
        return True, log_path

    print("\nVerifying copies...")
    all_ok = True
    failed = []

    for i, act in enumerate(actions, start=1):
        progress_line(i, n, f"verify: {act.src.name}")
        for dst in act.dsts:
            ok, method = verify_copy(act.src, dst, full_hash=full_hash)
            if not ok:
                all_ok = False
                failed.append((act.src, dst, method))

    if not all_ok:
        print("\nERROR: Verification failed. Originals will NOT be deleted.")
        for src, dst, method in failed[:30]:
            print(f" - {src} -> {dst} (method={method})")
        if len(failed) > 30:
            print(f" ... and {len(failed)-30} more.")
    else:
        print("\nAll copies verified OK ✅")

    return all_ok, log_path


def delete_originals(actions: List[CopyAction], recursive: bool) -> None:
    n = len(actions)
    print("\nDeleting originals...")
    for i, act in enumerate(actions, start=1):
        progress_line(i, n, f"delete: {act.src.name}")
        try:
            act.src.unlink()
        except Exception as e:
            print(f"\nWARNING: Could not delete {act.src}: {e}")

    # Optional cleanup: remove empty directories (only if recursive)
    if recursive:
        dirs = sorted({a.src.parent for a in actions}, key=lambda p: len(str(p)), reverse=True)
        for d in dirs:
            try:
                if d.exists() and d.is_dir() and not any(d.iterdir()):
                    d.rmdir()
            except Exception:
                pass

    print("\nDone. Originals deleted.")


# ---------------- WATCH MODE ----------------
def run_watch_mode(
    root: Path,
    source: Path,
    date_source: str,
    full_hash: bool,
    include_nonmedia: bool,
    recursive: bool,
    settle_seconds: float,
    stable_checks: int,
    poll_interval: float,
    dry_run: bool,
    watch_delete: bool,
) -> None:
    try:
        from watchdog.observers import Observer  # type: ignore
        from watchdog.events import FileSystemEventHandler  # type: ignore
    except Exception:
        print("ERROR: watchdog is not installed. Run: pip install watchdog")
        sys.exit(2)

    print(f"Watching: {source}  (recursive={recursive})")
    print(f"Watch delete: {'ON' if watch_delete else 'OFF'}")
    print("Press Ctrl+C to stop.\n")

    class Handler(FileSystemEventHandler):
        def on_created(self, event):
            if getattr(event, "is_directory", False):
                return
            self._handle(Path(event.src_path))

        def on_moved(self, event):
            if getattr(event, "is_directory", False):
                return
            dst = getattr(event, "dest_path", None)
            if dst:
                self._handle(Path(dst))
            else:
                self._handle(Path(event.src_path))

        def _handle(self, path: Path):
            try:
                path = path.resolve()
            except Exception:
                return

            if source not in path.parents and path != source:
                return

            if not is_media_file(path, include_nonmedia=include_nonmedia):
                return

            ok = ensure_file_stable(
                path,
                settle_seconds=settle_seconds,
                stable_checks=stable_checks,
                poll_interval=poll_interval,
            )
            if not ok:
                return

            try:
                action = plan_action(root, path, date_source=date_source)
            except Exception as e:
                print(f"\n[watch] Could not plan {path.name}: {e}")
                return

            try:
                # copy
                for dst in action.dsts:
                    if dry_run:
                        print(f"[watch][dry-run] {path.name} -> {dst}")
                    else:
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(path, dst)

                if dry_run:
                    return

                # verify
                for dst in action.dsts:
                    ok2, _ = verify_copy(path, dst, full_hash=full_hash)
                    if not ok2:
                        print(f"\n[watch] VERIFY FAILED: {path} -> {dst}")
                        return

                msg = f"[watch] OK: {path.name} -> year {action.year}"
                if "snapshot" in action.flags:
                    msg += " + SnapShots"
                if "snapchat" in action.flags:
                    msg += " + Snapchat"
                print(msg)

                # delete source if enabled
                if watch_delete:
                    try:
                        path.unlink()
                        print(f"[watch] DELETED source: {path.name}")
                    except Exception as e:
                        print(f"[watch] WARNING: could not delete {path}: {e}")

            except Exception as e:
                print(f"\n[watch] Error copying {path.name}: {e}")

    observer = Observer()
    handler = Handler()
    observer.schedule(handler, str(source), recursive=recursive)
    observer.start()
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping watch...")
    finally:
        observer.stop()
        observer.join()


# ---------------- MAIN ----------------
def main():
    parser = argparse.ArgumentParser(
        description="Organize photos/videos from Sorting folder into year folders + SnapShots/Snapchat duplicates."
    )
    parser.add_argument("--root", type=str, default=".", help="Root folder containing year folders, SnapShots, Snapchat, Sorting folder.")
    parser.add_argument("--source", type=str, default="Sorting folder", help="Input sorting folder (relative to --root if not absolute).")

    parser.add_argument(
        "--date-source",
        choices=["exif", "mtime", "ctime"],
        default="exif",
        help="Which timestamp to use for year: exif (photos) fallback->mtime, mtime (modified), ctime (created/metadata)."
    )

    parser.add_argument("--full-hash", action="store_true", help="Use full SHA256 for verification (slower).")
    parser.add_argument("--dry-run", action="store_true", help="Do not copy/delete, only show what would happen.")
    parser.add_argument("--include-nonmedia", action="store_true", help="Also process non-media files.")
    parser.add_argument("--no-recursive", action="store_true", help="Disable recursive processing of subfolders in Sorting folder.")

    # watch mode
    parser.add_argument("--watch", action="store_true", help="Watch the sorting folder and auto-sort new files (requires watchdog).")
    parser.add_argument("--watch-delete", action="store_true", help="In watch mode: delete source file AFTER successful copy and verification.")
    parser.add_argument("--settle-seconds", type=float, default=1.5, help="In watch mode: minimum age (seconds) since last modification.")
    parser.add_argument("--stable-checks", type=int, default=3, help="In watch mode: how many consecutive unchanged size checks.")
    parser.add_argument("--poll-interval", type=float, default=0.5, help="In watch mode: polling interval for stability check.")

    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    source = Path(args.source)
    if not source.is_absolute():
        source = (root / source).resolve()

    recursive = not args.no_recursive

    if not source.exists():
        print(f"ERROR: Source folder not found: {source}")
        sys.exit(2)

    # Watch mode (continuous)
    if args.watch:
        run_watch_mode(
            root=root,
            source=source,
            date_source=args.date_source,
            full_hash=args.full_hash,
            include_nonmedia=args.include_nonmedia,
            recursive=recursive,
            settle_seconds=args.settle_seconds,
            stable_checks=args.stable_checks,
            poll_interval=args.poll_interval,
            dry_run=args.dry_run,
            watch_delete=args.watch_delete,
        )
        return

    # Batch mode (one-shot) with scanning progress
    files = scan_source_with_progress(source, recursive=recursive, include_nonmedia=args.include_nonmedia)
    if not files:
        print("No files to process.")
        return

    actions = [plan_action(root, p, date_source=args.date_source) for p in files]

    print(f"\nRoot:   {root}")
    print(f"Source: {source} (recursive={recursive})")
    print(f"Files to process: {len(actions)}")
    print(f"Mode: {'DRY-RUN' if args.dry_run else 'COPY'} | Verification: {'SHA256(full)' if args.full_hash else 'Quick fingerprint'}")
    print(f"Date source: {args.date_source} (exif falls back to mtime)")
    print()

    ok, log_path = execute_actions(actions, root, full_hash=args.full_hash, dry_run=args.dry_run)

    if args.dry_run:
        print(f"Planned log: {log_path}")
        return

    if not ok:
        print(f"\nSee log: {log_path}")
        sys.exit(1)

    print(f"Log saved: {log_path}")

    answer = input(f"\nDelete originals from '{source}'? Type 'yes' to confirm: ").strip().lower()
    if answer != "yes":
        print("Not deleting anything.")
        return

    delete_originals(actions, recursive=recursive)


if __name__ == "__main__":
    main()
