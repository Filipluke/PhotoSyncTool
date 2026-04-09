# -*- coding: utf-8 -*-
"""
blur_tool_v2.py

Fast blur-candidate scanner + Tkinter reviewer that supports RESUME without re-showing kept photos.

Key fixes vs v1:
- No upfront Path.exists() sweep (OneDrive-friendly; window opens immediately).
- Resume support via a small sidecar decision log (*.decisions.jsonl), so we don't rewrite huge CSV on every click.
- Optional CSV compaction command if you want the CSV itself to shrink.

Commands:
  scan    -> create CSV of blur candidates
  review  -> GUI review (Keep/Trash/Delete), resumes automatically
  compact -> rewrite CSV to only PENDING items (optional maintenance)

Requirements:
  pip install pillow opencv-python send2trash
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from PIL import Image, ImageOps, ImageFile
ImageFile.LOAD_TRUNCATED_IMAGES = True

try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
except Exception:
    cv2 = None
    np = None

try:
    from send2trash import send2trash  # type: ignore
except Exception:
    send2trash = None


IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
DEFAULT_THRESHOLD = 120.0
MAX_SIDE_FOR_ANALYSIS = 900
MAX_SIDE_FOR_VIEW = 1400

CSV_FIELDS = [
    "path",
    "score",
    "width",
    "height",
    "filesize_bytes",
    "mtime_epoch",
]

STATUS_PENDING = "pending"
STATUS_KEEP = "keep"
STATUS_DELETED = "deleted"
STATUS_TRASHED = "trashed"
STATUS_MISSING = "missing"
STATUS_ERROR = "error"


def eprint(*args):
    print(*args, file=sys.stderr)


def safe_float(x: str, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def safe_int(x: str, default: int = 0) -> int:
    try:
        return int(float(x))
    except Exception:
        return default


def format_score(score: float) -> str:
    return f"{score:.2f}"


def platform_key() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def platform_label() -> str:
    key = platform_key()
    if key == "windows":
        return "Windows"
    if key == "macos":
        return "macOS"
    return "Linux"


def pip_install_hint(pkg: str) -> str:
    if platform_key() == "windows":
        return f"py -m pip install {pkg}"
    return f"python3 -m pip install {pkg}"


def gui_available() -> bool:
    if platform_key() == "windows":
        return True
    if platform_key() == "macos":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def open_in_explorer(path: Path) -> None:
    # Best effort: open folder (or select file on Windows).
    try:
        target = path if path.is_dir() else path.parent
        key = platform_key()
        if key == "windows":
            if path.is_file():
                subprocess.run(["explorer", f'/select,{str(path)}'], check=False)
            else:
                subprocess.run(["explorer", str(target)], check=False)
        elif key == "macos":
            subprocess.run(["open", str(target)], check=False)
        else:
            subprocess.run(["xdg-open", str(target)], check=False)
    except Exception:
        pass


def ask_text(prompt: str, default: Optional[str] = None) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        val = input(f"{prompt}{suffix}: ").strip()
        if val:
            return val
        if default is not None:
            return default
        print("Wartość nie może być pusta.")


def ask_float(prompt: str, default: float) -> float:
    while True:
        val = input(f"{prompt} [{default}]: ").strip()
        if not val:
            return default
        try:
            return float(val)
        except ValueError:
            print("Podaj liczbę, np. 120 lub 95.5")


def ask_int(prompt: str, default: int) -> int:
    while True:
        val = input(f"{prompt} [{default}]: ").strip()
        if not val:
            return default
        try:
            return int(val)
        except ValueError:
            print("Podaj liczbę całkowitą.")


def ask_yes_no(prompt: str, default: bool = False) -> bool:
    opt = "Y/n" if default else "y/N"
    while True:
        val = input(f"{prompt} ({opt}): ").strip().lower()
        if not val:
            return default
        if val in {"y", "yes", "t", "tak"}:
            return True
        if val in {"n", "no", "nie"}:
            return False
        print("Wpisz 'y' albo 'n'.")


def choose_from_list(items: List[Path], title: str) -> Optional[Path]:
    if not items:
        return None
    print(f"\n{title}")
    for i, p in enumerate(items, 1):
        print(f"  {i}. {p}")
    print("  0. Anuluj")

    while True:
        val = input("Wybierz numer: ").strip()
        try:
            idx = int(val)
        except ValueError:
            print("Podaj numer.")
            continue
        if idx == 0:
            return None
        if 1 <= idx <= len(items):
            return items[idx - 1]
        print("Numer poza zakresem.")


def find_csv_files(root: Path, limit: int = 30) -> List[Path]:
    if not root.exists() or not root.is_dir():
        return []
    out = sorted(root.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    return out[:limit]


def cli_menu() -> None:
    print("Blur Tool (menu CLI)")
    print("--------------------")
    print(f"Wykryta platforma: {platform_label()} ({sys.platform})")
    print("Masz dostępne akcje: scan, review, compact.")

    while True:
        print("\nMenu:")
        print("  1. Scan folder -> CSV")
        print("  2. Review CSV (GUI)")
        print("  3. Compact CSV (tylko pending)")
        print("  4. Wyjście")

        choice = input("Wybierz opcję [1-4]: ").strip()

        if choice == "1":
            root = Path(ask_text("Podaj folder ze zdjęciami (rekurencyjnie skanowany)")).expanduser()
            if not root.exists() or not root.is_dir():
                print(f"Niepoprawny folder: {root}")
                continue

            default_out = root / "blur_candidates.csv"
            out_csv = Path(ask_text("Podaj ścieżkę wyjściowego CSV", str(default_out))).expanduser()
            threshold = ask_float("Próg blur (mniej = bardziej rozmyte)", DEFAULT_THRESHOLD)
            include_all = ask_yes_no("Dodać wszystkie zdjęcia do CSV (nie tylko rozmyte)?", default=False)
            top = ask_int("TOP N najbardziej rozmytych (0 = bez limitu)", 0)

            try:
                cmd_scan(root=root, out_csv=out_csv, threshold=threshold, include_all=include_all, top=top)
            except Exception as e:
                print(f"Błąd scan: {e}")

        elif choice == "2":
            start_dir = Path(ask_text("Folder, w którym szukać plików CSV", str(Path.cwd()))).expanduser()
            csv_candidates = find_csv_files(start_dir)
            picked = choose_from_list(csv_candidates, "Znalezione CSV:") if csv_candidates else None
            if picked is None:
                csv_input = ask_text("Podaj ścieżkę do CSV ręcznie")
                csv_path = Path(csv_input).expanduser()
            else:
                csv_path = picked

            if not csv_path.exists():
                print(f"CSV nie istnieje: {csv_path}")
                continue

            hard_delete = ask_yes_no("Usuwać na stałe zamiast przenosić do kosza?", default=False)
            show_raw = ask_text(
                "Statusy do pokazania (lista po przecinku)",
                "pending",
            )
            show = tuple(s.strip().lower() for s in show_raw.split(",") if s.strip()) or (STATUS_PENDING,)
            print(f"Loaded queue from: {csv_path}")
            print(f"Decisions log: {decisions_path_for(csv_path)}")
            print("GUI: Right/Enter=Keep | D=Delete | K=Trash | O=Open folder | Esc=Exit")
            try:
                tk_review(csv_path=csv_path, hard_delete=hard_delete, show_statuses=show)
            except Exception as e:
                print(f"Błąd review: {e}")

        elif choice == "3":
            start_dir = Path(ask_text("Folder, w którym szukać plików CSV", str(Path.cwd()))).expanduser()
            csv_candidates = find_csv_files(start_dir)
            picked = choose_from_list(csv_candidates, "Znalezione CSV:") if csv_candidates else None
            if picked is None:
                csv_input = ask_text("Podaj ścieżkę do CSV ręcznie")
                csv_path = Path(csv_input).expanduser()
            else:
                csv_path = picked

            if not csv_path.exists():
                print(f"CSV nie istnieje: {csv_path}")
                continue
            try:
                cmd_compact(csv_path=csv_path)
            except Exception as e:
                print(f"Błąd compact: {e}")

        elif choice == "4":
            print("Koniec.")
            return
        else:
            print("Nieznana opcja. Wybierz 1-4.")


def decisions_path_for(csv_path: Path) -> Path:
    return csv_path.with_suffix(csv_path.suffix + ".decisions.jsonl")


def append_decision(decisions_path: Path, path: Path, status: str, extra: Optional[dict] = None) -> None:
    decisions_path.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": time.time(),
        "path": str(path),
        "status": status,
    }
    if extra:
        rec.update(extra)
    with decisions_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def load_decisions(decisions_path: Path) -> Dict[str, str]:
    """
    Returns map: absolute_path_string -> last_status
    """
    if not decisions_path.exists():
        return {}
    statuses: Dict[str, str] = {}
    with decisions_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                p = str(Path(rec.get("path", "")).resolve())
                s = str(rec.get("status", "")).strip().lower()
                if p and s:
                    statuses[p] = s
            except Exception:
                continue
    return statuses


def read_csv_rows(csv_path: Path) -> List[dict]:
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")
    with csv_path.open("r", newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        rows = []
        for row in r:
            cleaned = {k: row.get(k, "") for k in CSV_FIELDS}
            rows.append(cleaned)
        return rows


def atomic_write_csv(csv_path: Path, rows: List[dict]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = csv_path.with_suffix(csv_path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    tmp.replace(csv_path)


def pil_open_rgb(path: Path) -> Image.Image:
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    elif img.mode == "RGBA":
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    return img


def resize_keep_aspect(img: Image.Image, max_side: int) -> Image.Image:
    w, h = img.size
    m = max(w, h)
    if m <= max_side:
        return img
    scale = max_side / float(m)
    nw = max(1, int(w * scale))
    nh = max(1, int(h * scale))
    return img.resize((nw, nh), Image.Resampling.LANCZOS)


def blur_score_variance_of_laplacian(img_rgb: Image.Image) -> float:
    img_small = resize_keep_aspect(img_rgb, MAX_SIDE_FOR_ANALYSIS)
    gray = img_small.convert("L")

    if cv2 is not None and np is not None:
        arr = np.array(gray, dtype=np.uint8)
        lap = cv2.Laplacian(arr, cv2.CV_64F)
        return float(lap.var())

    # fallback
    px = list(gray.getdata())
    w, h = gray.size
    if w < 3 or h < 3:
        return 0.0

    def at(x, y):
        return px[y * w + x]

    vals = []
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            v = (
                at(x, y - 1) +
                at(x - 1, y) -
                4 * at(x, y) +
                at(x + 1, y) +
                at(x, y + 1)
            )
            vals.append(float(v))

    if not vals:
        return 0.0
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    return float(var)


@dataclass
class Candidate:
    path: Path
    score: float
    width: int
    height: int
    filesize: int
    mtime: int

    @classmethod
    def from_row(cls, row: dict) -> "Candidate":
        p = Path(row["path"])
        return cls(
            path=p,
            score=safe_float(row.get("score", "0"), 0.0),
            width=safe_int(row.get("width", "0"), 0),
            height=safe_int(row.get("height", "0"), 0),
            filesize=safe_int(row.get("filesize_bytes", "0"), 0),
            mtime=safe_int(row.get("mtime_epoch", "0"), 0),
        )

    def to_row(self) -> dict:
        return {
            "path": str(self.path),
            "score": format_score(self.score),
            "width": str(self.width),
            "height": str(self.height),
            "filesize_bytes": str(self.filesize),
            "mtime_epoch": str(self.mtime),
        }


def scan_images(root: Path) -> List[Path]:
    files: List[Path] = []
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in IMG_EXTS:
            files.append(p)
    return files


def cmd_scan(root: Path, out_csv: Path, threshold: float, include_all: bool, top: int) -> None:
    if not root.exists():
        raise FileNotFoundError(f"Root does not exist: {root}")

    if cv2 is None or np is None:
        eprint("WARNING: opencv-python not available. Using slower fallback scoring (still works).")

    files = scan_images(root)
    print(f"Found {len(files)} images under: {root}")

    rows: List[dict] = []
    errors = 0
    blurry = 0

    t0 = time.time()
    for i, path in enumerate(files, 1):
        try:
            img = pil_open_rgb(path)
            w, h = img.size
            score = blur_score_variance_of_laplacian(img)
            st = path.stat()

            cand = Candidate(
                path=path,
                score=score,
                width=w,
                height=h,
                filesize=st.st_size,
                mtime=int(st.st_mtime),
            )

            if include_all or score < threshold:
                rows.append(cand.to_row())
                if score < threshold:
                    blurry += 1

            if i % 200 == 0:
                dt = time.time() - t0
                print(f"[{i}/{len(files)}] processed... ({dt:.1f}s) rows={len(rows)}")
        except Exception as e:
            errors += 1
            eprint(f"ERROR reading/scoring: {path} -> {e}")

    # blurriest first
    rows.sort(key=lambda r: safe_float(r.get("score", "0"), 0.0))

    if top and top > 0 and len(rows) > top:
        rows = rows[:top]

    atomic_write_csv(out_csv, rows)

    dt = time.time() - t0
    print(f"\nDone in {dt:.1f}s")
    if include_all:
        print(f"Wrote {len(rows)} rows to CSV (include-all): {out_csv}")
        print(f"(Among processed, {blurry} were below threshold={threshold})")
    else:
        print(f"Wrote {len(rows)} candidates (score < {threshold}) to CSV: {out_csv}")
    if errors:
        print(f"Errors: {errors} (see stderr)")


def build_review_queue(csv_path: Path, show_statuses: Tuple[str, ...]) -> List[Candidate]:
    rows = read_csv_rows(csv_path)
    candidates = [Candidate.from_row(r) for r in rows]

    # Apply decisions (sidecar log)
    decisions = load_decisions(decisions_path_for(csv_path))
    queue: List[Candidate] = []
    for c in candidates:
        key = str(c.path.resolve())
        st = decisions.get(key, STATUS_PENDING)
        if st in show_statuses:
            queue.append(c)

    queue.sort(key=lambda c: c.score)  # blurriest first
    return queue


def cmd_compact(csv_path: Path) -> None:
    """
    Rewrites the CSV so it contains only PENDING items (according to decisions log).
    This is optional maintenance; review works fine without it.
    """
    rows = read_csv_rows(csv_path)
    decisions = load_decisions(decisions_path_for(csv_path))

    out: List[dict] = []
    kept = 0
    removed = 0

    for r in rows:
        p = Path(r["path"])
        st = decisions.get(str(p.resolve()), STATUS_PENDING)
        if st == STATUS_PENDING:
            out.append(r)
            kept += 1
        else:
            removed += 1

    # Sort again by score
    out.sort(key=lambda r: safe_float(r.get("score", "0"), 0.0))
    atomic_write_csv(csv_path, out)
    print(f"Compacted CSV: kept {kept}, removed {removed}. CSV now: {csv_path}")


def tk_review(csv_path: Path, hard_delete: bool, show_statuses: Tuple[str, ...]) -> None:
    if not gui_available():
        print("GUI niedostępne: brak sesji graficznej (DISPLAY/WAYLAND_DISPLAY).")
        print("Uruchom narzędzie w środowisku desktopowym albo użyj opcji scan/compact.")
        return

    import tkinter as tk
    from tkinter import messagebox
    from PIL import ImageTk  # must be inside Tk context

    decisions_p = decisions_path_for(csv_path)

    queue = build_review_queue(csv_path, show_statuses=show_statuses)
    total_start = len(queue)

    if not queue:
        print("No items to review (queue is empty).")
        return

    root = tk.Tk()
    root.title("Blur Review Tool (v2)")
    root.geometry("1200x850")

    idx = 0
    photo_ref = {"img": None}

    info = tk.StringVar()
    path_var = tk.StringVar()

    lbl_info = tk.Label(root, textvariable=info, font=("Segoe UI", 12))
    lbl_info.pack(pady=6)
    lbl_path = tk.Label(root, textvariable=path_var, font=("Consolas", 10), wraplength=1120, justify="left")
    lbl_path.pack(pady=4)

    canvas = tk.Canvas(root, bg="#111111", highlightthickness=0)
    canvas.pack(fill="both", expand=True, padx=12, pady=10)

    frm = tk.Frame(root)
    frm.pack(pady=10)

    def set_info():
        if idx >= len(queue):
            info.set(f"Done. Reviewed {total_start} items.")
        else:
            c = queue[idx]
            info.set(
                f"{idx+1}/{len(queue)} | score={format_score(c.score)} | {c.width}x{c.height} | {c.filesize/1024/1024:.2f} MB"
            )
            path_var.set(str(c.path))

    def skip_current(status: str, extra: Optional[dict] = None):
        """
        Mark decision in sidecar log and remove from queue so it never shows again in this run or next runs.
        """
        nonlocal idx
        if idx >= len(queue):
            return
        c = queue[idx]
        append_decision(decisions_p, c.path, status, extra=extra)
        queue.pop(idx)
        # keep idx as-is (next item slides into idx)
        load_current_image()

    def load_current_image():
        nonlocal idx
        if idx < 0:
            idx = 0
        if idx >= len(queue):
            messagebox.showinfo("Koniec", "Nie ma więcej zdjęć do przejrzenia.")
            root.destroy()
            return

        c = queue[idx]

        # If file missing now: mark and skip (fast)
        if not c.path.exists():
            skip_current(STATUS_MISSING)
            return

        set_info()

        try:
            img = pil_open_rgb(c.path)
            img = resize_keep_aspect(img, MAX_SIDE_FOR_VIEW)

            cw = max(1, canvas.winfo_width())
            ch = max(1, canvas.winfo_height())
            iw, ih = img.size
            scale = min(cw / iw, ch / ih, 1.0)
            nw = max(1, int(iw * scale))
            nh = max(1, int(ih * scale))
            if (nw, nh) != (iw, ih):
                img = img.resize((nw, nh), Image.Resampling.LANCZOS)

            tk_img = ImageTk.PhotoImage(img)
            photo_ref["img"] = tk_img
            canvas.delete("all")
            canvas.create_image(cw // 2, ch // 2, image=tk_img, anchor="center")
        except Exception as e:
            eprint(f"DISPLAY ERROR: {c.path} -> {e}")
            skip_current(STATUS_ERROR, extra={"error": str(e)})

    def keep(event=None):
        skip_current(STATUS_KEEP)

    def trash(event=None):
        if idx >= len(queue):
            return
        c = queue[idx]
        if send2trash is None:
            messagebox.showerror("Brak modułu", f"Brak send2trash. Zainstaluj: {pip_install_hint('send2trash')}")
            return
        try:
            send2trash(str(c.path))
            skip_current(STATUS_TRASHED)
        except Exception as e:
            messagebox.showerror("Błąd", f"Nie udało się przenieść do kosza:\n{c.path}\n\n{e}")

    def delete(event=None):
        if idx >= len(queue):
            return
        c = queue[idx]
        try:
            if hard_delete:
                c.path.unlink(missing_ok=True)
                skip_current(STATUS_DELETED)
            else:
                if send2trash is None:
                    messagebox.showerror("Brak modułu", f"Brak send2trash. Zainstaluj: {pip_install_hint('send2trash')}")
                    return
                send2trash(str(c.path))
                skip_current(STATUS_TRASHED)
        except Exception as e:
            messagebox.showerror("Błąd", f"Nie udało się usunąć:\n{c.path}\n\n{e}")

    def open_folder(event=None):
        if idx >= len(queue):
            return
        open_in_explorer(queue[idx].path)

    def on_resize(event=None):
        root.after(80, load_current_image)

    btn_keep = tk.Button(frm, text="Zostaw (→ / Enter)", width=20, command=keep)
    btn_delete = tk.Button(frm, text=("Usuń na stałe" if hard_delete else "Usuń"), width=20, command=delete)
    btn_trash = tk.Button(frm, text="Kosz (K)", width=20, command=trash)
    btn_open = tk.Button(frm, text="Otwórz folder (O)", width=20, command=open_folder)

    btn_keep.grid(row=0, column=0, padx=8)
    btn_delete.grid(row=0, column=1, padx=8)
    btn_trash.grid(row=0, column=2, padx=8)
    btn_open.grid(row=0, column=3, padx=8)

    root.bind("<Right>", keep)
    root.bind("<Return>", keep)
    root.bind("d", delete)
    root.bind("D", delete)
    root.bind("k", trash)
    root.bind("K", trash)
    root.bind("o", open_folder)
    root.bind("O", open_folder)
    root.bind("<Escape>", lambda e: root.destroy())
    canvas.bind("<Configure>", on_resize)

    set_info()
    load_current_image()
    root.mainloop()


def main():
    p = argparse.ArgumentParser(description="Blur scanner + reviewer (v2, OneDrive-friendly, resumable).")
    p.add_argument("--menu", action="store_true", help="Start interactive CLI menu.")
    sub = p.add_subparsers(dest="cmd")

    ps = sub.add_parser("scan", help="Scan folder and write CSV of blur candidates.")
    ps.add_argument("--root", required=True, help="Root folder with photos (recursive).")
    ps.add_argument("--out", required=True, help="Output CSV path.")
    ps.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, help="Score threshold (lower = blurrier).")
    ps.add_argument("--include-all", action="store_true", help="Write all images to CSV (sorted), not only below threshold.")
    ps.add_argument("--top", type=int, default=0, help="Keep only TOP N blurriest rows in CSV (0 = no limit).")

    pr = sub.add_parser("review", help="Review CSV in GUI; resumes via sidecar decisions log.")
    pr.add_argument("--csv", required=True, help="Candidates CSV from scan.")
    pr.add_argument("--hard-delete", action="store_true", help="Permanently delete instead of sending to recycle bin.")
    pr.add_argument("--show", default="pending", help="Comma list of statuses to show (default: pending). "
                                                    "Options: pending,keep,deleted,trashed,missing,error")

    pc = sub.add_parser("compact", help="Rewrite CSV to contain only PENDING items (optional maintenance).")
    pc.add_argument("--csv", required=True, help="Candidates CSV to compact.")

    args = p.parse_args()

    if args.menu or not args.cmd:
        cli_menu()
        return

    if args.cmd == "scan":
        cmd_scan(Path(args.root), Path(args.out), float(args.threshold), bool(args.include_all), int(args.top))
        return

    if args.cmd == "compact":
        cmd_compact(Path(args.csv))
        return

    if args.cmd == "review":
        csv_path = Path(args.csv)
        show = tuple(s.strip().lower() for s in str(args.show).split(",") if s.strip())
        if not show:
            show = (STATUS_PENDING,)
        print(f"Loaded queue from: {csv_path}")
        print(f"Decisions log: {decisions_path_for(csv_path)}")
        print("GUI: Right/Enter=Keep | D=Delete | K=Trash | O=Open folder | Esc=Exit")
        tk_review(csv_path=csv_path, hard_delete=bool(args.hard_delete), show_statuses=show)
        return


if __name__ == "__main__":
    main()
