# remove_name_duplicates.py
# Usuwa duplikaty nazw: *_1, *_2, ... przed rozszerzeniem w podanych folderach.

import argparse
import re
from pathlib import Path

DUP_RE = re.compile(r"^(?P<base>.+?)_(?P<num>\d+)$")  # bazowa_nazwa + _liczba

def is_name_duplicate(stem: str, allowed_nums: set[int] | None) -> bool:
    m = DUP_RE.match(stem)
    if not m:
        return False
    n = int(m.group("num"))
    return True if allowed_nums is None else (n in allowed_nums)

def iter_files(folder: Path, recursive: bool):
    if recursive:
        yield from (p for p in folder.rglob("*") if p.is_file())
    else:
        yield from (p for p in folder.glob("*") if p.is_file())

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--folders", nargs="+", required=True, help="Foldery do skanowania")
    ap.add_argument("--recursive", action="store_true", help="Skanuj podfoldery")
    ap.add_argument("--delete", action="store_true", help="FAKTYCZNIE usuń (bez tego jest tylko podgląd)")
    ap.add_argument("--nums", default="1,2", help="Które sufiksy usuwać, np. 1,2 albo 1-9 albo 'all'")
    ap.add_argument("--ext", default="all", help="Filtr rozszerzeń: np. jpg,png,mp4 albo 'all'")
    args = ap.parse_args()

    # nums parsing
    allowed_nums = None
    nums_s = args.nums.strip().lower()
    if nums_s != "all":
        allowed_nums = set()
        parts = [x.strip() for x in nums_s.split(",") if x.strip()]
        for part in parts:
            if "-" in part:
                a, b = part.split("-", 1)
                a, b = int(a), int(b)
                allowed_nums.update(range(min(a, b), max(a, b) + 1))
            else:
                allowed_nums.add(int(part))

    # ext parsing
    allowed_ext = None
    ext_s = args.ext.strip().lower()
    if ext_s != "all":
        allowed_ext = {("." + e.lstrip(".")).lower() for e in ext_s.split(",") if e.strip()}

    to_remove: list[Path] = []
    scanned = 0

    for f in args.folders:
        folder = Path(f)
        if not folder.exists():
            print(f"[WARN] Nie istnieje: {folder}")
            continue

        for p in iter_files(folder, args.recursive):
            scanned += 1
            if allowed_ext is not None and p.suffix.lower() not in allowed_ext:
                continue

            if is_name_duplicate(p.stem, allowed_nums):
                to_remove.append(p)

    print(f"Skan zakończony. Przeskanowano plików: {scanned}")
    print(f"Znaleziono do usunięcia: {len(to_remove)}\n")

    for p in to_remove:
        print(str(p))

    if not args.delete:
        print("\n(DRY-RUN) Nic nie usunięto. Dodaj --delete żeby skasować.")
        return

    removed = 0
    failed = 0
    for p in to_remove:
        try:
            p.unlink()
            removed += 1
        except Exception as e:
            failed += 1
            print(f"[FAIL] {p} -> {e}")

    print(f"\nUsunięto: {removed}, błędy: {failed}")

if __name__ == "__main__":
    main()
