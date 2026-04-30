#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import csv
import json
import shutil
import threading
import time
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from sort_photos_script import (
    ensure_file_stable,
    is_media_file,
    iter_source_files,
    plan_action,
    verify_copy,
)
from photo_manager_index import index_sync_records, update_file_status


CONFIG_FILE_NAME = "photo_manager_config.json"
SYNC_LOG_NAME = "photo_manager_sync_log.csv"
DATE_SOURCES = ("exif", "mtime", "ctime")
PENDING_STATUS = "pending"

DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_LABELS = {
    "mon": "Mon",
    "tue": "Tue",
    "wed": "Wed",
    "thu": "Thu",
    "fri": "Fri",
    "sat": "Sat",
    "sun": "Sun",
}
SYNC_LOG_COLUMNS = ["ts", "mode", "src", "dst", "year", "flags", "status"]

Logger = Callable[[str], None]


@dataclass
class AppConfig:
    root_dir: str
    source_dir: str
    date_source: str
    recursive: bool
    include_nonmedia: bool
    full_hash: bool
    dry_run: bool
    delete_after_sync: bool
    watch_delete: bool
    sync_allowed_hours: str
    sync_weekly_hours: str
    settle_seconds: float
    stable_checks: int
    poll_interval: float
    blur_csv: str
    blur_threshold: float
    blur_top: int
    auto_delete_max: int
    auto_delete_hard: bool
    autostart_background: bool
    autostart_windows: bool
    start_minimized: bool
    minimize_to_tray: bool


@dataclass
class RuntimeConfig:
    root: Path
    source: Path
    date_source: str
    recursive: bool
    include_nonmedia: bool
    full_hash: bool
    dry_run: bool
    delete_after_sync: bool
    watch_delete: bool
    sync_allowed_hours: str
    sync_weekly_hours: str
    settle_seconds: float
    stable_checks: int
    poll_interval: float
    blur_csv: Path
    blur_threshold: float
    blur_top: int
    auto_delete_max: int
    auto_delete_hard: bool


def default_app_config(script_dir: Path, autostart_windows: bool = False) -> AppConfig:
    return AppConfig(
        root_dir=str(script_dir),
        source_dir="Sorting folder",
        date_source="exif",
        recursive=True,
        include_nonmedia=False,
        full_hash=False,
        dry_run=False,
        delete_after_sync=False,
        watch_delete=False,
        sync_allowed_hours="0-24",
        sync_weekly_hours="",
        settle_seconds=1.5,
        stable_checks=3,
        poll_interval=0.5,
        blur_csv="blur_candidates.csv",
        blur_threshold=120.0,
        blur_top=0,
        auto_delete_max=50,
        auto_delete_hard=False,
        autostart_background=False,
        autostart_windows=autostart_windows,
        start_minimized=True,
        minimize_to_tray=True,
    )


def load_app_config(config_path: Path, default_cfg: AppConfig) -> AppConfig:
    cfg = default_cfg
    if not config_path.exists():
        return cfg
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return cfg
    for key in cfg.__dict__.keys():
        if key in data:
            setattr(cfg, key, data[key])
    if cfg.date_source not in DATE_SOURCES:
        cfg.date_source = "exif"
    return cfg


def resolve_runtime_config(cfg: AppConfig, script_dir: Path) -> RuntimeConfig:
    if cfg.date_source not in DATE_SOURCES:
        raise ValueError("Date source must be one of: exif, mtime, ctime")
    parse_hour_windows(cfg.sync_allowed_hours)
    parse_weekly_hours(cfg.sync_weekly_hours)

    root_path = Path(str(cfg.root_dir)).expanduser()
    if not root_path.is_absolute():
        root_path = (script_dir / root_path).resolve()
    else:
        root_path = root_path.resolve()
    if not root_path.exists():
        raise ValueError(f"Root folder does not exist: {root_path}")

    source_path = Path(str(cfg.source_dir)).expanduser()
    if not source_path.is_absolute():
        source_path = (root_path / source_path).resolve()
    else:
        source_path = source_path.resolve()

    blur_csv = Path(str(cfg.blur_csv)).expanduser()
    if not blur_csv.is_absolute():
        blur_csv = (root_path / blur_csv).resolve()
    else:
        blur_csv = blur_csv.resolve()

    return RuntimeConfig(
        root=root_path,
        source=source_path,
        date_source=str(cfg.date_source),
        recursive=bool(cfg.recursive),
        include_nonmedia=bool(cfg.include_nonmedia),
        full_hash=bool(cfg.full_hash),
        dry_run=bool(cfg.dry_run),
        delete_after_sync=bool(cfg.delete_after_sync),
        watch_delete=bool(cfg.watch_delete),
        sync_allowed_hours=str(cfg.sync_allowed_hours or "0-24"),
        sync_weekly_hours=str(cfg.sync_weekly_hours or ""),
        settle_seconds=float(cfg.settle_seconds),
        stable_checks=int(cfg.stable_checks),
        poll_interval=float(cfg.poll_interval),
        blur_csv=blur_csv,
        blur_threshold=float(cfg.blur_threshold),
        blur_top=int(cfg.blur_top),
        auto_delete_max=int(cfg.auto_delete_max),
        auto_delete_hard=bool(cfg.auto_delete_hard),
    )


def parse_hour_windows(raw: str) -> List[tuple[int, int]]:
    text = (raw or "0-24").strip()
    windows: List[tuple[int, int]] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" not in part:
            raise ValueError("Sync hours must use ranges like 0-24, 8-18, or 22-7")
        start_raw, end_raw = part.split("-", 1)
        try:
            start = int(start_raw.strip())
            end = int(end_raw.strip())
        except Exception as exc:
            raise ValueError(f"Invalid sync hour range: {part}") from exc
        if not (0 <= start <= 23 and 0 <= end <= 24):
            raise ValueError(f"Sync hour range out of bounds: {part}")
        if start == end:
            raise ValueError(f"Sync hour range cannot be empty: {part}")
        windows.append((start, end))
    if not windows:
        raise ValueError("Sync hours cannot be empty")
    return windows


def parse_weekly_hours(raw: str) -> Dict[str, List[tuple[int, int]]]:
    text = (raw or "").strip()
    if not text:
        return {}

    schedule: Dict[str, List[tuple[int, int]]] = {}
    lines: Iterable[str] = text.replace(";", "\n").splitlines()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if "=" not in line:
            raise ValueError("Weekly schedule must use lines like mon=8-18")
        day_raw, hours_raw = line.split("=", 1)
        day = day_raw.strip().lower()[:3]
        if day not in DAY_KEYS:
            raise ValueError(f"Unknown schedule day: {day_raw}")
        hours_raw = hours_raw.strip()
        schedule[day] = parse_hour_windows(hours_raw) if hours_raw else []
    return schedule


def serialize_weekly_hours(schedule: Dict[str, List[tuple[int, int]]]) -> str:
    lines: List[str] = []
    for day in DAY_KEYS:
        windows = schedule.get(day, [])
        ranges = ",".join(f"{start}-{end}" for start, end in windows)
        lines.append(f"{day}={ranges}")
    return "\n".join(lines)


def is_sync_time_allowed(cfg: RuntimeConfig, when: Optional[datetime] = None) -> bool:
    current = when or datetime.now()
    weekly = parse_weekly_hours(cfg.sync_weekly_hours)
    day = DAY_KEYS[current.weekday()]
    windows = weekly[day] if day in weekly else parse_hour_windows(cfg.sync_allowed_hours)
    hour = current.hour

    for start, end in windows:
        if start < end and start <= hour < end:
            return True
        if start > end and (hour >= start or hour < end):
            return True
    return False


def weekly_schedule_summary(raw: str, fallback_hours: str) -> str:
    weekly = parse_weekly_hours(raw)
    if not weekly:
        return f"Using daily hours: {fallback_hours or '0-24'}"
    allowed = 0
    blocked_days = 0
    for day in DAY_KEYS:
        windows = weekly.get(day, [])
        if not windows:
            blocked_days += 1
        for start, end in windows:
            allowed += (end - start) if start < end else ((24 - start) + end)
    return f"Weekly schedule: {allowed}/168 h allowed, {blocked_days} blocked day(s)"


def append_sync_records(root: Path, records: List[dict], lock: Optional[threading.Lock] = None) -> Path:
    if not records:
        return root / SYNC_LOG_NAME
    log_path = root / SYNC_LOG_NAME
    log_path.parent.mkdir(parents=True, exist_ok=True)

    context = lock if lock is not None else nullcontext()
    with context:
        write_header = not log_path.exists()
        with log_path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=SYNC_LOG_COLUMNS)
            if write_header:
                writer.writeheader()
            for rec in records:
                row = {
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "mode": rec.get("mode", ""),
                    "src": rec.get("src", ""),
                    "dst": rec.get("dst", ""),
                    "year": rec.get("year", ""),
                    "flags": rec.get("flags", ""),
                    "status": rec.get("status", ""),
                }
                writer.writerow(row)
    return log_path


def run_batch_sync(cfg: RuntimeConfig, log: Logger) -> None:
    log(f"Batch sync root={cfg.root}")
    log(f"Batch sync source={cfg.source}")

    files: List[Path] = []
    seen = 0
    t0 = time.time()
    for path in iter_source_files(cfg.source, recursive=cfg.recursive):
        seen += 1
        if is_media_file(path, include_nonmedia=cfg.include_nonmedia):
            files.append(path)
        if seen % 400 == 0:
            log(f"scan: checked={seen}, matched={len(files)}")

    log(f"scan complete: checked={seen}, matched={len(files)}, elapsed={time.time() - t0:.1f}s")
    if not files:
        log("No files matched current settings.")
        return

    actions = []
    for idx, path in enumerate(files, start=1):
        try:
            actions.append(plan_action(cfg.root, path, date_source=cfg.date_source))
        except Exception as exc:
            log(f"plan skipped: {path} ({exc})")
        if idx % 400 == 0:
            log(f"planned actions: {idx}/{len(files)}")

    if not actions:
        log("No valid actions were generated.")
        return

    records: List[dict] = []
    copy_errors = 0

    for idx, action in enumerate(actions, start=1):
        for dst in action.dsts:
            if cfg.dry_run:
                records.append(
                    {
                        "mode": "batch",
                        "src": str(action.src),
                        "dst": str(dst),
                        "year": action.year,
                        "flags": ",".join(action.flags),
                        "status": "dry_run",
                    }
                )
                continue
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(action.src, dst)
                records.append(
                    {
                        "mode": "batch",
                        "src": str(action.src),
                        "dst": str(dst),
                        "year": action.year,
                        "flags": ",".join(action.flags),
                        "status": "copied",
                    }
                )
            except Exception as exc:
                copy_errors += 1
                records.append(
                    {
                        "mode": "batch",
                        "src": str(action.src),
                        "dst": str(dst),
                        "year": action.year,
                        "flags": ",".join(action.flags),
                        "status": f"copy_error:{exc}",
                    }
                )
                log(f"copy error: {action.src.name} -> {dst.name}: {exc}")

        if idx % 250 == 0 or idx == len(actions):
            log(f"copy progress: {idx}/{len(actions)}")

    log_path = append_sync_records(cfg.root, records)
    log(f"sync log updated: {log_path}")
    try:
        index_sync_records(
            cfg.root,
            records,
            date_source=cfg.date_source,
            compute_hash=cfg.full_hash,
            log=log,
        )
    except Exception as exc:
        log(f"index update skipped: {exc}")

    if cfg.dry_run:
        log("Dry-run complete. No files were copied or deleted.")
        return

    verify_failures = 0
    for idx, action in enumerate(actions, start=1):
        for dst in action.dsts:
            if not dst.exists():
                verify_failures += 1
                continue
            ok, _method = verify_copy(action.src, dst, full_hash=cfg.full_hash)
            if not ok:
                verify_failures += 1
                log(f"verify failed: {action.src.name} -> {dst.name}")
        if idx % 250 == 0 or idx == len(actions):
            log(f"verify progress: {idx}/{len(actions)}")

    if copy_errors > 0 or verify_failures > 0:
        log(f"Sync completed with errors (copy={copy_errors}, verify={verify_failures}).")
        log("Source deletion skipped due to errors.")
        return

    log("All copied files verified successfully.")
    if cfg.delete_after_sync:
        deleted = 0
        failed = 0
        for action in actions:
            try:
                action.src.unlink(missing_ok=True)
                try:
                    update_file_status(cfg.root, action.src, status="deleted")
                except Exception as exc:
                    log(f"index source status skipped: {action.src.name} ({exc})")
                deleted += 1
            except Exception as exc:
                failed += 1
                log(f"delete failed: {action.src} ({exc})")
        log(f"Source cleanup: deleted={deleted}, failed={failed}")


class BackgroundSyncService:
    def __init__(self, log: Logger, pending_check_interval: float = 60.0) -> None:
        self.log = log
        self.pending_check_interval = pending_check_interval
        self.observer = None
        self.running = False
        self.cfg: Optional[RuntimeConfig] = None
        self._recent: Dict[str, float] = {}
        self._pending: Dict[str, Path] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._scheduler_thread: Optional[threading.Thread] = None

    def start(self, cfg: RuntimeConfig) -> None:
        if self.running:
            self.log("Background sync already running.")
            return

        try:
            from watchdog.events import FileSystemEventHandler  # type: ignore
            from watchdog.observers import Observer  # type: ignore
        except Exception as exc:
            raise RuntimeError(
                "watchdog is required for background sync. Install with: py -m pip install watchdog"
            ) from exc

        if not cfg.source.exists():
            raise RuntimeError(f"Source folder does not exist: {cfg.source}")

        self.cfg = cfg
        self._recent.clear()
        self._pending.clear()
        self._stop_event.clear()
        service = self

        class Handler(FileSystemEventHandler):
            def on_created(self, event):
                if getattr(event, "is_directory", False):
                    return
                service.process_path(Path(event.src_path))

            def on_moved(self, event):
                if getattr(event, "is_directory", False):
                    return
                dst = getattr(event, "dest_path", None)
                if dst:
                    service.process_path(Path(dst))
                else:
                    service.process_path(Path(event.src_path))

        self.observer = Observer()
        self.observer.schedule(Handler(), str(cfg.source), recursive=cfg.recursive)
        self.observer.start()
        self.running = True
        self._scheduler_thread = threading.Thread(target=self._run_pending_loop, daemon=True)
        self._scheduler_thread.start()
        self.log(f"Background sync started. Watching: {cfg.source}")

    def stop(self) -> None:
        if not self.running:
            return
        try:
            self._stop_event.set()
            if self.observer is not None:
                self.observer.stop()
                self.observer.join(timeout=5)
            if self._scheduler_thread is not None:
                self._scheduler_thread.join(timeout=3)
        finally:
            self.observer = None
            self._scheduler_thread = None
            self.running = False
            self.cfg = None
            self.log("Background sync stopped.")

    def _defer_path(self, path: Path) -> None:
        with self._lock:
            already_pending = str(path) in self._pending
            self._pending[str(path)] = path
        if not already_pending:
            self.log(f"[watch] Deferred outside sync schedule: {path.name}")

    def _run_pending_loop(self) -> None:
        while not self._stop_event.wait(self.pending_check_interval):
            cfg = self.cfg
            if cfg is None or not is_sync_time_allowed(cfg):
                continue
            with self._lock:
                pending = list(self._pending.values())
                self._pending.clear()
            if pending:
                self.log(f"[watch] Processing deferred files: {len(pending)}")
            for path in pending:
                self.process_path(path)

    def _should_skip_recent(self, path: Path, cooldown_s: float = 2.0) -> bool:
        key = str(path)
        now = time.time()
        with self._lock:
            last = self._recent.get(key, 0.0)
            self._recent[key] = now
            if len(self._recent) > 5000:
                threshold = now - 120.0
                self._recent = {k: v for k, v in self._recent.items() if v >= threshold}
        return (now - last) < cooldown_s

    def process_path(self, path: Path) -> None:
        cfg = self.cfg
        if cfg is None:
            return

        try:
            path = path.resolve()
        except Exception:
            return

        if self._should_skip_recent(path):
            return
        if cfg.source not in path.parents and path != cfg.source:
            return
        if not is_media_file(path, include_nonmedia=cfg.include_nonmedia):
            return
        if not is_sync_time_allowed(cfg):
            self._defer_path(path)
            return

        stable = ensure_file_stable(
            path,
            settle_seconds=cfg.settle_seconds,
            stable_checks=cfg.stable_checks,
            poll_interval=cfg.poll_interval,
        )
        if not stable:
            self.log(f"[watch] Skipped unstable/missing file: {path.name}")
            return

        try:
            action = plan_action(cfg.root, path, date_source=cfg.date_source)
        except Exception as exc:
            self.log(f"[watch] Could not plan action for {path.name}: {exc}")
            return

        records: List[dict] = []
        if cfg.dry_run:
            for dst in action.dsts:
                records.append(
                    {
                        "mode": "watch",
                        "src": str(path),
                        "dst": str(dst),
                        "year": action.year,
                        "flags": ",".join(action.flags),
                        "status": "dry_run",
                    }
                )
            append_sync_records(cfg.root, records)
            self.log(f"[watch][dry-run] {path.name} -> {len(action.dsts)} destinations")
            return

        copied_dsts: List[Path] = []
        for dst in action.dsts:
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dst)
                copied_dsts.append(dst)
                records.append(
                    {
                        "mode": "watch",
                        "src": str(path),
                        "dst": str(dst),
                        "year": action.year,
                        "flags": ",".join(action.flags),
                        "status": "copied",
                    }
                )
            except Exception as exc:
                records.append(
                    {
                        "mode": "watch",
                        "src": str(path),
                        "dst": str(dst),
                        "year": action.year,
                        "flags": ",".join(action.flags),
                        "status": f"copy_error:{exc}",
                    }
                )
                self.log(f"[watch] Copy failed: {path.name} -> {dst} ({exc})")

        all_ok = True
        for dst in copied_dsts:
            try:
                ok, _method = verify_copy(path, dst, full_hash=cfg.full_hash)
                if not ok:
                    all_ok = False
                    self.log(f"[watch] Verify failed: {path.name} -> {dst.name}")
            except Exception as exc:
                all_ok = False
                self.log(f"[watch] Verify error for {dst.name}: {exc}")

        append_sync_records(cfg.root, records)
        try:
            index_sync_records(
                cfg.root,
                records,
                date_source=cfg.date_source,
                compute_hash=cfg.full_hash,
                log=self.log,
            )
        except Exception as exc:
            self.log(f"[watch] Index update skipped: {exc}")

        if all_ok:
            msg = f"[watch] Synced: {path.name} -> year {action.year}"
            if "snapshot" in action.flags:
                msg += " + SnapShots"
            if "snapchat" in action.flags:
                msg += " + Snapchat"
            self.log(msg)
        else:
            self.log(f"[watch] Sync finished with verification issues for: {path.name}")

        if all_ok and cfg.watch_delete:
            try:
                path.unlink(missing_ok=True)
                try:
                    update_file_status(cfg.root, path, status="deleted")
                except Exception as exc:
                    self.log(f"[watch] Index source status skipped: {path.name} ({exc})")
                self.log(f"[watch] Deleted source after sync: {path.name}")
            except Exception as exc:
                self.log(f"[watch] Could not delete source {path.name}: {exc}")
