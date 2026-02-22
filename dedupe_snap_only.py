#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import csv
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

TARGET_ROOT_FOLDERS = ("snapchat", "snapshots")

# Rozpoznawanie suffixów duplikatów:
#  foo_1.jpg, foo__2.mp4, foo (1).jpg
DUP_PATTERNS = [
    re.compile(r"^(?P<base>.+)_(?P<num>\d+)$"),
    re.compile(r"^(?P<base>.+)__(?P<num>\d+)$"),
    re.compile(r"^(?P<base>.+)\s*\((?P<num>\d+)\)$"),
]


@dataclass
class Action:
    keep: Path
    remove: Path
    reason: str


def find_target_dirs(root: Path) -> List[Path]:
    existing = {p.name.lower(): p for p in root.iterdir() if p.is_dir()}
    out = []
    for name in TARGET_ROOT_FOLDERS:
        if name in existing:
            out.append(existing[name])
    return out


def safe_move_path(dst_dir: Path, filename: str) -> Path:
    dst_dir.mkdir(parents=True, exist_ok=True)
    cand = dst_dir / filename
    if not cand.exists():
        return cand
    base = Path(filename).stem
    ext = Path(filename).suffix
    i = 1
    while True:
        cand = dst_dir / f"{base}__nmdup{i}{ext}"
        if not cand.exists():
            return cand
        i += 1


def stem_base_from_dup(stem: str) -> Tuple[str, str] | Tuple[None, None]:
    for pat in DUP_PATTERNS:
        m = pat.match(stem)
        if m:
            return m.group("base"), m.group("num")
    return None, None


def build_actions_for_folder(folder: Path, include_subfolders: bool, prefer_base: bool = True) -> List[Action]:
    # Zbieramy wszystkie pliki (rekurencyjnie jeśli trzeba)
    files = list(folder.rglob("*") if include_subfolders else folder.glob("*"))
    files = [p for p in files if p.is_file()]

    # Map: (dir, lower_filename) -> Path
    by_name: Dict[Tuple[Path, str], Path] = {(p.parent, p.name.lower()): p for p in files}

    actions: List[Action] = []

    for p in files:
        base_stem, num = stem_base_from_dup(p.stem)
        if not base_stem:
            continue

        # Szukamy "bazowego" pliku w tym samym katalogu
        base_name = (base_stem + p.suffix).lower()
        base_key = (p.parent, base_name)
        base_path = by_name.get(base_key)

        if base_path and base_path.exists():
            # Mamy bazę -> usuń duplikat po nazwie
            actions.append(Action(keep=base_path, remove=p, reason=f"name_dup_suffix_{num}"))
            continue

        # Opcjonalnie: jeśli nie ma bazy, ale są inne duplikaty, możemy zostawić najniższy numer
        # Tu robimy to tylko gdy prefer_base=False (domyślnie True, bo Snapchat zwykle ma bazę)
        if not prefer_base:
            # nie ruszamy bez bazy
            pass

    # Dedup listy (na wypadek powtórek)
    uniq = {}
    for a in actions:
        uniq[str(a.remove)] = a
    return list(uniq.values())


def write_log(root: Path, actions: List[Action], name: str) -> Path:
    out = root / name
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["keep", "remove", "reason"])
        for a in actions:
            w.writerow([str(a.keep), str(a.remove), a.reason])
    return out


def apply_actions(actions: List[Action], root: Path, quarantine: Path, delete: bool, dry_run: bool) -> None:
    print(f"Planned removals: {len(actions)}")
    if dry_run:
        print("DRY-RUN: nic nie przenoszę/nie usuwam.")
        return

    if not delete:
        quarantine.mkdir(parents=True, exist_ok=True)

    for i, a in enumerate(actions, start=1):
        sys.stdout.write(f"\r{i}/{len(actions)}  {a.remove.name}   ")
        sys.stdout.flush()
        try:
            if delete:
                a.remove.unlink()
            else:
                # zachowaj źródłowy top-folder (Snapchat / SnapShots) w kwarantannie
                top = next((p for p in a.remove.parents if p.parent == root), None)
                top_name = top.name if top else "UNKNOWN"
                target_dir = quarantine / top_name / a.remove.parent.relative_to(top)
                target = safe_move_path(target_dir, a.remove.name)
                shutil.move(str(a.remove), str(target))
        except Exception as e:
            print(f"\nWARNING: nie udało się ruszyć {a.remove}: {e}")

    print("\nDone.")


def main():
    ap = argparse.ArgumentParser(
        description="Sprząta tylko w root/Snapchat i root/SnapShots: usuwa *_1, *_2, *(1) itd, jeśli istnieje plik bazowy."
    )
    ap.add_argument("--root", default=".", help="Folder Photos, w którym są Snapchat i SnapShots.")
    ap.add_argument("--recursive", action="store_true", help="Skanuj także podfoldery.")
    ap.add_argument("--dry-run", action="store_true", help="Tylko pokaż w logu co by było usunięte.")
    ap.add_argument("--delete", action="store_true", help="Usuń na stałe (domyślnie przenosi do kwarantanny).")
    ap.add_argument("--quarantine", default="_DUPLICATES_NAME", help="Folder kwarantanny pod root.")
    ap.add_argument("--log", default="dedupe_snap_name_log.csv", help="Nazwa loga CSV.")
    args = ap.parse_args()

    root = Path(args.root).expanduser().resolve()
    if not root.exists():
        print(f"ERROR: root nie istnieje: {root}")
        raise SystemExit(2)

    targets = find_target_dirs(root)
    if not targets:
        print("ERROR: nie znalazłem folderów Snapchat/SnapShots w root.")
        raise SystemExit(2)

    all_actions: List[Action] = []
    for t in targets:
        acts = build_actions_for_folder(t, include_subfolders=args.recursive, prefer_base=True)
        all_actions.extend(acts)

    log_path = write_log(root, all_actions, args.log)
    print(f"Log saved: {log_path}")

    quarantine = root / args.quarantine
    apply_actions(all_actions, root, quarantine, delete=args.delete, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
