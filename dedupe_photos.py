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
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Iterable

MEDIA_EXTS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp",
    ".heic", ".heif",
    ".mp4", ".mov", ".m4v", ".avi", ".mkv", ".3gp",
}
TEMP_NAME_HINTS = (".tmp", ".part", ".crdownload")

YEAR_DIR_RE = re.compile(r"^(19|20)\d{2}$")


def is_media_file(p: Path, include_nonmedia: bool) -> bool:
    if not p.is_file():
        return False
    name = p.name.lower()
    if name.endswith(TEMP_NAME_HINTS):
        return False
    if include_nonmedia:
        return True
    return p.suffix.lower() in MEDIA_EXTS


def iter_files(root: Path, recursive: bool) -> Iterable[Path]:
    if recursive:
        yield from (p for p in root.rglob("*") if p.is_file())
    else:
        yield from (p for p in root.iterdir() if p.is_file())


def progress_line(prefix: str, i: int, n: int, current: str) -> None:
    width = 28
    done = int(width * (i / max(1, n)))
    bar = "█" * done + "░" * (width - done)
    msg = f"{prefix} [{bar}] {i}/{n}  {current}"
    if len(msg) > 140:
        msg = msg[:137] + "..."
    sys.stdout.write("\r" + msg.ljust(140))
    sys.stdout.flush()
    if i == n:
        sys.stdout.write("\n")


def quick_fingerprint(path: Path) -> str:
    """
    Fast fingerprint: BLAKE2b of (size + first 1MB + last 1MB).
    Good for grouping likely duplicates quickly.
    """
    h = hashlib.blake2b(digest_size=16)
    size = path.stat().st_size
    h.update(str(size).encode("utf-8"))

    chunk = 1024 * 1024
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


def safe_move_path(dst_dir: Path, filename: str) -> Path:
    """
    Create a destination path that won't overwrite existing files.
    """
    dst_dir.mkdir(parents=True, exist_ok=True)
    base = Path(filename).stem
    ext = Path(filename).suffix
    cand = dst_dir / (base + ext)
    if not cand.exists():
        return cand
    i = 1
    while True:
        cand = dst_dir / f"{base}__dup{i}{ext}"
        if not cand.exists():
            return cand
        i += 1


@dataclass
class DupeAction:
    keep: Path
    remove: Path
    reason: str  # e.g. "same_sha256"
    group_key: str


def choose_keeper(paths: List[Path]) -> Path:
    """
    Choose which file to keep among duplicates.
    Heuristic:
      1) Prefer file without '__' or '__dup' suffix in name
      2) Prefer shortest name
      3) Prefer older modified time (often original)
    """
    def score(p: Path) -> Tuple[int, int, float]:
        name = p.name.lower()
        penalty = 1 if "__" in name else 0
        dup_penalty = 1 if "__dup" in name else 0
        # smaller is better
        return (penalty + dup_penalty, len(p.name), p.stat().st_mtime)

    return sorted(paths, key=score)[0]


def list_year_dirs(root: Path, years: Optional[List[str]]) -> List[Path]:
    if years:
        dirs = [(root / y) for y in years]
        return [d for d in dirs if d.exists() and d.is_dir()]
    # autodetect YYYY directories in root
    out = []
    for p in root.iterdir():
        if p.is_dir() and YEAR_DIR_RE.match(p.name):
            out.append(p)
    return sorted(out, key=lambda x: x.name)


def scan_with_progress(dirs: List[Path], include_nonmedia: bool, recursive: bool) -> List[Path]:
    # First pass: count
    print("Counting files...")
    total = 0
    for d in dirs:
        for p in iter_files(d, recursive=recursive):
            if is_media_file(p, include_nonmedia):
                total += 1

    print(f"Scanning {len(dirs)} year folders, total candidates: {total}")
    files: List[Path] = []
    i = 0
    t0 = time.time()

    for d in dirs:
        for p in iter_files(d, recursive=recursive):
            if not is_media_file(p, include_nonmedia):
                continue
            files.append(p)
            i += 1
            if i % 250 == 0:
                dt = time.time() - t0
                progress_line("scan", i, total, f"{p.parent.name}/{p.name} | {dt:.1f}s")

    progress_line("scan", i, total, "done")
    return files


def build_dupe_actions(files: List[Path], full_hash: bool) -> Tuple[List[DupeAction], Dict[str, List[Path]]]:
    """
    Returns dupe actions + map of groups for reporting.
    Deduping by content:
      - Group by (size + quick fingerprint)
      - Confirm by sha256 (optional always for groups>1)
    """
    # Group by size first (cheap)
    size_map: Dict[int, List[Path]] = {}
    for p in files:
        try:
            size_map.setdefault(p.stat().st_size, []).append(p)
        except Exception:
            pass

    # For each size group with >1, compute quick fingerprint
    qfp_map: Dict[Tuple[int, str], List[Path]] = {}
    candidates = [g for g in size_map.values() if len(g) > 1]
    print(f"Potential duplicate size-groups: {len(candidates)}")

    # compute quick fp
    total = sum(len(g) for g in candidates)
    i = 0
    t0 = time.time()
    for group in candidates:
        for p in group:
            i += 1
            if i % 200 == 0:
                dt = time.time() - t0
                progress_line("qfp ", i, total, f"{p.parent.name}/{p.name} | {dt:.1f}s")
            try:
                q = quick_fingerprint(p)
                qfp_map.setdefault((p.stat().st_size, q), []).append(p)
            except Exception:
                pass
    if total:
        progress_line("qfp ", total, total, "done")

    # Now confirm duplicates by sha256 inside qfp groups > 1
    dupe_actions: List[DupeAction] = []
    confirmed_groups: Dict[str, List[Path]] = {}

    confirm_groups = [g for g in qfp_map.values() if len(g) > 1]
    print(f"Quick-fingerprint groups with >1: {len(confirm_groups)}")

    total2 = sum(len(g) for g in confirm_groups)
    j = 0
    t1 = time.time()

    for group in confirm_groups:
        # hash per file
        sha_map: Dict[str, List[Path]] = {}
        for p in group:
            j += 1
            if j % 100 == 0:
                dt = time.time() - t1
                progress_line("sha ", j, total2, f"{p.parent.name}/{p.name} | {dt:.1f}s")
            try:
                # Always confirm with sha256 for safety
                s = sha256(p) if (full_hash or True) else quick_fingerprint(p)
                sha_map.setdefault(s, []).append(p)
            except Exception:
                pass

        # for each sha group with >1 -> duplicates
        for sha, paths in sha_map.items():
            if len(paths) <= 1:
                continue
            keeper = choose_keeper(paths)
            confirmed_groups[sha] = paths
            for p in paths:
                if p == keeper:
                    continue
                dupe_actions.append(DupeAction(
                    keep=keeper,
                    remove=p,
                    reason="same_sha256",
                    group_key=sha
                ))

    if total2:
        progress_line("sha ", total2, total2, "done")

    return dupe_actions, confirmed_groups


def write_log(root: Path, actions: List[DupeAction], out_name: str) -> Path:
    log_path = root / out_name
    with log_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["keep", "remove", "reason", "group_key"])
        for a in actions:
            w.writerow([str(a.keep), str(a.remove), a.reason, a.group_key])
    return log_path


def execute(actions: List[DupeAction], root: Path, quarantine_dir: Path, delete: bool, dry_run: bool) -> None:
    if not actions:
        print("No duplicates found ✅")
        return

    print(f"Duplicates to handle: {len(actions)}")
    if dry_run:
        print("DRY-RUN: not moving/deleting anything.")
        return

    if delete:
        confirm = input("You are about to DELETE duplicates permanently. Type 'yes' to continue: ").strip().lower()
        if confirm != "yes":
            print("Aborted. Nothing deleted.")
            return

    # Move or delete
    n = len(actions)
    for i, a in enumerate(actions, start=1):
        progress_line("exec", i, n, a.remove.name)
        try:
            if delete:
                a.remove.unlink()
            else:
                # preserve year folder name inside quarantine for clarity
                rel_year = a.remove.parent.name
                target_dir = quarantine_dir / rel_year
                target = safe_move_path(target_dir, a.remove.name)
                shutil.move(str(a.remove), str(target))
        except Exception as e:
            print(f"\nWARNING: could not process {a.remove}: {e}")

    print("\nDone.")


def main():
    parser = argparse.ArgumentParser(
        description="Deduplicate photos/videos inside year folders by content (sha256)."
    )
    parser.add_argument("--root", default=".", help="Root folder that contains year folders (e.g. 2017, 2018...).")
    parser.add_argument("--years", nargs="*", help="Optional list of years to process, e.g. --years 2017 2018")
    parser.add_argument("--recursive", action="store_true", help="Also scan subfolders inside year directories.")
    parser.add_argument("--include-nonmedia", action="store_true", help="Also include non-media files.")
    parser.add_argument("--dry-run", action="store_true", help="Only detect and report duplicates.")
    parser.add_argument("--delete", action="store_true", help="DELETE duplicates instead of moving to quarantine (danger).")
    parser.add_argument("--quarantine", default="_DUPLICATES", help="Folder (under root) where duplicates will be moved.")
    parser.add_argument("--full-hash", action="store_true", help="(kept for future) Always uses sha256 anyway for safety.")
    parser.add_argument("--log", default="dedupe_log.csv", help="CSV log filename saved in root.")

    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    if not root.exists():
        print(f"ERROR: root not found: {root}")
        sys.exit(2)

    year_dirs = list_year_dirs(root, args.years)
    if not year_dirs:
        print("No year folders found. Expected folders like 2017, 2018, ... in root.")
        sys.exit(2)

    print(f"Root: {root}")
    print(f"Year folders: {', '.join(d.name for d in year_dirs)}")
    print(f"Recursive: {args.recursive}")
    print(f"Mode: {'DELETE' if args.delete else 'QUARANTINE_MOVE'} | {'DRY-RUN' if args.dry_run else 'APPLY'}")

    files = scan_with_progress(year_dirs, include_nonmedia=args.include_nonmedia, recursive=args.recursive)

    dupe_actions, _ = build_dupe_actions(files, full_hash=args.full_hash)
    log_path = write_log(root, dupe_actions, args.log)
    print(f"Log saved: {log_path}")

    quarantine_dir = (root / args.quarantine).resolve()
    if not args.delete:
        quarantine_dir.mkdir(parents=True, exist_ok=True)

    execute(dupe_actions, root, quarantine_dir, delete=args.delete, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
