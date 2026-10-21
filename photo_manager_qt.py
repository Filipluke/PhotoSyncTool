#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import csv
import io
import json
import queue
import secrets
import shutil
import subprocess
import sys
import threading
import time
import warnings
from dataclasses import dataclass
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import Dict, List, Optional
import urllib.request

from photo_manager_core import (
    DAY_KEYS,
    DAY_LABELS,
    GOOGLE_DRIVE_LIBRARY_FOLDER_NAME,
    default_config_path,
    default_google_drive_library_folder,
    default_photo_root,
    detect_google_drive_folder,
    google_drive_folder_candidates,
    parse_weekly_hours,
    run_batch_sync,
    serialize_weekly_hours,
    weekly_schedule_summary,
)
from photo_manager_import import copy_verified, originals_verified
from photo_manager_index import (
    default_index_path,
    import_blur_csv,
    index_sync_records,
    rebuild_index,
    update_blur_status,
    update_file_status,
)
from photo_manager_features import (
    DuplicateCandidate,
    DuplicateScanCancelled,
    GalleryItem,
    build_thumbnail,
    dashboard_stats,
    enqueue_delete,
    export_delete_queue,
    export_sync_report,
    gallery_filter_options,
    human_bytes,
    list_delete_queue,
    list_gallery_items,
    parse_tags,
    run_light_ai,
    scan_duplicates,
    search_ai_metadata,
    trash_delete_items,
    update_delete_items,
)
from photo_manager_google_drive import (
    DEFAULT_REMOTE_ROOT_NAME,
    default_credentials_path as default_google_credentials_path,
    default_token_path as default_google_token_path,
    write_desktop_credentials_file,
)
from photo_manager_lan import (
    DEFAULT_LAN_INBOX,
    DEFAULT_LAN_PORT,
    MobileTransferServer,
    default_lan_inbox,
    lan_endpoint_urls,
)
from sort_photos_script import (
    ensure_file_stable,
    is_media_file,
    iter_source_files,
    plan_action,
    verify_copy,
)

try:
    from send2trash import send2trash  # type: ignore
except Exception:
    send2trash = None

try:
    from PySide6.QtCore import QSize, Qt, QTimer, QUrl
    from PySide6.QtGui import QAction, QColor, QCloseEvent, QDesktopServices, QIcon, QPixmap
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtWidgets import (
        QApplication,
        QAbstractItemView,
        QCheckBox,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QFileDialog,
        QFormLayout,
        QGridLayout,
        QGroupBox,
        QHeaderView,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidgetItem,
        QMainWindow,
        QMenu,
        QMessageBox,
        QListWidget,
        QScrollArea,
        QStyle,
        QSystemTrayIcon,
        QTabWidget,
        QTableWidget,
        QTableWidgetItem,
        QSplitter,
        QPlainTextEdit,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )
except Exception as exc:
    raise RuntimeError(
        "PySide6 is required for the modern GUI. Install with: py -m pip install PySide6"
    ) from exc


SYNC_LOG_NAME = "photo_manager_sync_log.csv"
BLUR_SCRIPT_NAME = "blur_tool.py"
WINDOWS_RUN_VALUE_NAME = "PhotoManagerPro"
APP_ICON_PACKAGE = "photosync_tool_assets"
APP_ICON_NAME = "photo_manager_icon.png"
SINGLE_INSTANCE_SERVER_NAME = "PhotoManagerPro.Filipluke.SingleInstance.v1"

DATE_SOURCES = ("exif", "mtime", "ctime")
PENDING_STATUS = "pending"


def load_app_icon() -> QIcon:
    icon = QIcon()
    try:
        data = resources.files(APP_ICON_PACKAGE).joinpath(APP_ICON_NAME).read_bytes()
    except Exception:
        data = b""

    if data:
        pixmap = QPixmap()
        if pixmap.loadFromData(data):
            icon.addPixmap(pixmap)
    return icon


def default_legacy_config_path() -> Path:
    return Path(__file__).resolve().parent / "photo_manager_config.json"


class SingleInstanceGuard:
    def __init__(self, server_name: str) -> None:
        self.server_name = server_name
        self.server: Optional[QLocalServer] = None
        self.on_activate = None

    def notify_existing_instance(self, timeout_ms: int = 500) -> bool:
        socket = QLocalSocket()
        socket.connectToServer(self.server_name)
        if not socket.waitForConnected(timeout_ms):
            socket.abort()
            return False

        socket.write(b"show\n")
        socket.flush()
        socket.waitForBytesWritten(timeout_ms)
        socket.disconnectFromServer()
        return True

    def listen(self, on_activate) -> None:
        self.on_activate = on_activate
        QLocalServer.removeServer(self.server_name)
        self.server = QLocalServer()
        self.server.newConnection.connect(self._handle_new_connection)
        if not self.server.listen(self.server_name):
            raise RuntimeError(f"Could not create single-instance server: {self.server.errorString()}")

    def close(self) -> None:
        if self.server is not None:
            self.server.close()
            self.server = None
        QLocalServer.removeServer(self.server_name)

    def _handle_new_connection(self) -> None:
        if self.server is None:
            return
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.close()
        if self.on_activate is not None:
            self.on_activate()


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
    google_drive_credentials: str
    google_drive_token: str
    google_drive_remote_root: str
    google_drive_parent_id: str
    google_drive_compute_hash: bool
    google_drive_overwrite: bool
    lan_transfer_inbox: str
    lan_transfer_port: int
    lan_transfer_pairing_code: str


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


class WeeklyScheduleDialog(QDialog):
    def __init__(self, schedule_text: str, fallback_hours: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Weekly Sync Schedule")
        self.resize(980, 420)

        self.table = QTableWidget(7, 24)
        self.table.setHorizontalHeaderLabels([f"{h:02d}" for h in range(24)])
        self.table.setVerticalHeaderLabels([DAY_LABELS[day] for day in DAY_KEYS])
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.cellClicked.connect(self._toggle_cell)
        self._use_daily_hours = False

        layout = QVBoxLayout(self)
        hint = QLabel("Click hours to allow or block background synchronization.")
        hint.setObjectName("scheduleHint")
        layout.addWidget(hint)
        layout.addWidget(self.table, stretch=1)

        preset_row = QHBoxLayout()
        self.allow_all_btn = QPushButton("Allow All")
        self.clear_all_btn = QPushButton("Block All")
        self.workday_btn = QPushButton("Workdays 08-18")
        self.night_btn = QPushButton("Nights 22-07")
        self.use_daily_btn = QPushButton("Use Daily Hours")
        for btn in (
            self.allow_all_btn,
            self.clear_all_btn,
            self.workday_btn,
            self.night_btn,
            self.use_daily_btn,
        ):
            btn.setObjectName("secondaryAction")
            preset_row.addWidget(btn)
        preset_row.addStretch(1)
        layout.addLayout(preset_row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.allow_all_btn.clicked.connect(lambda: self._fill_all(True))
        self.clear_all_btn.clicked.connect(lambda: self._fill_all(False))
        self.workday_btn.clicked.connect(self._preset_workdays)
        self.night_btn.clicked.connect(self._preset_nights)
        self.use_daily_btn.clicked.connect(self._accept_daily_hours)

        self._init_cells(schedule_text, fallback_hours)
        self.setStyleSheet(
            """
            QDialog { background: #191c22; color: #d7dde8; }
            QLabel#scheduleHint { color: #cdd4e2; padding: 4px; }
            QTableWidget {
                gridline-color: #343a46;
                background: #1f232b;
                alternate-background-color: #252a34;
                color: #d7dde8;
                border: 1px solid #3a3f49;
            }
            QHeaderView::section {
                background: #2a3039;
                color: #d7dde8;
                border: 1px solid #3a3f49;
                padding: 4px;
            }
            QPushButton {
                background: #2a3448;
                border: 1px solid #4a5e84;
                border-radius: 5px;
                padding: 6px 10px;
                color: #dce8ff;
            }
            QPushButton:hover { background: #334464; }
            """
        )

    def _init_cells(self, schedule_text: str, fallback_hours: str) -> None:
        try:
            schedule = parse_weekly_hours(schedule_text)
        except Exception:
            schedule = {}
        fallback = self._safe_windows(fallback_hours)
        for row, day in enumerate(DAY_KEYS):
            windows = schedule[day] if day in schedule else fallback
            for hour in range(24):
                item = QTableWidgetItem("")
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self.table.setItem(row, hour, item)
                self._set_allowed(item, self._hour_allowed(hour, windows))

    def _safe_windows(self, raw: str) -> List[tuple[int, int]]:
        parent = self.parent()
        try:
            return parent._parse_hour_windows(raw)  # type: ignore[attr-defined]
        except Exception:
            return [(0, 24)]

    def _hour_allowed(self, hour: int, windows: List[tuple[int, int]]) -> bool:
        for start, end in windows:
            if start < end and start <= hour < end:
                return True
            if start > end and (hour >= start or hour < end):
                return True
        return False

    def _set_allowed(self, item: QTableWidgetItem, allowed: bool) -> None:
        item.setData(Qt.ItemDataRole.UserRole, allowed)
        if allowed:
            item.setBackground(QColor("#23704a"))
            item.setToolTip("Sync allowed")
        else:
            item.setBackground(QColor("#2b303a"))
            item.setToolTip("Sync blocked")

    def _toggle_item(self, item: QTableWidgetItem) -> None:
        self._set_allowed(item, not bool(item.data(Qt.ItemDataRole.UserRole)))

    def _toggle_cell(self, row: int, column: int) -> None:
        item = self.table.item(row, column)
        if item is not None:
            self._toggle_item(item)

    def _fill_all(self, allowed: bool) -> None:
        for row in range(7):
            for col in range(24):
                item = self.table.item(row, col)
                if item is not None:
                    self._set_allowed(item, allowed)

    def _preset_workdays(self) -> None:
        self._fill_all(False)
        for row in range(5):
            for hour in range(8, 18):
                item = self.table.item(row, hour)
                if item is not None:
                    self._set_allowed(item, True)

    def _preset_nights(self) -> None:
        self._fill_all(False)
        for row in range(7):
            for hour in list(range(0, 7)) + [22, 23]:
                item = self.table.item(row, hour)
                if item is not None:
                    self._set_allowed(item, True)

    def _accept_daily_hours(self) -> None:
        self._use_daily_hours = True
        self.accept()

    def schedule_text(self) -> str:
        if self._use_daily_hours:
            return ""
        schedule: Dict[str, List[tuple[int, int]]] = {}
        for row, day in enumerate(DAY_KEYS):
            allowed = [
                bool(self.table.item(row, hour).data(Qt.ItemDataRole.UserRole))
                for hour in range(24)
            ]
            schedule[day] = self._ranges_from_allowed(allowed)
        return serialize_weekly_hours(schedule)

    def _ranges_from_allowed(self, allowed: List[bool]) -> List[tuple[int, int]]:
        if all(allowed):
            return [(0, 24)]
        if not any(allowed):
            return []

        ranges: List[tuple[int, int]] = []
        start = None
        for hour, is_allowed in enumerate(allowed):
            if is_allowed and start is None:
                start = hour
            if (not is_allowed or hour == 23) and start is not None:
                end = hour + 1 if is_allowed and hour == 23 else hour
                ranges.append((start, end))
                start = None

        if len(ranges) >= 2 and ranges[0][0] == 0 and ranges[-1][1] == 24:
            first = ranges.pop(0)
            last = ranges.pop()
            ranges.insert(0, (last[0], first[1]))
        return ranges


class SyncWatchService:
    def __init__(self, app: "PhotoManagerWindow") -> None:
        self.app = app
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
            self.app.log("Background sync already running.")
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
        self.app.log(f"Background sync started. Watching: {cfg.source}")

    def stop(self) -> None:
        if not self.running:
            return
        try:
            if self.observer is not None:
                self.observer.stop()
                self._stop_event.set()
                self.observer.join(timeout=5)
                if self._scheduler_thread is not None:
                    self._scheduler_thread.join(timeout=3)
        finally:
            self.observer = None
            self._scheduler_thread = None
            self.running = False
            self.cfg = None
            self.app.log("Background sync stopped.")

    def _defer_path(self, path: Path) -> None:
        with self._lock:
            already_pending = str(path) in self._pending
            self._pending[str(path)] = path
        if not already_pending:
            self.app.log(f"[watch] Deferred outside sync schedule: {path.name}")

    def _run_pending_loop(self) -> None:
        while not self._stop_event.wait(60):
            cfg = self.cfg
            if cfg is None or not self.app.is_sync_time_allowed(cfg):
                continue
            with self._lock:
                pending = list(self._pending.values())
                self._pending.clear()
            if pending:
                self.app.log(f"[watch] Processing deferred files: {len(pending)}")
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
        if not self.app.is_sync_time_allowed(cfg):
            self._defer_path(path)
            return

        stable = ensure_file_stable(
            path,
            settle_seconds=cfg.settle_seconds,
            stable_checks=cfg.stable_checks,
            poll_interval=cfg.poll_interval,
        )
        if not stable:
            self.app.log(f"[watch] Skipped unstable/missing file: {path.name}")
            return

        try:
            action = plan_action(cfg.root, path, date_source=cfg.date_source)
        except Exception as exc:
            self.app.log(f"[watch] Could not plan action for {path.name}: {exc}")
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
            self.app.log(f"[watch][dry-run] {path.name} -> {len(action.dsts)} destinations")
            return

        copied_dsts: List[Path] = []
        for dst_index, dst in enumerate(action.dsts):
            try:
                dst, status, digest = copy_verified(path, dst, cfg.root)
                action.dsts[dst_index] = dst
                copied_dsts.append(dst)
                records.append(
                    {
                        "mode": "watch",
                        "src": str(path),
                        "dst": str(dst),
                        "year": action.year,
                        "flags": ",".join(action.flags),
                        "status": status,
                        "sha256": digest,
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
                self.app.log(f"[watch] Copy failed: {path.name} -> {dst} ({exc})")

        all_ok = len(copied_dsts) == len(action.dsts) and bool(copied_dsts)
        for dst in copied_dsts:
            try:
                ok, _method = verify_copy(path, dst, full_hash=True)
                if not ok:
                    all_ok = False
                    self.app.log(f"[watch] Verify failed: {path.name} -> {dst.name}")
            except Exception as exc:
                all_ok = False
                self.app.log(f"[watch] Verify error for {dst.name}: {exc}")

        self.app.append_sync_records(cfg.root, records)
        try:
            index_sync_records(
                cfg.root,
                records,
                date_source=cfg.date_source,
                compute_hash=cfg.full_hash,
                log=self.app.log,
            )
        except Exception as exc:
            self.app.log(f"[watch] Index update skipped: {exc}")

        if all_ok:
            msg = f"[watch] Synced: {path.name} -> year {action.year}"
            if "snapshot" in action.flags:
                msg += " + SnapShots"
            if "snapchat" in action.flags:
                msg += " + Snapchat"
            self.app.log(msg)
        else:
            self.app.log(f"[watch] Sync finished with verification issues for: {path.name}")

        if all_ok and cfg.watch_delete and originals_verified([action]):
            try:
                path.unlink(missing_ok=True)
                try:
                    update_file_status(cfg.root, path, status="deleted")
                except Exception as exc:
                    self.app.log(f"[watch] Index source status skipped: {path.name} ({exc})")
                self.app.log(f"[watch] Deleted source after sync: {path.name}")
            except Exception as exc:
                self.app.log(f"[watch] Could not delete source {path.name}: {exc}")


class PhotoManagerWindow(QMainWindow):
    def __init__(self, app_icon: Optional[QIcon] = None) -> None:
        super().__init__()
        self.script_dir = Path(__file__).resolve().parent
        self.legacy_config_path = default_legacy_config_path()
        self.config_path = default_config_path()
        self._allow_real_close = False
        self.tray_icon: Optional[QSystemTrayIcon] = None
        self.sync_weekly_hours = ""
        self.app_icon = app_icon if app_icon is not None else load_app_icon()

        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.ui_queue: "queue.Queue[object]" = queue.Queue()
        self.log_lock = threading.Lock()
        self.worker_thread: Optional[threading.Thread] = None
        self.worker_name = ""
        self.sync_service = SyncWatchService(self)
        self.lan_server: Optional[MobileTransferServer] = None
        self.duplicate_scan_cancel_event = threading.Event()
        self.gallery_payload: List[tuple[GalleryItem, Optional[Path]]] = []
        self.duplicate_actions: List[DuplicateCandidate] = []
        self.lan_recent_uploads: List[Path] = []
        self.next_action_target = "settings"

        self.sync_log_columns = ["ts", "mode", "src", "dst", "year", "flags", "status"]

        self.setWindowTitle("Photo Manager Pro")
        if not self.app_icon.isNull():
            self.setWindowIcon(self.app_icon)
        self.resize(1440, 810)
        self.setMinimumSize(1120, 680)
        self._build_ui()
        self._apply_theme()
        self._build_tray()

        loaded_cfg = self._load_config()
        self._apply_config(loaded_cfg)
        self.log("Configuration loaded.")
        self._set_bg_running(False)
        self.on_compare_preview()
        QTimer.singleShot(250, self.on_dashboard_refresh)

        self.log_timer = QTimer(self)
        self.log_timer.timeout.connect(self._drain_log_queue)
        self.log_timer.start(150)

        if self.autostart_background_check.isChecked():
            QTimer.singleShot(500, self.on_start_background)

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("appRoot")
        self.setCentralWidget(root)
        main_layout = QVBoxLayout(root)
        main_layout.setContentsMargins(0, 4, 0, 0)
        main_layout.setSpacing(0)

        main_layout.addWidget(self._build_command_bar())

        self.workspace_tabs = QTabWidget()
        self.workspace_tabs.setObjectName("workspaceTabs")
        self.workspace_tabs.setDocumentMode(True)
        self.workspace_tabs.addTab(self._build_dashboard_tab(), "Dashboard")
        self.workspace_tabs.addTab(self._build_gallery_tab(), "Gallery")
        self.workspace_tabs.addTab(self._build_duplicates_tab(), "Duplicates")
        self.workspace_tabs.addTab(self._build_delete_queue_tab(), "Cleanup")
        self.workspace_tabs.addTab(self._build_ai_tab(), "AI Metadata")
        self.workspace_tabs.addTab(self._build_compare_tab(), "Sync Plan")
        self.workspace_tabs.addTab(self._build_cloud_tab(), "Cloud Sync")
        self.workspace_tabs.addTab(self._build_phone_connect_tab(), "Phone Connect")
        self.workspace_tabs.addTab(self._build_settings_tab(), "Settings")
        self.workspace_tabs.addTab(self._build_diagnostics_tab(), "Diagnostics")
        self.workspace_tabs.tabBar().hide()
        self.workspace_tabs.currentChanged.connect(self._sync_nav_selection)

        workspace_shell = QWidget()
        workspace_shell.setObjectName("workspaceShell")
        workspace_layout = QHBoxLayout(workspace_shell)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)
        workspace_layout.addWidget(self._build_sidebar())
        workspace_layout.addWidget(self.workspace_tabs, stretch=1)
        main_layout.addWidget(workspace_shell, stretch=1)

        self.save_settings_btn.clicked.connect(self.on_save_settings)
        self.use_google_drive_btn.clicked.connect(self.on_use_google_drive_root)
        self.open_google_drive_btn.clicked.connect(self.on_open_google_drive_root)
        self.compare_btn.clicked.connect(self.on_open_sync_plan)
        self.run_sync_btn.clicked.connect(self.on_run_sync_now)
        self.start_background_btn.clicked.connect(self.on_start_background)
        self.stop_background_btn.clicked.connect(self.on_stop_background)
        self.open_settings_btn.clicked.connect(lambda: self.workspace_tabs.setCurrentWidget(self.settings_tab))
        self.open_diagnostics_btn.clicked.connect(lambda: self.workspace_tabs.setCurrentWidget(self.diagnostics_tab))
        self.next_action_btn.clicked.connect(self.on_next_best_action)
        self.next_settings_btn.clicked.connect(self.on_about)
        self.edit_schedule_btn.clicked.connect(self.on_edit_schedule)
        self.sync_hours_edit.textChanged.connect(lambda _text: self._refresh_schedule_summary())
        self.install_service_btn.clicked.connect(lambda: self.on_service_command("install"))
        self.start_service_btn.clicked.connect(lambda: self.on_service_command("start"))
        self.stop_service_btn.clicked.connect(lambda: self.on_service_command("stop"))
        self.uninstall_service_btn.clicked.connect(lambda: self.on_service_command("uninstall"))
        self.rebuild_index_btn.clicked.connect(self.on_rebuild_index)
        self.import_blur_index_btn.clicked.connect(self.on_import_blur_index)
        self.blur_scan_btn.clicked.connect(self.on_blur_scan)
        self.blur_review_btn.clicked.connect(self.on_blur_review)
        self.blur_queue_btn.clicked.connect(self.on_queue_blur_candidates)
        self.blur_autodelete_btn.clicked.connect(self.on_blur_auto_delete)
        self.dashboard_refresh_btn.clicked.connect(self.on_dashboard_refresh)
        self.dashboard_export_btn.clicked.connect(self.on_dashboard_export_sync_report)
        self.about_btn.clicked.connect(self.on_about)
        self.check_updates_btn.clicked.connect(self.on_check_updates)
        self.start_menu_shortcut_btn.clicked.connect(self.on_create_start_menu_shortcut)
        self.gallery_refresh_btn.clicked.connect(self.on_gallery_refresh)
        self.gallery_search_edit.returnPressed.connect(self.on_gallery_refresh)
        self.gallery_open_btn.clicked.connect(self.on_gallery_open_selected)
        self.gallery_queue_btn.clicked.connect(self.on_gallery_queue_selected)
        self.gallery_list.currentItemChanged.connect(self.on_gallery_selected)
        self.duplicate_scan_btn.clicked.connect(self.on_duplicate_scan)
        self.duplicate_cancel_scan_btn.clicked.connect(self.on_duplicate_cancel_scan)
        self.duplicate_queue_selected_btn.clicked.connect(self.on_duplicate_queue_selected)
        self.duplicate_queue_all_btn.clicked.connect(self.on_duplicate_queue_all)
        self.duplicate_open_keep_btn.clicked.connect(lambda: self.on_duplicate_open("keep"))
        self.duplicate_open_remove_btn.clicked.connect(lambda: self.on_duplicate_open("remove"))
        self.duplicates_table.itemSelectionChanged.connect(self.on_duplicate_selected)
        self.delete_refresh_btn.clicked.connect(self.on_delete_queue_refresh)
        self.delete_status_combo.currentTextChanged.connect(lambda _text: self.on_delete_queue_refresh())
        self.delete_cancel_btn.clicked.connect(self.on_delete_queue_cancel_selected)
        self.delete_trash_selected_btn.clicked.connect(self.on_delete_queue_trash_selected)
        self.delete_trash_all_btn.clicked.connect(self.on_delete_queue_trash_all)
        self.delete_export_btn.clicked.connect(self.on_delete_queue_export)
        self.delete_recycle_btn.clicked.connect(self.on_open_recycle_bin)
        self.ai_run_btn.clicked.connect(self.on_light_ai_run)
        self.ai_search_btn.clicked.connect(self.on_ai_search)
        self.ai_search_edit.returnPressed.connect(self.on_ai_search)
        self.ai_open_btn.clicked.connect(self.on_ai_open_selected)
        self.google_save_credentials_btn.clicked.connect(self.on_google_save_credentials)
        self.google_auth_btn.clicked.connect(self.on_google_auth)
        self.google_plan_upload_btn.clicked.connect(self.on_google_plan_upload)
        self.google_upload_btn.clicked.connect(self.on_google_upload)
        self.google_plan_download_btn.clicked.connect(self.on_google_plan_download)
        self.google_download_btn.clicked.connect(self.on_google_download)
        self.lan_start_btn.clicked.connect(self.on_start_lan_receiver)
        self.lan_stop_btn.clicked.connect(self.on_stop_lan_receiver)
        self.lan_open_inbox_btn.clicked.connect(self.on_open_lan_inbox)
        self.lan_sync_inbox_btn.clicked.connect(self.on_sync_lan_inbox)
        self.lan_generate_code_btn.clicked.connect(self.on_generate_lan_pairing_code)
        self.lan_refresh_urls_btn.clicked.connect(self._refresh_lan_urls)

    def _build_command_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("commandBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(26, 14, 18, 14)
        layout.setSpacing(12)

        title_block = QWidget()
        title_shell = QHBoxLayout(title_block)
        title_shell.setContentsMargins(0, 0, 0, 0)
        title_shell.setSpacing(12)

        app_logo = QLabel()
        app_logo.setObjectName("appLogo")
        app_logo.setFixedSize(40, 40)
        app_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if not self.app_icon.isNull():
            app_logo.setPixmap(self.app_icon.pixmap(QSize(34, 34)))
        else:
            app_logo.setText("PM")

        title_text = QWidget()
        title_layout = QVBoxLayout(title_text)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(3)
        app_title = QLabel("Photo Manager Pro")
        app_title.setObjectName("appTitle")
        app_subtitle = QLabel("Local photo sync, review, cleanup, and metadata")
        app_subtitle.setObjectName("appSubtitle")
        title_layout.addWidget(app_title)
        title_layout.addWidget(app_subtitle)
        title_shell.addWidget(app_logo)
        title_shell.addWidget(title_text)

        self.bg_status_label = QLabel("Watching paused")
        self.bg_status_label.setObjectName("statusPill")
        self.bg_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.bg_status_label.setFixedHeight(42)
        self.bg_status_label.setMinimumWidth(170)
        self.compare_btn = QPushButton("Preview Sync Plan")
        self.compare_btn.setObjectName("secondaryAction")
        self.run_sync_btn = QPushButton("Sync Now")
        self.run_sync_btn.setObjectName("primaryAction")
        self.start_background_btn = QPushButton("Start Watching")
        self.start_background_btn.setObjectName("secondaryAction")
        self.stop_background_btn = QPushButton("Stop Watching")
        self.stop_background_btn.setObjectName("quietAction")
        self.open_settings_btn = QPushButton("Settings")
        self.open_settings_btn.setObjectName("toolbarButton")
        self.open_diagnostics_btn = QPushButton("Diagnostics")
        self.open_diagnostics_btn.setObjectName("toolbarButton")

        self.compare_btn.setIcon(self._standard_icon("SP_FileDialogDetailedView"))
        self.run_sync_btn.setIcon(self._standard_icon("SP_MediaPlay"))
        self.start_background_btn.setIcon(self._standard_icon("SP_BrowserReload"))
        self.stop_background_btn.setIcon(self._standard_icon("SP_MediaStop"))
        self.open_settings_btn.setIcon(self._standard_icon("SP_FileDialogContentsView"))
        self.open_diagnostics_btn.setIcon(self._standard_icon("SP_MessageBoxInformation"))

        for btn in (
            self.compare_btn,
            self.run_sync_btn,
            self.start_background_btn,
            self.stop_background_btn,
            self.open_settings_btn,
            self.open_diagnostics_btn,
        ):
            btn.setMinimumHeight(40)

        layout.addWidget(title_block, stretch=1)
        layout.addWidget(self.bg_status_label)
        layout.addWidget(self.compare_btn)
        layout.addWidget(self.run_sync_btn)
        layout.addWidget(self.start_background_btn)
        layout.addWidget(self.stop_background_btn)
        layout.addSpacing(6)
        layout.addWidget(self.open_settings_btn)
        layout.addWidget(self.open_diagnostics_btn)
        return bar

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(258)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(10, 18, 10, 12)
        layout.setSpacing(8)

        self.nav_buttons: List[QPushButton] = []
        self._add_nav_button(layout, 0, "⌂", "Dashboard")
        self._add_nav_button(layout, 1, "▧", "Gallery")
        self._add_nav_button(layout, 2, "□", "Duplicates")
        self._add_nav_button(layout, 3, "⌁", "Cleanup")
        self._add_nav_button(layout, 4, "✧", "AI Metadata")
        self._add_nav_button(layout, 5, "▤", "Sync Plan")
        self._add_nav_button(layout, 6, "☁", "Cloud Sync")
        self._add_nav_button(layout, 7, "▯", "Phone Connect")
        layout.addWidget(self._sidebar_separator())
        self._add_nav_button(layout, 8, "⚙", "Settings")
        self._add_nav_button(layout, 9, "⚕", "Diagnostics")
        layout.addStretch(1)

        library_card = QWidget()
        library_card.setObjectName("sidebarInfoCard")
        card_layout = QVBoxLayout(library_card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(7)
        info_title = QLabel("Library Location")
        info_title.setObjectName("sidebarInfoTitle")
        self.root_summary_label = QLabel("Library: not configured")
        self.root_summary_label.setObjectName("sidebarInfoBody")
        self.root_summary_label.setWordWrap(True)
        card_layout.addWidget(info_title)
        card_layout.addWidget(self.root_summary_label)
        mini_actions = QGridLayout()
        mini_actions.setContentsMargins(0, 6, 0, 0)
        mini_actions.setHorizontalSpacing(6)
        mini_actions.setVerticalSpacing(6)
        self.check_updates_btn = QPushButton("Updates")
        self.start_menu_shortcut_btn = QPushButton("Shortcut")
        for btn in (self.check_updates_btn, self.start_menu_shortcut_btn):
            btn.setObjectName("sidebarMiniButton")
            btn.setMinimumHeight(32)
        mini_actions.addWidget(self.check_updates_btn, 0, 0)
        mini_actions.addWidget(self.start_menu_shortcut_btn, 0, 1)
        card_layout.addLayout(mini_actions)
        layout.addWidget(library_card)

        self._sync_nav_selection(self.workspace_tabs.currentIndex())
        return sidebar

    def _add_nav_button(self, layout: QVBoxLayout, tab_index: int, icon: str, label: str) -> QPushButton:
        button = QPushButton(f"{icon}   {label}")
        button.setObjectName("navButton")
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setMinimumHeight(46)
        button.setProperty("tabIndex", tab_index)
        button.clicked.connect(lambda _checked=False, index=tab_index: self.workspace_tabs.setCurrentIndex(index))
        self.nav_buttons.append(button)
        layout.addWidget(button)
        return button

    def _sidebar_separator(self) -> QWidget:
        line = QWidget()
        line.setObjectName("sidebarSeparator")
        line.setFixedHeight(1)
        return line

    def _sync_nav_selection(self, index: int) -> None:
        if not hasattr(self, "nav_buttons"):
            return
        for button in self.nav_buttons:
            button.setChecked(int(button.property("tabIndex")) == index)

    def _standard_icon(self, name: str) -> QIcon:
        icon_id = getattr(QStyle.StandardPixmap, name, None)
        if icon_id is None:
            return QIcon()
        return self.style().standardIcon(icon_id)

    def _build_settings_tab(self) -> QWidget:
        self.settings_tab = QWidget()
        page_layout = QVBoxLayout(self.settings_tab)
        page_layout.setContentsMargins(14, 14, 14, 14)
        page_layout.setSpacing(12)
        page_layout.addLayout(
            self._page_header(
                "Settings",
                "Configure library locations, sync behavior, background service, indexing, and cleanup tools.",
            )
        )

        scroll = QScrollArea()
        scroll.setObjectName("settingsScroll")
        scroll.viewport().setObjectName("settingsViewport")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        settings_panel = QWidget()
        settings_panel.setObjectName("settingsPanel")
        settings_layout = QGridLayout(settings_panel)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setHorizontalSpacing(12)
        settings_layout.setVerticalSpacing(12)

        config_group = QGroupBox("Library Locations")
        config_layout = QFormLayout(config_group)
        config_layout.setSpacing(8)
        self.root_edit, root_row = self._path_row(self.on_browse_root)
        self.source_edit, source_row = self._path_row(self.on_browse_source)
        self.blur_csv_edit, blur_csv_row = self._path_row(self.on_browse_blur_csv)
        self.google_drive_status_label = QLabel("Checking for mounted Google Drive...")
        self.google_drive_status_label.setObjectName("pathLabel")
        self.use_google_drive_btn = QPushButton("Use Google Drive")
        self.use_google_drive_btn.setObjectName("toolbarButton")
        self.open_google_drive_btn = QPushButton("Open")
        self.open_google_drive_btn.setObjectName("toolbarButton")
        self.open_google_drive_btn.setFixedWidth(72)
        google_drive_row = QWidget()
        google_drive_layout = QHBoxLayout(google_drive_row)
        google_drive_layout.setContentsMargins(0, 0, 0, 0)
        google_drive_layout.setSpacing(6)
        google_drive_layout.addWidget(self.google_drive_status_label, stretch=1)
        google_drive_layout.addWidget(self.use_google_drive_btn)
        google_drive_layout.addWidget(self.open_google_drive_btn)
        config_layout.addRow("Root folder", root_row)
        config_layout.addRow("Google Drive", google_drive_row)
        config_layout.addRow("Source folder", source_row)
        config_layout.addRow("Blur CSV", blur_csv_row)

        self.save_settings_btn = QPushButton("Save Settings")
        self.save_settings_btn.setObjectName("primaryAction")
        config_layout.addRow("", self.save_settings_btn)

        sync_group = QGroupBox("Sync Rules")
        sync_layout = QGridLayout(sync_group)
        sync_layout.setHorizontalSpacing(8)
        sync_layout.setVerticalSpacing(6)

        self.date_source_combo = QComboBox()
        self.date_source_combo.addItems(list(DATE_SOURCES))
        self.sync_hours_edit = QLineEdit("0-24")
        self.schedule_summary_label = QLabel("Using daily hours: 0-24")
        self.schedule_summary_label.setObjectName("pathLabel")
        self.edit_schedule_btn = QPushButton("Weekly Schedule")
        self.edit_schedule_btn.setObjectName("secondaryAction")
        self.settle_edit = QLineEdit("1.5")
        self.stable_checks_edit = QLineEdit("3")
        self.poll_interval_edit = QLineEdit("0.5")

        sync_layout.addWidget(QLabel("Date source"), 0, 0)
        sync_layout.addWidget(self.date_source_combo, 0, 1)
        sync_layout.addWidget(QLabel("Sync hours"), 1, 0)
        sync_layout.addWidget(self.sync_hours_edit, 1, 1)
        schedule_row = QWidget()
        schedule_row_layout = QHBoxLayout(schedule_row)
        schedule_row_layout.setContentsMargins(0, 0, 0, 0)
        schedule_row_layout.setSpacing(6)
        schedule_row_layout.addWidget(self.schedule_summary_label, stretch=1)
        schedule_row_layout.addWidget(self.edit_schedule_btn)
        sync_layout.addWidget(QLabel("Schedule"), 2, 0)
        sync_layout.addWidget(schedule_row, 2, 1)
        sync_layout.addWidget(QLabel("Settle"), 3, 0)
        sync_layout.addWidget(self.settle_edit, 3, 1)
        sync_layout.addWidget(QLabel("Stable checks"), 4, 0)
        sync_layout.addWidget(self.stable_checks_edit, 4, 1)
        sync_layout.addWidget(QLabel("Poll interval"), 5, 0)
        sync_layout.addWidget(self.poll_interval_edit, 5, 1)

        self.recursive_check = QCheckBox("Recursive source scan")
        self.include_nonmedia_check = QCheckBox("Include non-media files")
        self.full_hash_check = QCheckBox("Also fingerprint library index entries")
        self.dry_run_check = QCheckBox("Dry-run mode")
        self.delete_after_sync_check = QCheckBox("Delete source after batch sync")
        self.watch_delete_check = QCheckBox("Delete source in background sync")
        self.autostart_background_check = QCheckBox("Autostart background on launch")
        self.autostart_windows_check = QCheckBox("Open on Windows startup")
        self.start_minimized_check = QCheckBox("Windows startup opens minimized")
        self.minimize_to_tray_check = QCheckBox("Close button hides to tray")

        sync_layout.addWidget(self.recursive_check, 6, 0, 1, 2)
        sync_layout.addWidget(self.include_nonmedia_check, 7, 0, 1, 2)
        sync_layout.addWidget(self.full_hash_check, 8, 0, 1, 2)
        sync_layout.addWidget(self.dry_run_check, 9, 0, 1, 2)
        sync_layout.addWidget(self.delete_after_sync_check, 10, 0, 1, 2)
        sync_layout.addWidget(self.watch_delete_check, 11, 0, 1, 2)
        sync_layout.addWidget(self.autostart_background_check, 12, 0, 1, 2)
        sync_layout.addWidget(self.autostart_windows_check, 13, 0, 1, 2)
        sync_layout.addWidget(self.start_minimized_check, 14, 0, 1, 2)
        sync_layout.addWidget(self.minimize_to_tray_check, 15, 0, 1, 2)

        service_group = QGroupBox("Background Service")
        service_layout = QGridLayout(service_group)
        service_layout.setHorizontalSpacing(8)
        service_layout.setVerticalSpacing(6)
        self.install_service_btn = QPushButton("Install")
        self.start_service_btn = QPushButton("Start")
        self.stop_service_btn = QPushButton("Stop")
        self.uninstall_service_btn = QPushButton("Uninstall")
        for btn in (
            self.install_service_btn,
            self.start_service_btn,
            self.stop_service_btn,
            self.uninstall_service_btn,
        ):
            btn.setObjectName("secondaryAction")
        service_layout.addWidget(self.install_service_btn, 0, 0)
        service_layout.addWidget(self.start_service_btn, 0, 1)
        service_layout.addWidget(self.stop_service_btn, 1, 0)
        service_layout.addWidget(self.uninstall_service_btn, 1, 1)
        self.service_note_label = QLabel("Runs sync without the GUI.")
        self.service_note_label.setObjectName("pathLabel")
        service_layout.addWidget(self.service_note_label, 2, 0, 1, 2)
        if sys.platform == "win32":
            self.service_note_label.setText("Uses Windows Service commands.")
        elif sys.platform.startswith("linux"):
            self.service_note_label.setText("Uses a systemd user service.")
        else:
            for btn in (
                self.install_service_btn,
                self.start_service_btn,
                self.stop_service_btn,
                self.uninstall_service_btn,
            ):
                btn.setEnabled(False)
            self.service_note_label.setText("Use foreground background sync on this platform.")

        index_group = QGroupBox("Library Index")
        index_layout = QGridLayout(index_group)
        index_layout.setHorizontalSpacing(8)
        index_layout.setVerticalSpacing(6)
        self.rebuild_index_btn = QPushButton("Rebuild Index")
        self.rebuild_index_btn.setObjectName("secondaryAction")
        self.import_blur_index_btn = QPushButton("Import Blur CSV")
        self.import_blur_index_btn.setObjectName("secondaryAction")
        self.index_note_label = QLabel("SQLite cache for search and AI metadata.")
        self.index_note_label.setObjectName("pathLabel")
        index_layout.addWidget(self.rebuild_index_btn, 0, 0)
        index_layout.addWidget(self.import_blur_index_btn, 0, 1)
        index_layout.addWidget(self.index_note_label, 1, 0, 1, 2)

        blur_group = QGroupBox("Blur Tools")
        blur_layout = QGridLayout(blur_group)
        blur_layout.setHorizontalSpacing(8)
        blur_layout.setVerticalSpacing(6)

        self.blur_threshold_edit = QLineEdit("120.0")
        self.blur_top_edit = QLineEdit("0")
        self.auto_delete_max_edit = QLineEdit("50")
        self.auto_delete_hard_check = QCheckBox("Hard delete (skip recycle bin)")

        blur_layout.addWidget(QLabel("Threshold"), 0, 0)
        blur_layout.addWidget(self.blur_threshold_edit, 0, 1)
        blur_layout.addWidget(QLabel("Top N"), 1, 0)
        blur_layout.addWidget(self.blur_top_edit, 1, 1)
        blur_layout.addWidget(QLabel("Auto delete max"), 2, 0)
        blur_layout.addWidget(self.auto_delete_max_edit, 2, 1)
        blur_layout.addWidget(self.auto_delete_hard_check, 3, 0, 1, 2)

        self.blur_scan_btn = QPushButton("Scan Blur")
        self.blur_scan_btn.setObjectName("secondaryAction")
        self.blur_review_btn = QPushButton("Manual Review")
        self.blur_review_btn.setObjectName("secondaryAction")
        self.blur_queue_btn = QPushButton("Queue Blur Candidates")
        self.blur_queue_btn.setObjectName("secondaryAction")
        self.blur_autodelete_btn = QPushButton("Auto Delete")
        self.blur_autodelete_btn.setObjectName("dangerAction")
        blur_layout.addWidget(self.blur_scan_btn, 4, 0, 1, 2)
        blur_layout.addWidget(self.blur_review_btn, 5, 0, 1, 2)
        blur_layout.addWidget(self.blur_queue_btn, 6, 0, 1, 2)
        blur_layout.addWidget(self.blur_autodelete_btn, 7, 0, 1, 2)

        settings_layout.addWidget(config_group, 0, 0)
        settings_layout.addWidget(sync_group, 0, 1, 2, 1)
        settings_layout.addWidget(index_group, 1, 0)
        settings_layout.addWidget(service_group, 2, 0)
        settings_layout.addWidget(blur_group, 2, 1)
        settings_layout.setColumnStretch(0, 1)
        settings_layout.setColumnStretch(1, 1)
        scroll.setWidget(settings_panel)
        page_layout.addWidget(scroll, stretch=1)
        return self.settings_tab

    def _build_compare_tab(self) -> QWidget:
        self.compare_tab = QWidget()
        layout = QVBoxLayout(self.compare_tab)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)
        layout.addLayout(
            self._page_header(
                "Sync Plan",
                "1. Choose Source and Library in Settings. 2. Preview destinations. 3. Select Sync Now. First 300 files shown.",
            )
        )
        preview_split = QSplitter()
        preview_split.setChildrenCollapsible(False)

        src_group = QGroupBox("Source")
        src_layout = QVBoxLayout(src_group)
        self.source_preview_path = QLabel("-")
        self.source_preview_path.setObjectName("pathLabel")
        self.source_preview_list = QListWidget()
        src_layout.addWidget(self.source_preview_path)
        src_layout.addWidget(self.source_preview_list)

        dst_group = QGroupBox("Target / Root Preview")
        dst_layout = QVBoxLayout(dst_group)
        self.target_preview_path = QLabel("-")
        self.target_preview_path.setObjectName("pathLabel")
        self.target_preview_list = QListWidget()
        dst_layout.addWidget(self.target_preview_path)
        dst_layout.addWidget(self.target_preview_list)

        preview_split.addWidget(src_group)
        preview_split.addWidget(dst_group)
        preview_split.setSizes([530, 530])

        layout.addWidget(preview_split, stretch=1)
        return self.compare_tab

    def _build_cloud_tab(self) -> QWidget:
        self.cloud_tab = QWidget()
        layout = QVBoxLayout(self.cloud_tab)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)
        layout.addLayout(
            self._page_header(
                "Cloud Sync",
                "Connect a Google account, review upload/download plans, and run optional Drive transfers.",
            )
        )

        setup_group = QGroupBox("Google Drive Account")
        setup_layout = QFormLayout(setup_group)
        setup_layout.setSpacing(8)
        self.google_credentials_edit, credentials_row = self._path_row(self.on_browse_google_credentials)
        self.google_client_id_edit = QLineEdit()
        self.google_client_id_edit.setPlaceholderText("Client ID from Google Cloud OAuth desktop app")
        self.google_client_secret_edit = QLineEdit()
        self.google_client_secret_edit.setPlaceholderText("Client secret")
        self.google_client_secret_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.google_save_credentials_btn = QPushButton("Save OAuth JSON")
        self.google_save_credentials_btn.setObjectName("secondaryAction")
        secret_row = QWidget()
        secret_layout = QHBoxLayout(secret_row)
        secret_layout.setContentsMargins(0, 0, 0, 0)
        secret_layout.setSpacing(6)
        secret_layout.addWidget(self.google_client_secret_edit, stretch=1)
        secret_layout.addWidget(self.google_save_credentials_btn)
        self.google_token_edit = QLineEdit()
        self.google_remote_root_edit = QLineEdit(DEFAULT_REMOTE_ROOT_NAME)
        self.google_parent_id_edit = QLineEdit()
        self.google_parent_id_edit.setPlaceholderText("Optional existing Drive folder ID")
        self.google_compute_hash_check = QCheckBox("Use stronger hash checks for cloud plans")
        self.google_overwrite_check = QCheckBox("Allow download overwrite when local files differ")
        self.google_status_label = QLabel("Google Drive is optional and disabled until OAuth is configured.")
        self.google_status_label.setObjectName("pathLabel")
        self.google_status_label.setWordWrap(True)

        setup_layout.addRow("OAuth client JSON", credentials_row)
        setup_layout.addRow("Client ID", self.google_client_id_edit)
        setup_layout.addRow("Client secret", secret_row)
        setup_layout.addRow("Token file", self.google_token_edit)
        setup_layout.addRow("Remote folder", self.google_remote_root_edit)
        setup_layout.addRow("Parent folder ID", self.google_parent_id_edit)
        setup_layout.addRow("", self.google_compute_hash_check)
        setup_layout.addRow("", self.google_overwrite_check)
        setup_layout.addRow("", self.google_status_label)

        action_group = QGroupBox("Actions")
        action_layout = QGridLayout(action_group)
        action_layout.setHorizontalSpacing(8)
        action_layout.setVerticalSpacing(8)
        self.google_auth_btn = QPushButton("Authenticate")
        self.google_auth_btn.setObjectName("primaryAction")
        self.google_plan_upload_btn = QPushButton("Plan Upload")
        self.google_plan_upload_btn.setObjectName("secondaryAction")
        self.google_upload_btn = QPushButton("Upload")
        self.google_upload_btn.setObjectName("secondaryAction")
        self.google_plan_download_btn = QPushButton("Plan Download")
        self.google_plan_download_btn.setObjectName("secondaryAction")
        self.google_download_btn = QPushButton("Download Missing")
        self.google_download_btn.setObjectName("secondaryAction")

        action_layout.addWidget(self.google_auth_btn, 0, 0, 1, 2)
        action_layout.addWidget(self.google_plan_upload_btn, 1, 0)
        action_layout.addWidget(self.google_upload_btn, 1, 1)
        action_layout.addWidget(self.google_plan_download_btn, 2, 0)
        action_layout.addWidget(self.google_download_btn, 2, 1)

        note = QLabel(
            "This uses a Google OAuth desktop client JSON, not an API key. Plans are written as CSV files in the "
            "library root. Downloads skip conflicts unless overwrite is enabled."
        )
        note.setObjectName("pathLabel")
        note.setWordWrap(True)
        action_layout.addWidget(note, 3, 0, 1, 2)

        layout.addWidget(setup_group)
        layout.addWidget(action_group)
        layout.addStretch(1)
        return self.cloud_tab

    def _build_phone_connect_tab(self) -> QWidget:
        self.phone_connect_tab = QWidget()
        layout = QVBoxLayout(self.phone_connect_tab)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        header = self._page_header(
            "Phone Connect",
            "Receive phone photos over your local network, then sort the inbox into the desktop library.",
        )
        self.lan_refresh_urls_btn = QPushButton("Refresh URLs")
        self.lan_refresh_urls_btn.setObjectName("secondaryAction")
        self.lan_refresh_urls_btn.setIcon(self._standard_icon("SP_BrowserReload"))
        header.addWidget(self.lan_refresh_urls_btn)
        layout.addLayout(header)

        top_row = QHBoxLayout()
        top_row.setSpacing(12)

        receiver_group = QGroupBox("Receiver")
        receiver_layout = QFormLayout(receiver_group)
        receiver_layout.setSpacing(8)
        self.lan_inbox_edit, inbox_row = self._path_row(self.on_browse_lan_inbox)
        self.lan_port_edit = QLineEdit(str(DEFAULT_LAN_PORT))
        self.lan_port_edit.setFixedWidth(110)
        self.lan_pairing_code_edit = QLineEdit()
        self.lan_pairing_code_edit.setMaxLength(12)
        self.lan_pairing_code_edit.setFixedWidth(140)
        self.lan_pairing_code_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lan_pairing_code_edit.setObjectName("pairingCode")
        self.lan_generate_code_btn = QPushButton("New Code")
        self.lan_generate_code_btn.setObjectName("toolbarButton")
        self.lan_generate_code_btn.setIcon(self._standard_icon("SP_DialogResetButton"))
        code_row = QWidget()
        code_layout = QHBoxLayout(code_row)
        code_layout.setContentsMargins(0, 0, 0, 0)
        code_layout.setSpacing(6)
        code_layout.addWidget(self.lan_pairing_code_edit)
        code_layout.addWidget(self.lan_generate_code_btn)
        code_layout.addStretch(1)
        self.lan_status_label = QLabel("Receiver stopped")
        self.lan_status_label.setObjectName("pathLabel")
        self.lan_status_label.setWordWrap(True)
        receiver_layout.addRow("Inbox", inbox_row)
        receiver_layout.addRow("Port", self.lan_port_edit)
        receiver_layout.addRow("Pairing code", code_row)
        receiver_layout.addRow("Status", self.lan_status_label)

        actions_group = QGroupBox("Actions")
        actions_layout = QGridLayout(actions_group)
        actions_layout.setHorizontalSpacing(8)
        actions_layout.setVerticalSpacing(8)
        self.lan_start_btn = QPushButton("Start Receiver")
        self.lan_start_btn.setObjectName("primaryAction")
        self.lan_start_btn.setIcon(self._standard_icon("SP_MediaPlay"))
        self.lan_stop_btn = QPushButton("Stop")
        self.lan_stop_btn.setObjectName("quietAction")
        self.lan_stop_btn.setIcon(self._standard_icon("SP_MediaStop"))
        self.lan_open_inbox_btn = QPushButton("Open Inbox")
        self.lan_open_inbox_btn.setObjectName("secondaryAction")
        self.lan_open_inbox_btn.setIcon(self._standard_icon("SP_DirOpenIcon"))
        self.lan_sync_inbox_btn = QPushButton("Sort Inbox")
        self.lan_sync_inbox_btn.setObjectName("secondaryAction")
        self.lan_sync_inbox_btn.setIcon(self._standard_icon("SP_DialogApplyButton"))
        actions_layout.addWidget(self.lan_start_btn, 0, 0)
        actions_layout.addWidget(self.lan_stop_btn, 0, 1)
        actions_layout.addWidget(self.lan_open_inbox_btn, 1, 0)
        actions_layout.addWidget(self.lan_sync_inbox_btn, 1, 1)
        self.lan_action_note = QLabel("Uploads land in the inbox first; sorting reuses the desktop sync rules.")
        self.lan_action_note.setObjectName("pathLabel")
        self.lan_action_note.setWordWrap(True)
        actions_layout.addWidget(self.lan_action_note, 2, 0, 1, 2)

        top_row.addWidget(receiver_group, stretch=3)
        top_row.addWidget(actions_group, stretch=2)
        layout.addLayout(top_row)

        endpoint_group = QGroupBox("Connection")
        endpoint_layout = QVBoxLayout(endpoint_group)
        endpoint_layout.setSpacing(8)
        self.lan_urls_view = QPlainTextEdit()
        self.lan_urls_view.setReadOnly(True)
        self.lan_urls_view.setMaximumHeight(132)
        endpoint_layout.addWidget(self.lan_urls_view)
        layout.addWidget(endpoint_group)

        recent_group = QGroupBox("Recent Uploads")
        recent_layout = QVBoxLayout(recent_group)
        self.lan_recent_list = QListWidget()
        recent_layout.addWidget(self.lan_recent_list)
        layout.addWidget(recent_group, stretch=1)
        return self.phone_connect_tab

    def _build_diagnostics_tab(self) -> QWidget:
        self.diagnostics_tab = QWidget()
        layout = QVBoxLayout(self.diagnostics_tab)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)
        layout.addLayout(
            self._page_header(
                "Diagnostics",
                "Operational log for troubleshooting sync, indexing, cleanup, and background tasks.",
            )
        )
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view, stretch=1)
        return self.diagnostics_tab

    def _page_header(self, title: str, subtitle: str) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        text = QWidget()
        text_layout = QVBoxLayout(text)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(3)
        title_label = QLabel(title)
        title_label.setObjectName("pageTitle")
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("pageSubtitle")
        subtitle_label.setWordWrap(True)
        text_layout.addWidget(title_label)
        text_layout.addWidget(subtitle_label)
        layout.addWidget(text, stretch=1)
        return layout

    def _new_table(self, headers: List[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        return table

    def _metric_card(
        self,
        title: str,
        value: str,
        caption: str,
        icon: str,
        accent: str,
    ) -> tuple[QWidget, QLabel, QLabel]:
        card = QWidget()
        card.setObjectName("metricCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(12)
        icon_label = QLabel(icon)
        icon_label.setObjectName("metricIcon")
        icon_label.setFixedSize(44, 44)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setStyleSheet(
            f"background: {accent}; border: 1px solid {accent}; border-radius: 22px; color: #ffffff;"
        )
        title_label = QLabel(title)
        title_label.setObjectName("metricTitle")
        title_label.setWordWrap(True)
        top_row.addWidget(icon_label)
        top_row.addWidget(title_label, stretch=1)
        layout.addLayout(top_row)
        value_label = QLabel(value)
        value_label.setObjectName("metricValue")
        caption_label = QLabel(caption)
        caption_label.setObjectName("metricCaption")
        caption_label.setWordWrap(True)
        layout.addWidget(value_label)
        layout.addWidget(caption_label)
        layout.addStretch(1)
        return card, value_label, caption_label

    def _build_dashboard_tab(self) -> QWidget:
        self.dashboard_tab = QWidget()
        self.dashboard_tab.setObjectName("dashboardTab")
        layout = QVBoxLayout(self.dashboard_tab)
        layout.setContentsMargins(24, 22, 30, 12)
        layout.setSpacing(16)

        header = self._page_header(
            "Library Dashboard",
            "A quick read on sync readiness, cleanup pressure, indexing health, and recent activity.",
        )
        self.dashboard_refresh_btn = QPushButton("Refresh")
        self.dashboard_refresh_btn.setObjectName("secondaryAction")
        self.dashboard_refresh_btn.setIcon(self._standard_icon("SP_BrowserReload"))
        self.dashboard_export_btn = QPushButton("Export Report")
        self.dashboard_export_btn.setObjectName("secondaryAction")
        self.dashboard_export_btn.setIcon(self._standard_icon("SP_DialogSaveButton"))
        self.about_btn = QPushButton("About")
        self.about_btn.setIcon(self._standard_icon("SP_MessageBoxInformation"))
        self.about_btn.setObjectName("toolbarButton")
        header.addWidget(self.dashboard_refresh_btn)
        header.addWidget(self.dashboard_export_btn)
        header.addWidget(self.about_btn)
        layout.addLayout(header)

        metrics = QGridLayout()
        metrics.setHorizontalSpacing(12)
        metrics.setVerticalSpacing(12)
        total_card, self.metric_total_files, _ = self._metric_card(
            "Media Files",
            "0",
            "Indexed library items",
            "▧",
            "#0ea5e9",
        )
        present_card, self.metric_present_files, _ = self._metric_card(
            "Ready Files",
            "0",
            "Currently present in library",
            "✓",
            "#14b8a6",
        )
        ai_card, self.metric_ai_count, _ = self._metric_card(
            "AI Metadata",
            "0",
            "Captions, tags, and OCR rows",
            "AI",
            "#8b5cf6",
        )
        blur_card, self.metric_blur_count, _ = self._metric_card(
            "Blur Review",
            "0",
            "Candidate photos to inspect",
            "⌗",
            "#f97316",
        )
        queue_card, self.metric_queue_count, _ = self._metric_card(
            "Cleanup Queue",
            "0",
            "Files waiting for review",
            "⌫",
            "#d97706",
        )
        errors_card, self.metric_error_count, _ = self._metric_card(
            "Sync Errors",
            "0",
            "Events needing attention",
            "!",
            "#ef4444",
        )
        for col, card in enumerate((total_card, present_card, ai_card, blur_card, queue_card, errors_card)):
            metrics.addWidget(card, 0, col)
            metrics.setColumnStretch(col, 1)
        layout.addLayout(metrics)

        next_card = QWidget()
        next_card.setObjectName("libraryIndexCard")
        next_layout = QHBoxLayout(next_card)
        next_layout.setContentsMargins(26, 22, 26, 22)
        next_layout.setSpacing(28)
        library_art = QLabel("▧\n⌕")
        library_art.setObjectName("libraryIndexArt")
        library_art.setFixedSize(150, 118)
        library_art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        next_text = QWidget()
        next_text_layout = QVBoxLayout(next_text)
        next_text_layout.setContentsMargins(0, 0, 0, 0)
        next_text_layout.setSpacing(8)
        self.next_action_title_label = QLabel("Build your library index")
        self.next_action_title_label.setObjectName("nextActionTitle")
        self.next_action_body_label = QLabel(
            "No indexed media yet. Confirm folders in Settings, then build the library index."
        )
        self.next_action_body_label.setObjectName("nextActionBody")
        self.next_action_body_label.setWordWrap(True)
        next_text_layout.addWidget(self.next_action_title_label)
        next_text_layout.addWidget(self.next_action_body_label)
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 12, 0, 0)
        action_row.setSpacing(10)
        self.next_action_btn = QPushButton("Open Settings")
        self.next_action_btn.setObjectName("primaryAction")
        self.next_action_btn.setIcon(self._standard_icon("SP_FileDialogContentsView"))
        self.next_settings_btn = QPushButton("Learn how it works")
        self.next_settings_btn.setObjectName("quietAction")
        self.next_settings_btn.setIcon(self._standard_icon("SP_MessageBoxQuestion"))
        action_row.addWidget(self.next_action_btn)
        action_row.addWidget(self.next_settings_btn)
        action_row.addStretch(1)
        next_text_layout.addLayout(action_row)
        next_text_layout.addStretch(1)

        separator = QWidget()
        separator.setObjectName("verticalSeparator")
        separator.setFixedWidth(1)

        overview = self._build_system_overview()
        next_layout.addWidget(library_art)
        next_layout.addWidget(next_text, stretch=1)
        next_layout.addWidget(separator)
        next_layout.addWidget(overview, stretch=1)
        layout.addWidget(next_card)

        self.dashboard_year_table = self._new_table(["Year", "Files"])
        self.dashboard_folder_table = self._new_table(["Folder", "Files", "Size"])
        self.dashboard_events_table = self._new_table(["Time", "Mode", "Status", "Source", "Target"])
        years_panel, self.dashboard_year_empty = self._dashboard_panel(
            "Years",
            "▣",
            self.dashboard_year_table,
            "No data yet",
            "Years will appear here once the library is indexed.",
        )
        folders_panel, self.dashboard_folder_empty = self._dashboard_panel(
            "Top Folders",
            "▱",
            self.dashboard_folder_table,
            "No folders yet",
            "Your top folders will be shown here after indexing.",
        )
        events_panel, self.dashboard_events_empty = self._dashboard_panel(
            "Recent Sync Events",
            "↻",
            self.dashboard_events_table,
            "No events yet",
            "Sync activity will appear here once syncing begins.",
        )

        lower_grid = QGridLayout()
        lower_grid.setHorizontalSpacing(12)
        lower_grid.setVerticalSpacing(12)
        lower_grid.addWidget(years_panel, 0, 0)
        lower_grid.addWidget(folders_panel, 0, 1)
        lower_grid.addWidget(events_panel, 0, 2)
        lower_grid.setColumnStretch(0, 1)
        lower_grid.setColumnStretch(1, 1)
        lower_grid.setColumnStretch(2, 2)
        layout.addLayout(lower_grid, stretch=1)

        self.dashboard_summary_label = QLabel("Index dashboard not loaded yet.")
        self.dashboard_summary_label.setObjectName("dashboardStatusLine")
        self.dashboard_summary_label.setVisible(False)
        layout.addWidget(self.dashboard_summary_label)
        layout.addWidget(self._build_dashboard_footer())
        self._set_dashboard_empty_states()
        return self.dashboard_tab

    def _build_system_overview(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("systemOverview")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        title = QLabel("System Overview")
        title.setObjectName("systemOverviewTitle")
        layout.addWidget(title)

        grid = QGridLayout()
        grid.setContentsMargins(0, 2, 0, 0)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(12)
        self.dashboard_overview_values: Dict[str, QLabel] = {}
        items = [
            ("Files", "files", "#14b8a6"),
            ("Present", "present", "#14b8a6"),
            ("AI metadata", "ai", "#14b8a6"),
            ("Blur candidates", "blur", "#14b8a6"),
            ("Delete queue", "queue", "#14b8a6"),
            ("Sync errors", "errors", "#ef4444"),
        ]
        for index, (label, key, color) in enumerate(items):
            row = index % 3
            column = (index // 3) * 2
            item = self._overview_item(label, key, color)
            grid.addWidget(item, row, column, 1, 2)
        layout.addLayout(grid)
        layout.addStretch(1)
        return panel

    def _overview_item(self, label: str, key: str, color: str) -> QWidget:
        item = QWidget()
        item.setObjectName("overviewItem")
        layout = QHBoxLayout(item)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        dot = QLabel()
        dot.setObjectName("overviewDot")
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(f"background: {color}; border-radius: 4px;")
        name = QLabel(f"{label}:")
        name.setObjectName("overviewLabel")
        value = QLabel("0")
        value.setObjectName("overviewValue")
        self.dashboard_overview_values[key] = value
        layout.addWidget(dot)
        layout.addWidget(name)
        layout.addWidget(value)
        layout.addStretch(1)
        return item

    def _build_dashboard_footer(self) -> QWidget:
        footer = QWidget()
        footer.setObjectName("dashboardFooter")
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        privacy = QLabel("All data is stored locally and never leaves your device.")
        privacy.setObjectName("footerText")
        version = QLabel(f"Photo Manager Pro v{self._app_version()}")
        version.setObjectName("footerText")
        layout.addWidget(privacy)
        layout.addStretch(1)
        layout.addWidget(version)
        return footer

    def _dashboard_panel(
        self,
        title: str,
        icon: str,
        table: QTableWidget,
        empty_title: str,
        empty_body: str,
    ) -> tuple[QWidget, QLabel]:
        panel = QWidget()
        panel.setObjectName("tablePanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        panel_header = QWidget()
        panel_header.setObjectName("tablePanelHeader")
        header_layout = QHBoxLayout(panel_header)
        header_layout.setContentsMargins(16, 13, 16, 12)
        header_layout.setSpacing(9)
        icon_label = QLabel(icon)
        icon_label.setObjectName("panelIcon")
        icon_label.setFixedSize(22, 22)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label = QLabel(title)
        title_label.setObjectName("sectionTitle")
        header_layout.addWidget(icon_label)
        header_layout.addWidget(title_label)
        header_layout.addStretch(1)
        layout.addWidget(panel_header)
        table.setMinimumHeight(46)
        layout.addWidget(table)
        empty_label = QLabel(f"{empty_title}\n{empty_body}")
        empty_label.setObjectName("tableEmptyState")
        empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_label.setWordWrap(True)
        layout.addWidget(empty_label, stretch=1)
        return panel, empty_label

    def _build_gallery_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)
        layout.addLayout(
            self._page_header(
                "Gallery",
                "Browse indexed media, inspect metadata, and queue cleanup decisions without leaving the app.",
            )
        )

        filters = QHBoxLayout()
        self.gallery_year_combo = QComboBox()
        self.gallery_status_combo = QComboBox()
        self.gallery_status_combo.addItems(["present", "all"])
        self.gallery_blur_max_edit = QLineEdit()
        self.gallery_blur_max_edit.setPlaceholderText("Max blur score")
        self.gallery_search_edit = QLineEdit()
        self.gallery_search_edit.setPlaceholderText("Search path, caption, tag, OCR")
        self.gallery_limit_edit = QLineEdit("300")
        self.gallery_limit_edit.setFixedWidth(70)
        self.gallery_refresh_btn = QPushButton("Load")
        self.gallery_refresh_btn.setObjectName("secondaryAction")
        self.gallery_open_btn = QPushButton("Open")
        self.gallery_queue_btn = QPushButton("Queue Delete")
        self.gallery_queue_btn.setObjectName("cautionAction")
        filters.addWidget(QLabel("Year"))
        filters.addWidget(self.gallery_year_combo)
        filters.addWidget(QLabel("Status"))
        filters.addWidget(self.gallery_status_combo)
        filters.addWidget(QLabel("Blur"))
        filters.addWidget(self.gallery_blur_max_edit)
        filters.addWidget(self.gallery_search_edit, stretch=1)
        filters.addWidget(QLabel("Limit"))
        filters.addWidget(self.gallery_limit_edit)
        filters.addWidget(self.gallery_refresh_btn)
        filters.addWidget(self.gallery_open_btn)
        filters.addWidget(self.gallery_queue_btn)
        layout.addLayout(filters)

        split = QSplitter()
        split.setChildrenCollapsible(False)
        self.gallery_list = QListWidget()
        self.gallery_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.gallery_list.setIconSize(QSize(160, 160))
        self.gallery_list.setGridSize(QSize(220, 220))
        self.gallery_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.gallery_list.setMovement(QListWidget.Movement.Static)
        self.gallery_list.setSpacing(8)
        self.gallery_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

        preview_panel = QWidget()
        preview_panel.setObjectName("inspectorPanel")
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(8, 0, 0, 0)
        self.gallery_preview_label = QLabel()
        self.gallery_preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.gallery_preview_label.setMinimumSize(320, 320)
        self.gallery_preview_label.setStyleSheet(
            "background: #07090d; border: 1px solid #2a3140; border-radius: 8px;"
        )
        self.gallery_details = QPlainTextEdit()
        self.gallery_details.setReadOnly(True)
        self.gallery_details.setMinimumHeight(140)
        preview_layout.addWidget(self.gallery_preview_label, stretch=1)
        preview_layout.addWidget(self.gallery_details)

        split.addWidget(self.gallery_list)
        split.addWidget(preview_panel)
        split.setSizes([650, 330])
        layout.addWidget(split, stretch=1)
        return tab

    def _build_duplicates_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)
        layout.addLayout(
            self._page_header(
                "Duplicate Review",
                "Compare exact matches side by side and queue the files you want to remove for safe cleanup.",
            )
        )

        toolbar = QHBoxLayout()
        self.duplicate_scan_btn = QPushButton("Scan Duplicates")
        self.duplicate_scan_btn.setObjectName("secondaryAction")
        self.duplicate_cancel_scan_btn = QPushButton("Cancel Scan")
        self.duplicate_cancel_scan_btn.setObjectName("secondaryAction")
        self.duplicate_cancel_scan_btn.setEnabled(False)
        self.duplicate_queue_selected_btn = QPushButton("Queue Selected")
        self.duplicate_queue_all_btn = QPushButton("Queue All")
        self.duplicate_queue_all_btn.setObjectName("cautionAction")
        self.duplicate_open_keep_btn = QPushButton("Open Keep")
        self.duplicate_open_remove_btn = QPushButton("Open Remove")
        toolbar.addWidget(self.duplicate_scan_btn)
        toolbar.addWidget(self.duplicate_cancel_scan_btn)
        toolbar.addStretch(1)
        toolbar.addWidget(self.duplicate_open_keep_btn)
        toolbar.addWidget(self.duplicate_open_remove_btn)
        toolbar.addWidget(self.duplicate_queue_selected_btn)
        toolbar.addWidget(self.duplicate_queue_all_btn)
        layout.addLayout(toolbar)

        split = QSplitter()
        split.setChildrenCollapsible(False)
        self.duplicates_table = self._new_table(["Remove", "Keep", "Size", "Reason"])
        preview = QWidget()
        preview.setObjectName("inspectorPanel")
        preview_layout = QGridLayout(preview)
        self.duplicate_keep_preview = QLabel("Keep")
        self.duplicate_remove_preview = QLabel("Remove")
        for label in (self.duplicate_keep_preview, self.duplicate_remove_preview):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setMinimumSize(220, 220)
            label.setStyleSheet("background: #07090d; border: 1px solid #2a3140; border-radius: 8px;")
        self.duplicate_details = QPlainTextEdit()
        self.duplicate_details.setReadOnly(True)
        preview_layout.addWidget(QLabel("Keep"), 0, 0)
        preview_layout.addWidget(QLabel("Duplicate"), 0, 1)
        preview_layout.addWidget(self.duplicate_keep_preview, 1, 0)
        preview_layout.addWidget(self.duplicate_remove_preview, 1, 1)
        preview_layout.addWidget(self.duplicate_details, 2, 0, 1, 2)
        split.addWidget(self.duplicates_table)
        split.addWidget(preview)
        split.setSizes([620, 360])
        layout.addWidget(split, stretch=1)
        return tab

    def _build_delete_queue_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)
        layout.addLayout(
            self._page_header(
                "Cleanup Queue",
                "Review staged delete decisions before moving anything to the system recycle bin.",
            )
        )

        toolbar = QHBoxLayout()
        self.delete_status_combo = QComboBox()
        self.delete_status_combo.addItems(["queued", "all", "trashed", "cancelled", "missing", "error"])
        self.delete_refresh_btn = QPushButton("Refresh")
        self.delete_trash_selected_btn = QPushButton("Trash Selected")
        self.delete_trash_all_btn = QPushButton("Trash All Queued")
        self.delete_trash_all_btn.setObjectName("dangerAction")
        self.delete_cancel_btn = QPushButton("Cancel Selected")
        self.delete_export_btn = QPushButton("Export CSV")
        self.delete_recycle_btn = QPushButton("Open Recycle Bin")
        toolbar.addWidget(QLabel("Status"))
        toolbar.addWidget(self.delete_status_combo)
        toolbar.addWidget(self.delete_refresh_btn)
        toolbar.addStretch(1)
        toolbar.addWidget(self.delete_cancel_btn)
        toolbar.addWidget(self.delete_trash_selected_btn)
        toolbar.addWidget(self.delete_trash_all_btn)
        toolbar.addWidget(self.delete_export_btn)
        toolbar.addWidget(self.delete_recycle_btn)
        layout.addLayout(toolbar)

        self.delete_table = self._new_table(["ID", "Path", "Reason", "Source", "Status", "Created"])
        layout.addWidget(self.delete_table, stretch=1)
        return tab

    def _build_ai_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)
        layout.addLayout(
            self._page_header(
                "AI Metadata",
                "Run lightweight local tagging and search captions, tags, and OCR text stored in the index.",
            )
        )

        toolbar = QHBoxLayout()
        self.ai_limit_edit = QLineEdit("100")
        self.ai_limit_edit.setFixedWidth(70)
        self.ai_only_missing_check = QCheckBox("Only missing metadata")
        self.ai_only_missing_check.setChecked(True)
        self.ai_run_btn = QPushButton("Run Light AI")
        self.ai_run_btn.setObjectName("secondaryAction")
        self.ai_search_edit = QLineEdit()
        self.ai_search_edit.setPlaceholderText("Search captions, tags, OCR")
        self.ai_search_btn = QPushButton("Search")
        self.ai_open_btn = QPushButton("Open Selected")
        toolbar.addWidget(QLabel("Limit"))
        toolbar.addWidget(self.ai_limit_edit)
        toolbar.addWidget(self.ai_only_missing_check)
        toolbar.addWidget(self.ai_run_btn)
        toolbar.addSpacing(12)
        toolbar.addWidget(self.ai_search_edit, stretch=1)
        toolbar.addWidget(self.ai_search_btn)
        toolbar.addWidget(self.ai_open_btn)
        layout.addLayout(toolbar)

        self.ai_results_table = self._new_table(["Path", "Year", "Tags", "Caption", "Backend"])
        layout.addWidget(self.ai_results_table, stretch=1)
        return tab

    def _path_row(self, browse_callback):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        edit = QLineEdit()
        button = QPushButton("Browse")
        button.setObjectName("toolbarButton")
        button.setFixedWidth(88)
        button.clicked.connect(browse_callback)
        layout.addWidget(edit, stretch=1)
        layout.addWidget(button)
        return edit, row

    def _apply_theme(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow { background: #070b11; color: #edf5ff; }
            QWidget#appRoot { background: #070b11; color: #edf5ff; }
            QWidget#workspaceShell { background: #09111b; }
            QWidget#dashboardTab { background: #09111b; }
            QWidget { color: #d8e5f2; font-size: 12px; }
            QLabel { color: #d8e5f2; }
            QWidget#commandBar {
                background: #070b11;
                border-bottom: 1px solid #1a2736;
            }
            QLabel#appLogo {
                background: #0e2638;
                border: 1px solid #0ea5e9;
                border-radius: 9px;
                color: #67e8f9;
                font-size: 12px;
                font-weight: 900;
            }
            QLabel#appTitle {
                color: #f8fbff;
                font-size: 22px;
                font-weight: 900;
            }
            QLabel#appSubtitle {
                color: #9aabbd;
                font-size: 12px;
                font-weight: 600;
            }
            QLabel#statusPill {
                color: #fbbf24;
                background: #2a1d08;
                border: 1px solid #7c4b10;
                border-radius: 8px;
                padding: 6px 14px;
                font-weight: 900;
            }
            QWidget#sidebar {
                background: #09111b;
                border-right: 1px solid #1a2736;
            }
            QPushButton#navButton {
                background: transparent;
                border: 1px solid transparent;
                border-radius: 7px;
                color: #cbd7e5;
                font-size: 14px;
                font-weight: 750;
                padding: 10px 15px;
                text-align: left;
            }
            QPushButton#navButton:hover {
                background: #101b27;
                border-color: #203247;
                color: #f8fbff;
            }
            QPushButton#navButton:checked {
                background: #0c3548;
                border: 1px solid #0f7494;
                color: #f1fbff;
            }
            QWidget#sidebarSeparator {
                background: #1b2a39;
                margin-top: 8px;
                margin-bottom: 8px;
            }
            QWidget#sidebarInfoCard {
                background: #0d1723;
                border: 1px solid #203247;
                border-radius: 8px;
            }
            QLabel#sidebarInfoTitle {
                color: #9fb0c2;
                font-size: 11px;
                font-weight: 800;
            }
            QLabel#sidebarInfoBody {
                color: #c7d2df;
                font-size: 11px;
                line-height: 140%;
            }
            QPushButton#sidebarMiniButton {
                background: #101b27;
                border: 1px solid #25384c;
                border-radius: 6px;
                color: #b9c8d8;
                font-size: 11px;
                font-weight: 800;
                padding: 5px 8px;
            }
            QPushButton#sidebarMiniButton:hover {
                border-color: #14b8a6;
                color: #ecfeff;
            }
            QScrollArea#settingsScroll {
                border: none;
                background: transparent;
            }
            QWidget#settingsViewport, QWidget#settingsPanel {
                background: transparent;
            }
            QScrollBar:vertical {
                background: #09111b;
                border: none;
                width: 12px;
                margin: 0;
            }
            QScrollBar::handle:vertical {
                background: #2d3d51;
                border-radius: 6px;
                min-height: 28px;
            }
            QScrollBar::handle:vertical:hover { background: #14b8a6; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                border: none;
                background: transparent;
                height: 0;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: #09111b;
            }
            QLabel#pageTitle {
                color: #f8fbff;
                font-size: 22px;
                font-weight: 900;
            }
            QLabel#pageSubtitle {
                color: #a7b4c4;
                font-size: 13px;
                font-weight: 500;
            }
            QLabel#sectionTitle {
                color: #edf5ff;
                font-size: 14px;
                font-weight: 900;
            }
            QGroupBox {
                border: 1px solid #203247;
                border-radius: 8px;
                margin-top: 14px;
                font-weight: 800;
                background: #101925;
                color: #e5edf7;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 7px;
                color: #5eead4;
                background: #101925;
            }
            QLineEdit, QComboBox, QListWidget, QTableWidget, QPlainTextEdit {
                border: 1px solid #203247;
                border-radius: 7px;
                padding: 6px;
                background: #0c141f;
                color: #eef3f8;
                selection-background-color: #0f766e;
                selection-color: #f8fbff;
            }
            QLineEdit#pairingCode {
                color: #fff7ed;
                background: #2a1a10;
                border-color: #f97316;
                font-size: 18px;
                font-weight: 900;
            }
            QLineEdit:focus, QComboBox:focus, QListWidget:focus, QTableWidget:focus, QPlainTextEdit:focus {
                border-color: #14b8a6;
            }
            QSplitter::handle {
                background: #1a2736;
                margin: 2px;
            }
            QTabWidget#workspaceTabs::pane {
                border: none;
                background: transparent;
                top: -1px;
            }
            QTabWidget#workspaceTabs::tab-bar {
                left: 0;
                top: -6px;
            }
            QTabWidget#workspaceTabs QTabBar::base {
                border: none;
                background: transparent;
            }
            QTabWidget#workspaceTabs QTabBar::tab {
                background: #0c141f;
                color: #9fb0c2;
                padding: 7px 14px;
                border: 1px solid #203247;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                margin-right: 4px;
                font-weight: 800;
            }
            QTabWidget#workspaceTabs QTabBar::tab:selected {
                background: #101925;
                color: #5eead4;
                border-bottom-color: #101925;
            }
            QTabWidget#workspaceTabs QTabBar::tab:hover {
                color: #f8fbff;
                background: #132233;
            }
            QTableWidget {
                gridline-color: #1f3042;
                alternate-background-color: #101a26;
                border-top-left-radius: 0;
                border-top-right-radius: 0;
            }
            QHeaderView::section {
                background: #172434;
                color: #bcc9d9;
                padding: 9px;
                border: none;
                border-right: 1px solid #203247;
                font-weight: 800;
            }
            QTableWidget::item { padding: 6px; }
            QListWidget { font-family: 'Segoe UI', sans-serif; }
            QLabel#pathLabel {
                color: #a9b8c8;
                padding: 5px 7px;
                border: 1px solid #203247;
                border-radius: 7px;
                background: #0c141f;
            }
            QWidget#metricCard, QWidget#libraryIndexCard, QWidget#tablePanel, QWidget#inspectorPanel {
                background: #101925;
                border: 1px solid #203247;
                border-radius: 8px;
            }
            QWidget#metricCard {
                min-height: 116px;
            }
            QLabel#metricIcon {
                font-size: 17px;
                font-weight: 900;
            }
            QLabel#metricTitle {
                color: #dbe8f6;
                font-size: 13px;
                font-weight: 900;
            }
            QLabel#metricValue {
                color: #f8fbff;
                font-size: 29px;
                font-weight: 900;
            }
            QLabel#metricCaption {
                color: #94a5b7;
                font-size: 12px;
            }
            QLabel#libraryIndexArt {
                background: #0b2233;
                border: 1px solid #1e4661;
                border-radius: 8px;
                color: #7dd3fc;
                font-size: 34px;
                font-weight: 900;
            }
            QLabel#nextActionTitle {
                color: #f8fbff;
                font-size: 21px;
                font-weight: 900;
            }
            QLabel#nextActionBody {
                color: #b5c2d1;
                font-size: 13px;
            }
            QWidget#verticalSeparator {
                background: #26384b;
            }
            QWidget#systemOverview {
                background: transparent;
            }
            QLabel#systemOverviewTitle {
                color: #dfe9f6;
                font-size: 14px;
                font-weight: 900;
            }
            QLabel#overviewLabel, QLabel#overviewValue {
                color: #c5d2e0;
                font-size: 13px;
            }
            QLabel#overviewValue {
                color: #edf5ff;
                font-weight: 800;
            }
            QWidget#tablePanel {
                background: #101925;
            }
            QWidget#tablePanelHeader {
                background: #101925;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
            }
            QLabel#panelIcon {
                color: #c8d5e5;
                font-size: 16px;
                font-weight: 900;
            }
            QLabel#tableEmptyState {
                color: #a0afc1;
                font-size: 13px;
                padding: 26px 20px;
                background: #0e1722;
                border-bottom-left-radius: 8px;
                border-bottom-right-radius: 8px;
            }
            QLabel#dashboardStatusLine {
                color: #fbbf24;
                background: #291d0b;
                border: 1px solid #7c4b10;
                border-radius: 7px;
                padding: 6px 10px;
            }
            QLabel#footerText {
                color: #8e9daf;
                font-size: 12px;
            }
            QPushButton {
                border: 1px solid #26384c;
                border-radius: 7px;
                padding: 8px 14px;
                background: #111c28;
                color: #d8e2ee;
                font-weight: 800;
            }
            QPushButton:hover { background: #172739; border-color: #14b8a6; color: #f8fbff; }
            QPushButton:pressed { background: #0c141f; }
            QPushButton:disabled { color: #657184; background: #0d141d; border-color: #1c2836; }
            QPushButton#primaryAction {
                background: #14b8b6;
                border-color: #19d3d0;
                color: #031314;
                font-size: 14px;
                font-weight: 900;
            }
            QPushButton#primaryAction:hover { background: #22d3ee; border-color: #67e8f9; color: #031314; }
            QPushButton#secondaryAction {
                background: #0b2233;
                border-color: #0e5f82;
                color: #d8f3ff;
                font-weight: 900;
            }
            QPushButton#secondaryAction:hover { background: #0e3147; border-color: #38bdf8; color: #f0fbff; }
            QPushButton#quietAction {
                background: #0b1119;
                border-color: #28394d;
                color: #c2cfdd;
            }
            QPushButton#quietAction:hover {
                background: #111c28;
                border-color: #4b5d70;
                color: #f8fbff;
            }
            QPushButton#cautionAction {
                background: #2d210b;
                border-color: #b86d0d;
                color: #fde68a;
            }
            QPushButton#cautionAction:hover { background: #432f0b; color: #fffbeb; }
            QPushButton#dangerAction {
                background: #5f1717;
                border-color: #ef4444;
                color: #fee2e2;
            }
            QPushButton#dangerAction:hover { background: #991b1b; color: #ffffff; }
            QPushButton#toolbarButton {
                background: #0b1119;
                border-color: #26384c;
                color: #d0dbe8;
            }
            QPushButton#toolbarButton:hover {
                background: #111c28;
                border-color: #14b8a6;
            }
            QCheckBox { padding: 3px; color: #c4d2e3; }
            """
        )

    def _build_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.tray_icon = None
            return

        icon = self.app_icon
        if icon.isNull():
            icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        menu = QMenu(self)

        show_action = QAction("Open Photo Manager", self)
        start_bg_action = QAction("Start Background Sync", self)
        stop_bg_action = QAction("Stop Background Sync", self)
        quit_action = QAction("Quit", self)

        show_action.triggered.connect(self._show_from_tray)
        start_bg_action.triggered.connect(self.on_start_background)
        stop_bg_action.triggered.connect(self.on_stop_background)
        quit_action.triggered.connect(self._quit_from_tray)

        menu.addAction(show_action)
        menu.addSeparator()
        menu.addAction(start_bg_action)
        menu.addAction(stop_bg_action)
        menu.addSeparator()
        menu.addAction(quit_action)

        self.tray_icon = QSystemTrayIcon(icon, self)
        self.tray_icon.setToolTip("Photo Manager Pro")
        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._show_from_tray()

    def _show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit_from_tray(self) -> None:
        self._allow_real_close = True
        self.sync_service.stop()
        if self.lan_server is not None:
            self.lan_server.stop()
            self.lan_server = None
        if self.tray_icon is not None:
            self.tray_icon.hide()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _default_config(self) -> AppConfig:
        return AppConfig(
            root_dir=str(default_photo_root()),
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
            autostart_windows=self._is_windows_startup_enabled(),
            start_minimized=True,
            minimize_to_tray=True,
            google_drive_credentials=str(default_google_credentials_path()),
            google_drive_token=str(default_google_token_path()),
            google_drive_remote_root=DEFAULT_REMOTE_ROOT_NAME,
            google_drive_parent_id="",
            google_drive_compute_hash=False,
            google_drive_overwrite=False,
            lan_transfer_inbox=DEFAULT_LAN_INBOX,
            lan_transfer_port=DEFAULT_LAN_PORT,
            lan_transfer_pairing_code=self._new_pairing_code(),
        )

    def _new_pairing_code(self) -> str:
        return str(100000 + secrets.randbelow(900000))

    def _load_config(self) -> AppConfig:
        cfg = self._default_config()
        config_path = self.config_path
        if not config_path.exists() and self.legacy_config_path.exists():
            config_path = self.legacy_config_path
            self.log(f"Using legacy config path: {config_path}")
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

    def _apply_config(self, cfg: AppConfig) -> None:
        self.root_edit.setText(str(cfg.root_dir))
        self.source_edit.setText(str(cfg.source_dir))
        self.blur_csv_edit.setText(str(cfg.blur_csv))
        self.date_source_combo.setCurrentText(str(cfg.date_source))
        self.recursive_check.setChecked(bool(cfg.recursive))
        self.include_nonmedia_check.setChecked(bool(cfg.include_nonmedia))
        self.full_hash_check.setChecked(bool(cfg.full_hash))
        self.dry_run_check.setChecked(bool(cfg.dry_run))
        self.delete_after_sync_check.setChecked(bool(cfg.delete_after_sync))
        self.watch_delete_check.setChecked(bool(cfg.watch_delete))
        self.autostart_background_check.setChecked(bool(cfg.autostart_background))
        self.sync_hours_edit.setText(str(cfg.sync_allowed_hours or "0-24"))
        self.sync_weekly_hours = str(getattr(cfg, "sync_weekly_hours", "") or "")
        self._refresh_schedule_summary()
        self.settle_edit.setText(str(cfg.settle_seconds))
        self.stable_checks_edit.setText(str(cfg.stable_checks))
        self.poll_interval_edit.setText(str(cfg.poll_interval))
        self.blur_threshold_edit.setText(str(cfg.blur_threshold))
        self.blur_top_edit.setText(str(cfg.blur_top))
        self.auto_delete_max_edit.setText(str(cfg.auto_delete_max))
        self.auto_delete_hard_check.setChecked(bool(cfg.auto_delete_hard))
        self.autostart_windows_check.setChecked(bool(cfg.autostart_windows))
        self.start_minimized_check.setChecked(bool(getattr(cfg, "start_minimized", True)))
        self.minimize_to_tray_check.setChecked(bool(getattr(cfg, "minimize_to_tray", True)))
        self.google_credentials_edit.setText(str(getattr(cfg, "google_drive_credentials", default_google_credentials_path())))
        self.google_token_edit.setText(str(getattr(cfg, "google_drive_token", default_google_token_path())))
        self.google_remote_root_edit.setText(str(getattr(cfg, "google_drive_remote_root", DEFAULT_REMOTE_ROOT_NAME)))
        self.google_parent_id_edit.setText(str(getattr(cfg, "google_drive_parent_id", "") or ""))
        self.google_compute_hash_check.setChecked(bool(getattr(cfg, "google_drive_compute_hash", False)))
        self.google_overwrite_check.setChecked(bool(getattr(cfg, "google_drive_overwrite", False)))
        self.lan_inbox_edit.setText(str(getattr(cfg, "lan_transfer_inbox", DEFAULT_LAN_INBOX) or DEFAULT_LAN_INBOX))
        self.lan_port_edit.setText(str(getattr(cfg, "lan_transfer_port", DEFAULT_LAN_PORT) or DEFAULT_LAN_PORT))
        pairing_code = str(getattr(cfg, "lan_transfer_pairing_code", "") or self._new_pairing_code())
        self.lan_pairing_code_edit.setText(pairing_code)
        self._load_google_credentials_fields()
        self._refresh_google_status_label()
        if sys.platform != "win32":
            self.autostart_windows_check.setEnabled(False)
            self.autostart_windows_check.setToolTip("Windows-only option.")
        if hasattr(self, "root_summary_label"):
            root_label = self._compact_path_label(str(cfg.root_dir))
            source_label = self._compact_path_label(str(cfg.source_dir))
            self.root_summary_label.setText(f"Library: {root_label}\nSource: {source_label}")
            self.root_summary_label.setToolTip(f"Library: {cfg.root_dir}\nSource: {cfg.source_dir}")
        self._refresh_google_drive_status()
        self._refresh_lan_status()
        self._refresh_lan_urls()

    def _compact_path_label(self, raw: str) -> str:
        text = str(raw).strip()
        if not text:
            return "not set"
        try:
            name = Path(text).expanduser().name
        except Exception:
            name = ""
        return name or text

    def _refresh_google_status_label(self, message: str = "") -> None:
        credentials = self._google_credentials_path()
        token = self._google_token_path()
        if message:
            self.google_status_label.setText(message)
        elif token.exists():
            self.google_status_label.setText(f"OAuth token ready: {token}")
        elif credentials.exists():
            self.google_status_label.setText("OAuth client JSON selected. Authenticate before cloud transfer.")
        else:
            self.google_status_label.setText("Paste Client ID/secret or choose a Google OAuth desktop client JSON.")

    def _load_google_credentials_fields(self) -> None:
        path = self._google_credentials_path()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            installed = data.get("installed", {})
            client_id = str(installed.get("client_id", "")).strip()
            client_secret = str(installed.get("client_secret", "")).strip()
        except Exception as exc:
            self.log(f"google-drive: could not read OAuth JSON fields: {exc}")
            return
        if client_id and not self.google_client_id_edit.text().strip():
            self.google_client_id_edit.setText(client_id)
        if client_secret and not self.google_client_secret_edit.text().strip():
            self.google_client_secret_edit.setText(client_secret)

    def _google_credentials_path(self) -> Path:
        raw = self.google_credentials_edit.text().strip() or str(default_google_credentials_path())
        return Path(raw).expanduser()

    def _google_token_path(self) -> Path:
        raw = self.google_token_edit.text().strip() or str(default_google_token_path())
        return Path(raw).expanduser()

    def _google_remote_root(self) -> str:
        return self.google_remote_root_edit.text().strip() or DEFAULT_REMOTE_ROOT_NAME

    def _write_google_credentials_from_fields(self, *, show_message: bool) -> bool:
        client_id = self.google_client_id_edit.text().strip()
        client_secret = self.google_client_secret_edit.text().strip()
        if not client_id or not client_secret:
            QMessageBox.critical(
                self,
                "Missing Google OAuth details",
                "Paste both Client ID and Client secret, or choose an existing OAuth desktop client JSON.",
            )
            return False
        try:
            out = write_desktop_credentials_file(self._google_credentials_path(), client_id, client_secret)
        except Exception as exc:
            QMessageBox.critical(self, "Could not save OAuth JSON", str(exc))
            return False
        self.google_credentials_edit.setText(str(out))
        self._refresh_google_status_label(f"OAuth client JSON saved: {out}")
        self.log(f"google-drive: OAuth client JSON saved to {out}")
        if show_message:
            QMessageBox.information(self, "OAuth JSON saved", f"Saved:\n{out}")
        return True

    def _google_plan_path(self, cfg: RuntimeConfig, direction: str) -> Path:
        return cfg.root / f"google-drive-{direction}-plan.csv"

    def _google_base_command(
        self,
        command: str,
        cfg: Optional[RuntimeConfig] = None,
        *,
        include_auth: bool = True,
    ) -> List[str]:
        cmd = [
            str(self._console_python_executable()),
            str(self.script_dir / "photo_manager_google_drive.py"),
            command,
        ]
        if cfg is not None:
            cmd += ["--root", str(cfg.root)]
        cmd += ["--remote-root", self._google_remote_root()]
        if include_auth:
            cmd += [
                "--credentials",
                str(self._google_credentials_path()),
                "--token",
                str(self._google_token_path()),
            ]
            parent_id = self.google_parent_id_edit.text().strip()
            if parent_id:
                cmd += ["--parent-id", parent_id]
        if self.include_nonmedia_check.isChecked():
            cmd.append("--include-nonmedia")
        if self.google_compute_hash_check.isChecked():
            cmd.append("--compute-hash")
        return cmd

    def _refresh_google_drive_status(self) -> None:
        drive_folder = detect_google_drive_folder()
        if drive_folder is not None:
            library_folder = default_google_drive_library_folder()
            self.google_drive_status_label.setText(f"Detected: {drive_folder.name}")
            self.google_drive_status_label.setToolTip(
                f"Google Drive folder: {drive_folder}\nLibrary folder: {library_folder}"
            )
            self.open_google_drive_btn.setEnabled(True)
            return

        checked = "\n".join(str(path) for path in google_drive_folder_candidates())
        self.google_drive_status_label.setText("No mounted folder detected")
        self.google_drive_status_label.setToolTip(f"Checked:\n{checked}")
        self.open_google_drive_btn.setEnabled(False)

    def _lan_port(self) -> int:
        port = self._parse_int(self.lan_port_edit.text(), "Phone Connect port", 1)
        if port > 65535:
            raise ValueError("Phone Connect port must be between 1 and 65535")
        return port

    def _resolve_lan_inbox(self, cfg: RuntimeConfig) -> Path:
        raw = self.lan_inbox_edit.text().strip()
        if not raw:
            return default_lan_inbox(cfg.root).resolve()
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = (cfg.root / path).resolve()
        else:
            path = path.resolve()
        return path

    def _refresh_lan_status(self) -> None:
        if self.lan_server is not None and self.lan_server.running:
            self.lan_status_label.setText(
                f"Receiver active on port {self.lan_server.actual_port}. Inbox: {self.lan_server.incoming_dir}"
            )
            self.lan_status_label.setStyleSheet(
                "color: #bbf7d0; background: #062f20; border: 1px solid #22c55e; "
                "border-radius: 7px; padding: 5px 7px; font-weight: 800;"
            )
        else:
            self.lan_status_label.setText("Receiver stopped")
            self.lan_status_label.setStyleSheet(
                "color: #fef3c7; background: #31230a; border: 1px solid #f59e0b; "
                "border-radius: 7px; padding: 5px 7px; font-weight: 800;"
            )

    def _refresh_lan_urls(self) -> None:
        try:
            port = self.lan_server.actual_port if self.lan_server is not None and self.lan_server.running else self._lan_port()
            urls = lan_endpoint_urls(port)
        except Exception as exc:
            self.lan_urls_view.setPlainText(f"Port error: {exc}")
            return
        code = self.lan_pairing_code_edit.text().strip()
        lines = []
        for url in urls:
            lines.append(url)
        if code:
            lines.append("")
            lines.append(f"Pairing code: {code}")
        self.lan_urls_view.setPlainText("\n".join(lines))

    def _build_config_from_widgets(self) -> AppConfig:
        return AppConfig(
            root_dir=self.root_edit.text().strip(),
            source_dir=self.source_edit.text().strip(),
            date_source=self.date_source_combo.currentText().strip(),
            recursive=self.recursive_check.isChecked(),
            include_nonmedia=self.include_nonmedia_check.isChecked(),
            full_hash=self.full_hash_check.isChecked(),
            dry_run=self.dry_run_check.isChecked(),
            delete_after_sync=self.delete_after_sync_check.isChecked(),
            watch_delete=self.watch_delete_check.isChecked(),
            sync_allowed_hours=self.sync_hours_edit.text().strip() or "0-24",
            sync_weekly_hours=self.sync_weekly_hours,
            settle_seconds=self._parse_float(self.settle_edit.text(), "Settle seconds", 0.0),
            stable_checks=self._parse_int(self.stable_checks_edit.text(), "Stable checks", 1),
            poll_interval=self._parse_float(self.poll_interval_edit.text(), "Poll interval", 0.05),
            blur_csv=self.blur_csv_edit.text().strip(),
            blur_threshold=self._parse_float(self.blur_threshold_edit.text(), "Blur threshold", 0.0),
            blur_top=self._parse_int(self.blur_top_edit.text(), "Blur top N", 0),
            auto_delete_max=self._parse_int(self.auto_delete_max_edit.text(), "Auto delete max", 0),
            auto_delete_hard=self.auto_delete_hard_check.isChecked(),
            autostart_background=self.autostart_background_check.isChecked(),
            autostart_windows=self.autostart_windows_check.isChecked(),
            start_minimized=self.start_minimized_check.isChecked(),
            minimize_to_tray=self.minimize_to_tray_check.isChecked(),
            google_drive_credentials=self.google_credentials_edit.text().strip(),
            google_drive_token=self.google_token_edit.text().strip(),
            google_drive_remote_root=self.google_remote_root_edit.text().strip() or DEFAULT_REMOTE_ROOT_NAME,
            google_drive_parent_id=self.google_parent_id_edit.text().strip(),
            google_drive_compute_hash=self.google_compute_hash_check.isChecked(),
            google_drive_overwrite=self.google_overwrite_check.isChecked(),
            lan_transfer_inbox=self.lan_inbox_edit.text().strip() or DEFAULT_LAN_INBOX,
            lan_transfer_port=self._parse_int(self.lan_port_edit.text(), "Phone Connect port", 1),
            lan_transfer_pairing_code=self.lan_pairing_code_edit.text().strip(),
        )

    def _resolve_runtime_config(self) -> RuntimeConfig:
        cfg = self._build_config_from_widgets()
        if cfg.date_source not in DATE_SOURCES:
            raise ValueError("Date source must be one of: exif, mtime, ctime")
        self._parse_hour_windows(cfg.sync_allowed_hours)
        parse_weekly_hours(cfg.sync_weekly_hours)

        root_path = Path(cfg.root_dir).expanduser()
        if not root_path.is_absolute():
            root_path = (self.script_dir / root_path).resolve()
        else:
            root_path = root_path.resolve()
        if not root_path.exists():
            raise ValueError(f"Root folder does not exist: {root_path}")

        source_path = Path(cfg.source_dir).expanduser()
        if not source_path.is_absolute():
            source_path = (root_path / source_path).resolve()
        else:
            source_path = source_path.resolve()

        blur_csv = Path(cfg.blur_csv).expanduser()
        if not blur_csv.is_absolute():
            blur_csv = (root_path / blur_csv).resolve()
        else:
            blur_csv = blur_csv.resolve()

        return RuntimeConfig(
            root=root_path,
            source=source_path,
            date_source=cfg.date_source,
            recursive=cfg.recursive,
            include_nonmedia=cfg.include_nonmedia,
            full_hash=cfg.full_hash,
            dry_run=cfg.dry_run,
            delete_after_sync=cfg.delete_after_sync,
            watch_delete=cfg.watch_delete,
            sync_allowed_hours=cfg.sync_allowed_hours,
            sync_weekly_hours=cfg.sync_weekly_hours,
            settle_seconds=cfg.settle_seconds,
            stable_checks=cfg.stable_checks,
            poll_interval=cfg.poll_interval,
            blur_csv=blur_csv,
            blur_threshold=cfg.blur_threshold,
            blur_top=cfg.blur_top,
            auto_delete_max=cfg.auto_delete_max,
            auto_delete_hard=cfg.auto_delete_hard,
        )

    def _parse_float(self, raw: str, name: str, minimum: float) -> float:
        try:
            value = float(str(raw).strip())
        except Exception as exc:
            raise ValueError(f"{name}: invalid float value: {raw}") from exc
        if value < minimum:
            raise ValueError(f"{name} must be >= {minimum}")
        return value

    def _parse_int(self, raw: str, name: str, minimum: int) -> int:
        try:
            value = int(str(raw).strip())
        except Exception as exc:
            raise ValueError(f"{name}: invalid integer value: {raw}") from exc
        if value < minimum:
            raise ValueError(f"{name} must be >= {minimum}")
        return value

    def _parse_hour_windows(self, raw: str) -> List[tuple[int, int]]:
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

    def is_sync_time_allowed(self, cfg: RuntimeConfig) -> bool:
        now = datetime.now()
        weekly = parse_weekly_hours(cfg.sync_weekly_hours)
        day = DAY_KEYS[now.weekday()]
        windows = weekly[day] if day in weekly else self._parse_hour_windows(cfg.sync_allowed_hours)
        for start, end in windows:
            hour = now.hour
            if start < end and start <= hour < end:
                return True
            if start > end and (hour >= start or hour < end):
                return True
        return False

    def _get_runtime_or_message(self) -> Optional[RuntimeConfig]:
        try:
            return self._resolve_runtime_config()
        except Exception as exc:
            QMessageBox.critical(self, "Invalid settings", str(exc))
            return None

    def _refresh_schedule_summary(self) -> None:
        try:
            summary = weekly_schedule_summary(
                self.sync_weekly_hours,
                self.sync_hours_edit.text().strip() or "0-24",
            )
        except Exception as exc:
            summary = f"Schedule error: {exc}"
        self.schedule_summary_label.setText(summary)

    def on_edit_schedule(self) -> None:
        dialog = WeeklyScheduleDialog(
            self.sync_weekly_hours,
            self.sync_hours_edit.text().strip() or "0-24",
            self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.sync_weekly_hours = dialog.schedule_text()
            self._refresh_schedule_summary()
            self.log("Weekly schedule updated.")

    def _drain_log_queue(self) -> None:
        lines: List[str] = []
        while True:
            try:
                lines.append(self.log_queue.get_nowait())
            except queue.Empty:
                break
        if lines:
            self.log_view.appendPlainText("\n".join(lines))
            self.log_view.ensureCursorVisible()
        while True:
            try:
                callback = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                callback()
            except Exception as exc:
                self.log(f"ui update failed: {exc}")

    def _post_ui(self, callback) -> None:
        self.ui_queue.put(callback)

    def log(self, msg: str) -> None:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        self.log_queue.put(f"[{ts}] {msg}")

    def append_sync_records(self, root: Path, records: List[dict]) -> Path:
        if not records:
            return root / SYNC_LOG_NAME
        log_path = root / SYNC_LOG_NAME
        log_path.parent.mkdir(parents=True, exist_ok=True)

        with self.log_lock:
            write_header = not log_path.exists()
            with log_path.open("a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=self.sync_log_columns)
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

    def _start_worker(self, name: str, target, *args) -> bool:
        if self.worker_thread is not None and self.worker_thread.is_alive():
            QMessageBox.information(self, "Task in progress", f"Task already running: {self.worker_name}")
            return False

        self.worker_name = name

        def run():
            self.log(f"{name}: started.")
            try:
                target(*args)
                self.log(f"{name}: finished.")
            except Exception as exc:
                self.log(f"{name}: failed: {exc}")
            finally:
                self.worker_name = ""

        self.worker_thread = threading.Thread(target=run, daemon=True)
        self.worker_thread.start()
        return True

    def _startup_command(self, minimized: bool = True) -> str:
        exe = Path(sys.executable)
        if getattr(sys, "frozen", False):
            cmd = f'"{exe}"'
        elif exe.name.lower() == "python.exe":
            pythonw = exe.with_name("pythonw.exe")
            if pythonw.exists():
                exe = pythonw
            cmd = f'"{exe}" "{self.script_dir / "photo_manager_gui.py"}"'
        else:
            cmd = f'"{exe}" "{self.script_dir / "photo_manager_gui.py"}"'
        if minimized:
            cmd += " --minimized"
        return cmd

    def _is_windows_startup_enabled(self) -> bool:
        if sys.platform != "win32":
            return False
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
                value, _value_type = winreg.QueryValueEx(key, WINDOWS_RUN_VALUE_NAME)
            normalized = str(value).strip()
            return normalized in {self._startup_command(True), self._startup_command(False)}
        except FileNotFoundError:
            return False
        except Exception:
            return False

    def _set_windows_startup(self, enabled: bool, start_minimized: bool) -> None:
        if sys.platform != "win32":
            return
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                if enabled:
                    winreg.SetValueEx(
                        key,
                        WINDOWS_RUN_VALUE_NAME,
                        0,
                        winreg.REG_SZ,
                        self._startup_command(start_minimized),
                    )
                else:
                    try:
                        winreg.DeleteValue(key, WINDOWS_RUN_VALUE_NAME)
                    except FileNotFoundError:
                        pass
        except Exception as exc:
            raise RuntimeError(f"Could not update Windows startup setting: {exc}") from exc

    def _persist_settings(self, show_message: bool) -> bool:
        try:
            cfg = self._build_config_from_widgets()
            self._set_windows_startup(cfg.autostart_windows, cfg.start_minimized)
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text(json.dumps(cfg.__dict__, ensure_ascii=False, indent=2), encoding="utf-8")
            self.log(f"Settings saved to: {self.config_path}")
            self.log(
                "Windows startup: "
                + ("enabled" if self._is_windows_startup_enabled() else "disabled")
            )
            if show_message:
                QMessageBox.information(self, "Saved", f"Settings saved to:\n{self.config_path}")
            return True
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return False

    def on_save_settings(self) -> None:
        self._persist_settings(show_message=True)

    def on_run_sync_now(self) -> None:
        cfg = self._get_runtime_or_message()
        if cfg is None:
            return
        if not cfg.source.exists():
            QMessageBox.critical(self, "Invalid source", f"Source folder does not exist:\n{cfg.source}")
            return
        self._start_worker("sync-now", self._run_sync_now_worker, cfg)

    def on_compare_preview(self) -> None:
        cfg = self._get_runtime_or_message()
        if cfg is None:
            return
        self.source_preview_list.clear()
        self.target_preview_list.clear()
        self.source_preview_path.setText(str(cfg.source))
        self.target_preview_path.setText(str(cfg.root))
        if not cfg.source.exists():
            self.source_preview_list.addItem("Source folder does not exist.")
            return
        reserved = {}
        count = 0
        for path in iter_source_files(cfg.source, recursive=cfg.recursive):
            if not is_media_file(path, include_nonmedia=cfg.include_nonmedia):
                continue
            count += 1
            if count > 300:
                continue
            self.source_preview_list.addItem(str(path.relative_to(cfg.source)))
            try:
                action = plan_action(cfg.root, path, date_source=cfg.date_source, reserved=reserved)
                for dst in action.dsts:
                    state = "Already present" if dst.exists() else "Copy + SHA256"
                    self.target_preview_list.addItem(f"{state}: {dst.relative_to(cfg.root)}")
            except Exception as exc:
                self.target_preview_list.addItem(f"Error: {path.name}: {exc}")
        if count == 0:
            self.source_preview_list.addItem("No matching files found.")
        if count > 300:
            self.source_preview_list.addItem(f"... and {count - 300} more files")
            self.target_preview_list.addItem("Preview limited to 300 sources; import plans every file.")
        self.log(f"compare-preview: {count} sources; no library changes.")

    def _quick_file_count(self, folder: Path, limit: int) -> int:
        count = 0
        for _ in folder.rglob("*"):
            if _.is_file():
                count += 1
                if count >= limit:
                    return count
        return count

    def _set_table_item(self, table: QTableWidget, row: int, column: int, text: str, data=None) -> None:
        item = QTableWidgetItem(text)
        if data is not None:
            item.setData(Qt.ItemDataRole.UserRole, data)
        table.setItem(row, column, item)

    def _open_path(self, path: Path) -> None:
        if path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        else:
            QMessageBox.information(self, "Missing file", f"File does not exist:\n{path}")

    def _app_version(self) -> str:
        try:
            from importlib import metadata

            return metadata.version("photosync-tool")
        except Exception:
            return "0.2.1"

    def _set_preview_image(self, label: QLabel, path: Path) -> None:
        pixmap = QPixmap()
        try:
            from PIL import Image, ImageOps

            with warnings.catch_warnings():
                if hasattr(Image, "DecompressionBombWarning"):
                    warnings.simplefilter("ignore", Image.DecompressionBombWarning)
                img = Image.open(path)
            with img:
                img = ImageOps.exif_transpose(img)
                if img.mode in {"RGBA", "LA"}:
                    bg = Image.new("RGBA", img.size, (22, 25, 30, 255))
                    bg.alpha_composite(img.convert("RGBA"))
                    img = bg.convert("RGB")
                else:
                    img = img.convert("RGB")
                data = io.BytesIO()
                img.save(data, format="PNG")
                pixmap.loadFromData(data.getvalue())
        except Exception:
            pixmap = QPixmap(str(path))
        if pixmap.isNull():
            label.setText(path.name)
            label.setPixmap(QPixmap())
            return
        target = label.size()
        if target.width() < 40 or target.height() < 40:
            target = QSize(320, 320)
        label.setText("")
        label.setPixmap(
            pixmap.scaled(
                target,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _format_count(self, value) -> str:
        return f"{int(value or 0):,}"

    def _set_dashboard_overview_counts(self, stats: Optional[dict] = None) -> None:
        if not hasattr(self, "dashboard_overview_values"):
            return
        values = {
            "files": 0,
            "present": 0,
            "ai": 0,
            "blur": 0,
            "queue": 0,
            "errors": 0,
        }
        if stats is not None:
            values.update(
                {
                    "files": int(stats.get("total", 0) or 0),
                    "present": int(stats.get("present", 0) or 0),
                    "ai": int(stats.get("ai_count", 0) or 0),
                    "blur": int(stats.get("blur_pending", 0) or 0),
                    "queue": int(stats.get("delete_queued", 0) or 0),
                    "errors": int(stats.get("sync_errors", 0) or 0),
                }
            )
        for key, label in self.dashboard_overview_values.items():
            label.setText(self._format_count(values.get(key, 0)))

    def _set_dashboard_empty_states(self) -> None:
        empty_pairs = (
            (self.dashboard_year_table, self.dashboard_year_empty),
            (self.dashboard_folder_table, self.dashboard_folder_empty),
            (self.dashboard_events_table, self.dashboard_events_empty),
        )
        for table, empty_label in empty_pairs:
            is_empty = table.rowCount() == 0
            empty_label.setVisible(is_empty)
            header_height = table.horizontalHeader().height() or 36
            if is_empty:
                table.setMaximumHeight(header_height + 10)
            else:
                table.setMaximumHeight(16777215)

    def _clear_dashboard_tables(self) -> None:
        for table in (self.dashboard_year_table, self.dashboard_folder_table, self.dashboard_events_table):
            table.setRowCount(0)
        self._set_dashboard_empty_states()

    def on_dashboard_refresh(self) -> None:
        cfg = self._get_runtime_or_message()
        if cfg is None:
            self.next_action_target = "settings"
            self.next_action_title_label.setText("Finish Setup")
            self.next_action_body_label.setText("Open Settings and choose valid root and source folders before syncing.")
            self.next_action_btn.setText("Open Settings")
            self._set_dashboard_overview_counts()
            self._clear_dashboard_tables()
            return
        try:
            stats = dashboard_stats(cfg.root)
        except Exception as exc:
            self.dashboard_summary_label.setText(f"Dashboard unavailable: {exc}")
            self.dashboard_summary_label.setVisible(True)
            self.next_action_target = "settings"
            self.next_action_title_label.setText("Index Unavailable")
            self.next_action_body_label.setText("Open Settings, confirm the library folder, then rebuild the index.")
            self.next_action_btn.setText("Open Settings")
            self._set_dashboard_overview_counts()
            self._clear_dashboard_tables()
            return

        self.dashboard_summary_label.setVisible(False)
        self._set_dashboard_overview_counts(stats)
        self.metric_total_files.setText(self._format_count(stats["total"]))
        self.metric_present_files.setText(self._format_count(stats["present"]))
        self.metric_ai_count.setText(self._format_count(stats["ai_count"]))
        self.metric_blur_count.setText(self._format_count(stats["blur_pending"]))
        self.metric_queue_count.setText(self._format_count(stats["delete_queued"]))
        self.metric_error_count.setText(self._format_count(stats["sync_errors"]))

        self.dashboard_summary_label.setText(
            "Files: {total} | Present: {present} | AI metadata: {ai} | "
            "Blur candidates: {blur} | Delete queue: {queue} | Sync errors: {errors}".format(
                total=stats["total"],
                present=stats["present"],
                ai=stats["ai_count"],
                blur=stats["blur_pending"],
                queue=stats["delete_queued"],
                errors=stats["sync_errors"],
            )
        )

        if int(stats["sync_errors"]) > 0:
            self.next_action_target = "diagnostics"
            self.next_action_title_label.setText("Check Sync Errors")
            self.next_action_body_label.setText("Recent sync events include errors. Open Diagnostics and export a report if needed.")
            self.next_action_btn.setText("Open Diagnostics")
        elif int(stats["delete_queued"]) > 0:
            self.next_action_target = "cleanup"
            self.next_action_title_label.setText("Review Cleanup Queue")
            self.next_action_body_label.setText("Files are staged for deletion. Review them before moving anything to the recycle bin.")
            self.next_action_btn.setText("Open Cleanup")
        elif int(stats["blur_pending"]) > 0:
            self.next_action_target = "cleanup"
            self.next_action_title_label.setText("Review Blur Candidates")
            self.next_action_body_label.setText("Blur candidates are indexed. Queue or review them before cleanup.")
            self.next_action_btn.setText("Review Cleanup")
        elif int(stats["total"]) == 0:
            self.next_action_target = "settings"
            self.next_action_title_label.setText("Build your library index")
            self.next_action_body_label.setText("No indexed media yet. Confirm folders in Settings, then rebuild the library index.")
            self.next_action_btn.setText("Open Settings")
        else:
            self.next_action_target = "gallery"
            self.next_action_title_label.setText("Explore Your Library")
            self.next_action_body_label.setText("The index is ready. Browse media, inspect metadata, or search captions and tags.")
            self.next_action_btn.setText("Open Gallery")

        self.dashboard_year_table.setRowCount(len(stats["years"]))
        for row, item in enumerate(stats["years"]):
            self._set_table_item(self.dashboard_year_table, row, 0, str(item["year"]))
            self._set_table_item(self.dashboard_year_table, row, 1, str(item["count"]))

        self.dashboard_folder_table.setRowCount(len(stats["folders"]))
        for row, item in enumerate(stats["folders"]):
            self._set_table_item(self.dashboard_folder_table, row, 0, str(item["folder"]))
            self._set_table_item(self.dashboard_folder_table, row, 1, str(item["count"]))
            self._set_table_item(self.dashboard_folder_table, row, 2, human_bytes(int(item["bytes"])))

        events = stats["recent_events"]
        self.dashboard_events_table.setRowCount(len(events))
        for row, item in enumerate(events):
            self._set_table_item(self.dashboard_events_table, row, 0, str(item.get("ts", "")))
            self._set_table_item(self.dashboard_events_table, row, 1, str(item.get("mode", "")))
            self._set_table_item(self.dashboard_events_table, row, 2, str(item.get("status", "")))
            self._set_table_item(self.dashboard_events_table, row, 3, Path(str(item.get("src", ""))).name)
            self._set_table_item(self.dashboard_events_table, row, 4, Path(str(item.get("dst", ""))).name)
        self._set_dashboard_empty_states()
        self.log("dashboard: refreshed.")

    def on_next_best_action(self) -> None:
        targets = {
            "settings": self.settings_tab,
            "cleanup": self.workspace_tabs.widget(3),
            "diagnostics": self.diagnostics_tab,
            "gallery": self.workspace_tabs.widget(1),
            "duplicates": self.workspace_tabs.widget(2),
        }
        target = targets.get(self.next_action_target, self.settings_tab)
        self.workspace_tabs.setCurrentWidget(target)

    def on_open_sync_plan(self) -> None:
        self.workspace_tabs.setCurrentWidget(self.compare_tab)
        self.on_compare_preview()

    def on_dashboard_export_sync_report(self) -> None:
        cfg = self._get_runtime_or_message()
        if cfg is None:
            return
        default_path = cfg.root / "photo_manager_sync_report.csv"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export sync report",
            str(default_path),
            "CSV files (*.csv);;All files (*.*)",
        )
        if not path:
            return
        out = export_sync_report(cfg.root, Path(path))
        self.log(f"sync-report: exported {out}")

    def on_about(self) -> None:
        QMessageBox.information(
            self,
            "About Photo Manager Pro",
            (
                f"Photo Manager Pro\n"
                f"Version: {self._app_version()}\n\n"
                "Local photo sync, gallery index, blur review, duplicate review, "
                "safe delete queue, and light AI metadata."
            ),
        )

    def on_check_updates(self) -> None:
        self._start_worker("update-check", self._run_update_check_worker)

    def _run_update_check_worker(self) -> None:
        current = self._app_version()
        try:
            with urllib.request.urlopen("https://pypi.org/pypi/photosync-tool/json", timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
            latest = str(payload.get("info", {}).get("version", "unknown"))
            message = f"Installed version: {current}\nLatest PyPI version: {latest}"
        except Exception as exc:
            message = f"Could not check PyPI updates:\n{exc}"
        self._post_ui(lambda: QMessageBox.information(self, "Update Check", message))

    def on_create_start_menu_shortcut(self) -> None:
        if sys.platform != "win32":
            QMessageBox.information(self, "Windows only", "Start Menu shortcut creation is Windows-only.")
            return
        try:
            import os
            import win32com.client  # type: ignore

            start_menu = (
                Path(os.environ["APPDATA"])
                / "Microsoft"
                / "Windows"
                / "Start Menu"
                / "Programs"
            )
            start_menu.mkdir(parents=True, exist_ok=True)
            shortcut_path = start_menu / "Photo Manager Pro.lnk"
            exe = Path(sys.executable)
            if exe.name.lower() == "python.exe":
                pythonw = exe.with_name("pythonw.exe")
                if pythonw.exists():
                    exe = pythonw

            shell = win32com.client.Dispatch("WScript.Shell")
            shortcut = shell.CreateShortcut(str(shortcut_path))
            shortcut.TargetPath = str(exe)
            shortcut.Arguments = "-m photo_manager_gui"
            shortcut.WorkingDirectory = str(self.script_dir)
            icon_ref = resources.files(APP_ICON_PACKAGE).joinpath("photo_manager_icon.ico")
            with resources.as_file(icon_ref) as icon_path:
                shortcut.IconLocation = f"{icon_path},0"
                shortcut.Save()
            QMessageBox.information(self, "Shortcut created", f"Created:\n{shortcut_path}")
        except Exception as exc:
            QMessageBox.critical(self, "Shortcut failed", str(exc))

    def _refresh_gallery_filter_options(self, root: Path) -> None:
        current_year = self.gallery_year_combo.currentText()
        current_status = self.gallery_status_combo.currentText()
        try:
            years, statuses = gallery_filter_options(root)
        except Exception:
            years, statuses = [], []
        self.gallery_year_combo.blockSignals(True)
        self.gallery_status_combo.blockSignals(True)
        self.gallery_year_combo.clear()
        self.gallery_year_combo.addItem("all")
        self.gallery_year_combo.addItems(years)
        self.gallery_status_combo.clear()
        self.gallery_status_combo.addItem("present")
        self.gallery_status_combo.addItem("all")
        for status in statuses:
            if status not in {"present", "all"}:
                self.gallery_status_combo.addItem(status)
        if current_year:
            idx = self.gallery_year_combo.findText(current_year)
            if idx >= 0:
                self.gallery_year_combo.setCurrentIndex(idx)
        if current_status:
            idx = self.gallery_status_combo.findText(current_status)
            if idx >= 0:
                self.gallery_status_combo.setCurrentIndex(idx)
        self.gallery_year_combo.blockSignals(False)
        self.gallery_status_combo.blockSignals(False)

    def _gallery_filters(self) -> tuple[str, str, Optional[float], str, int]:
        blur_text = self.gallery_blur_max_edit.text().strip()
        blur_max = float(blur_text) if blur_text else None
        limit = self._parse_int(self.gallery_limit_edit.text(), "Gallery limit", 1)
        return (
            self.gallery_year_combo.currentText(),
            self.gallery_status_combo.currentText(),
            blur_max,
            self.gallery_search_edit.text().strip(),
            limit,
        )

    def on_gallery_refresh(self) -> None:
        cfg = self._get_runtime_or_message()
        if cfg is None:
            return
        try:
            filters = self._gallery_filters()
        except Exception as exc:
            QMessageBox.critical(self, "Invalid gallery filters", str(exc))
            return
        self._refresh_gallery_filter_options(cfg.root)
        self._start_worker("gallery-load", self._run_gallery_load_worker, cfg, filters)

    def _run_gallery_load_worker(self, cfg: RuntimeConfig, filters) -> None:
        year, status, blur_max, search, limit = filters
        items = list_gallery_items(
            cfg.root,
            year=year,
            status=status,
            blur_max=blur_max,
            search=search,
            limit=limit,
        )
        payload = [(item, build_thumbnail(cfg.root, item.path)) for item in items]
        self._post_ui(lambda payload=payload: self._populate_gallery(payload))

    def _populate_gallery(self, payload: List[tuple[GalleryItem, Optional[Path]]]) -> None:
        self.gallery_payload = payload
        self.gallery_list.clear()
        for item, thumb in payload:
            icon = QIcon(str(thumb)) if thumb is not None else self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
            qitem = QListWidgetItem(icon, item.name)
            qitem.setData(Qt.ItemDataRole.UserRole, str(item.path))
            qitem.setToolTip(
                f"{item.relative_path}\n"
                f"Status: {item.status}\n"
                f"Blur: {item.blur_score if item.blur_score is not None else '-'}\n"
                f"Tags: {', '.join(item.tags)}\n"
                f"{item.caption}"
            )
            self.gallery_list.addItem(qitem)
        self.gallery_details.setPlainText(f"Loaded {len(payload)} items.")
        self.gallery_preview_label.clear()
        self.log(f"gallery: loaded {len(payload)} items.")

    def _gallery_item_for_path(self, path: Path) -> Optional[GalleryItem]:
        normalized = str(path)
        for item, _thumb in self.gallery_payload:
            if str(item.path) == normalized:
                return item
        return None

    def on_gallery_selected(self, current: Optional[QListWidgetItem], _previous: Optional[QListWidgetItem] = None) -> None:
        if current is None:
            return
        path = Path(str(current.data(Qt.ItemDataRole.UserRole)))
        item = self._gallery_item_for_path(path)
        self._set_preview_image(self.gallery_preview_label, path)
        if item is None:
            self.gallery_details.setPlainText(str(path))
            return
        details = [
            str(item.path),
            f"Relative: {item.relative_path}",
            f"Year: {item.year or '-'}",
            f"Status: {item.status}",
            f"Size: {human_bytes(item.size_bytes)}",
            f"Dimensions: {item.width or '-'} x {item.height or '-'}",
            f"Blur: {item.blur_score if item.blur_score is not None else '-'} ({item.blur_status or '-'})",
            f"Tags: {', '.join(item.tags) if item.tags else '-'}",
            f"Caption: {item.caption or '-'}",
        ]
        self.gallery_details.setPlainText("\n".join(details))

    def _selected_gallery_path(self) -> Optional[Path]:
        item = self.gallery_list.currentItem()
        if item is None:
            return None
        return Path(str(item.data(Qt.ItemDataRole.UserRole)))

    def on_gallery_open_selected(self) -> None:
        path = self._selected_gallery_path()
        if path is not None:
            self._open_path(path)

    def on_gallery_queue_selected(self) -> None:
        cfg = self._get_runtime_or_message()
        path = self._selected_gallery_path()
        if cfg is None or path is None:
            return
        enqueue_delete(cfg.root, path, reason="manual_gallery_review", source="gallery")
        self.log(f"delete-queue: queued from gallery: {path.name}")
        self.on_delete_queue_refresh()
        self.on_dashboard_refresh()

    def on_duplicate_scan(self) -> None:
        cfg = self._get_runtime_or_message()
        if cfg is None:
            return
        self.duplicate_scan_cancel_event.clear()
        if self._start_worker("duplicate-scan", self._run_duplicate_scan_worker, cfg):
            self.duplicate_scan_btn.setEnabled(False)
            self.duplicate_cancel_scan_btn.setEnabled(True)
            self.duplicate_details.setPlainText("Duplicate scan running...")

    def on_duplicate_cancel_scan(self) -> None:
        if self.worker_name != "duplicate-scan":
            return
        self.duplicate_scan_cancel_event.set()
        self.duplicate_cancel_scan_btn.setEnabled(False)
        self.duplicate_details.setPlainText("Cancelling duplicate scan...")
        self.log("duplicates: cancel requested")

    def _run_duplicate_scan_worker(self, cfg: RuntimeConfig) -> None:
        try:
            try:
                actions = scan_duplicates(
                    cfg.root,
                    include_nonmedia=cfg.include_nonmedia,
                    recursive=True,
                    log=self.log,
                    should_cancel=self.duplicate_scan_cancel_event.is_set,
                )
            except DuplicateScanCancelled:
                self._post_ui(self._duplicate_scan_cancelled)
                return
            self._post_ui(lambda actions=actions: self._populate_duplicates(actions))
        finally:
            self._post_ui(self._duplicate_scan_finished)

    def _duplicate_scan_finished(self) -> None:
        self.duplicate_scan_btn.setEnabled(True)
        self.duplicate_cancel_scan_btn.setEnabled(False)
        self.duplicate_scan_cancel_event.clear()

    def _duplicate_scan_cancelled(self) -> None:
        self.duplicate_scan_btn.setEnabled(True)
        self.duplicate_cancel_scan_btn.setEnabled(False)
        self.duplicate_scan_cancel_event.clear()
        self.duplicate_details.setPlainText("Duplicate scan cancelled. Existing table results were left unchanged.")

    def _populate_duplicates(self, actions: List[DuplicateCandidate]) -> None:
        self.duplicate_actions = actions
        self.duplicates_table.setRowCount(len(actions))
        for row, action in enumerate(actions):
            self._set_table_item(self.duplicates_table, row, 0, str(action.remove), row)
            self._set_table_item(self.duplicates_table, row, 1, str(action.keep))
            self._set_table_item(self.duplicates_table, row, 2, human_bytes(action.size_bytes))
            self._set_table_item(self.duplicates_table, row, 3, action.reason)
        self.duplicate_details.setPlainText(f"Found {len(actions)} duplicate removal candidates.")
        self.log(f"duplicates: table populated with {len(actions)} candidates.")

    def _selected_duplicate_rows(self) -> List[int]:
        rows = sorted({index.row() for index in self.duplicates_table.selectionModel().selectedRows()})
        if not rows and self.duplicates_table.currentRow() >= 0:
            rows = [self.duplicates_table.currentRow()]
        return [row for row in rows if 0 <= row < len(self.duplicate_actions)]

    def on_duplicate_selected(self) -> None:
        rows = self._selected_duplicate_rows()
        if not rows:
            return
        action = self.duplicate_actions[rows[0]]
        self._set_preview_image(self.duplicate_keep_preview, action.keep)
        self._set_preview_image(self.duplicate_remove_preview, action.remove)
        self.duplicate_details.setPlainText(
            "\n".join(
                [
                    f"Keep: {action.keep}",
                    f"Duplicate: {action.remove}",
                    f"Reason: {action.reason}",
                    f"Group: {action.group_key}",
                    f"Size: {human_bytes(action.size_bytes)}",
                ]
            )
        )

    def on_duplicate_open(self, which: str) -> None:
        rows = self._selected_duplicate_rows()
        if not rows:
            return
        action = self.duplicate_actions[rows[0]]
        self._open_path(action.keep if which == "keep" else action.remove)

    def _queue_duplicate_rows(self, rows: List[int]) -> None:
        cfg = self._get_runtime_or_message()
        if cfg is None:
            return
        queued = 0
        for row in rows:
            action = self.duplicate_actions[row]
            enqueue_delete(
                cfg.root,
                action.remove,
                reason=f"duplicate:{action.reason}",
                source="duplicate-review",
                details={"keep": str(action.keep), "group_key": action.group_key},
            )
            queued += 1
        self.log(f"delete-queue: queued duplicates: {queued}")
        self.on_delete_queue_refresh()
        self.on_dashboard_refresh()

    def on_duplicate_queue_selected(self) -> None:
        self._queue_duplicate_rows(self._selected_duplicate_rows())

    def on_duplicate_queue_all(self) -> None:
        if not self.duplicate_actions:
            return
        answer = QMessageBox.question(
            self,
            "Queue all duplicates",
            f"Queue {len(self.duplicate_actions)} duplicate files for safe delete review?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self._queue_duplicate_rows(list(range(len(self.duplicate_actions))))

    def _selected_delete_ids(self) -> List[int]:
        ids: List[int] = []
        for index in self.delete_table.selectionModel().selectedRows():
            item = self.delete_table.item(index.row(), 0)
            if item is not None:
                ids.append(int(item.data(Qt.ItemDataRole.UserRole)))
        return sorted(set(ids))

    def on_delete_queue_refresh(self) -> None:
        cfg = self._get_runtime_or_message()
        if cfg is None:
            return
        status = self.delete_status_combo.currentText()
        items = list_delete_queue(cfg.root, status=status, limit=1000)
        self.delete_table.setRowCount(len(items))
        for row, item in enumerate(items):
            self._set_table_item(self.delete_table, row, 0, str(item.id), item.id)
            self._set_table_item(self.delete_table, row, 1, str(item.path))
            self._set_table_item(self.delete_table, row, 2, item.reason)
            self._set_table_item(self.delete_table, row, 3, item.source)
            self._set_table_item(self.delete_table, row, 4, item.status)
            self._set_table_item(self.delete_table, row, 5, item.created_at)
        self.log(f"delete-queue: refreshed {len(items)} items.")

    def on_delete_queue_cancel_selected(self) -> None:
        cfg = self._get_runtime_or_message()
        ids = self._selected_delete_ids()
        if cfg is None or not ids:
            return
        update_delete_items(cfg.root, ids, status="cancelled")
        self.log(f"delete-queue: cancelled {len(ids)} items.")
        self.on_delete_queue_refresh()
        self.on_dashboard_refresh()

    def on_delete_queue_trash_selected(self) -> None:
        cfg = self._get_runtime_or_message()
        ids = self._selected_delete_ids()
        if cfg is None or not ids:
            return
        answer = QMessageBox.question(
            self,
            "Trash selected",
            f"Move {len(ids)} queued files to the system recycle bin?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self._start_worker("delete-queue-trash", self._run_delete_queue_trash_worker, cfg, ids)

    def on_delete_queue_trash_all(self) -> None:
        cfg = self._get_runtime_or_message()
        if cfg is None:
            return
        items = list_delete_queue(cfg.root, status="queued", limit=100000)
        ids = [item.id for item in items]
        if not ids:
            return
        answer = QMessageBox.question(
            self,
            "Trash all queued",
            f"Move all {len(ids)} queued files to the system recycle bin?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self._start_worker("delete-queue-trash-all", self._run_delete_queue_trash_worker, cfg, ids)

    def _run_delete_queue_trash_worker(self, cfg: RuntimeConfig, ids: List[int]) -> None:
        trashed = trash_delete_items(cfg.root, ids, log=self.log)
        self.log(f"delete-queue: trashed {trashed} files.")
        self._post_ui(lambda: (self.on_delete_queue_refresh(), self.on_dashboard_refresh()))

    def on_delete_queue_export(self) -> None:
        cfg = self._get_runtime_or_message()
        if cfg is None:
            return
        default_path = cfg.root / "photo_manager_delete_queue.csv"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export delete queue",
            str(default_path),
            "CSV files (*.csv);;All files (*.*)",
        )
        if not path:
            return
        out = export_delete_queue(cfg.root, Path(path))
        self.log(f"delete-queue: exported {out}")

    def on_open_recycle_bin(self) -> None:
        if sys.platform == "win32":
            subprocess.Popen(["explorer.exe", "shell:RecycleBinFolder"])
        else:
            QMessageBox.information(self, "Recycle Bin", "Open your system trash from the file manager.")

    def on_light_ai_run(self) -> None:
        cfg = self._get_runtime_or_message()
        if cfg is None:
            return
        try:
            limit = self._parse_int(self.ai_limit_edit.text(), "AI limit", 1)
        except Exception as exc:
            QMessageBox.critical(self, "Invalid AI settings", str(exc))
            return
        self._start_worker(
            "light-ai",
            self._run_light_ai_worker,
            cfg,
            limit,
            self.ai_only_missing_check.isChecked(),
        )

    def _run_light_ai_worker(self, cfg: RuntimeConfig, limit: int, only_missing: bool) -> None:
        run_light_ai(
            cfg.root,
            limit=limit,
            only_missing=only_missing,
            bad_blur_threshold=cfg.blur_threshold,
            log=self.log,
        )
        self._post_ui(lambda: (self.on_ai_search(), self.on_dashboard_refresh()))

    def on_ai_search(self) -> None:
        cfg = self._get_runtime_or_message()
        if cfg is None:
            return
        rows = search_ai_metadata(cfg.root, search=self.ai_search_edit.text().strip(), limit=500)
        self.ai_results_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            path = str(row.get("path", ""))
            tags = ", ".join(parse_tags(row.get("tags_json", "")))
            self._set_table_item(self.ai_results_table, row_index, 0, str(row.get("relative_path") or path), path)
            self._set_table_item(self.ai_results_table, row_index, 1, str(row.get("year") or ""))
            self._set_table_item(self.ai_results_table, row_index, 2, tags)
            self._set_table_item(self.ai_results_table, row_index, 3, str(row.get("caption") or ""))
            self._set_table_item(self.ai_results_table, row_index, 4, str(row.get("backend") or ""))
        self.log(f"light-ai: search returned {len(rows)} rows.")

    def on_ai_open_selected(self) -> None:
        row = self.ai_results_table.currentRow()
        if row < 0:
            return
        item = self.ai_results_table.item(row, 0)
        if item is None:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self._open_path(Path(str(path)))

    def on_queue_blur_candidates(self) -> None:
        cfg = self._get_runtime_or_message()
        if cfg is None:
            return
        if not cfg.blur_csv.exists():
            QMessageBox.critical(self, "Missing CSV", f"Blur CSV does not exist:\n{cfg.blur_csv}")
            return
        decision_map = self._load_blur_decisions(cfg.blur_csv)
        queued = 0
        limit = cfg.auto_delete_max
        with cfg.blur_csv.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                raw_path = row.get("path", "").strip()
                if not raw_path:
                    continue
                path = Path(raw_path)
                if not path.is_absolute():
                    path = (cfg.root / path).resolve()
                else:
                    path = path.resolve()
                status = decision_map.get(str(path), PENDING_STATUS)
                if status != PENDING_STATUS:
                    continue
                score = self._safe_float(row.get("score", "0"), 0.0)
                if score > cfg.blur_threshold:
                    continue
                enqueue_delete(
                    cfg.root,
                    path,
                    reason=f"blur_score:{score}",
                    source="blur-queue",
                    details={"score": score, "threshold": cfg.blur_threshold},
                )
                queued += 1
                if limit > 0 and queued >= limit:
                    break
        self.log(f"delete-queue: queued blur candidates: {queued}")
        self.on_delete_queue_refresh()
        self.on_dashboard_refresh()

    def _run_sync_now_worker(self, cfg: RuntimeConfig) -> None:
        result = run_batch_sync(cfg, self.log)
        def show_result():
            if result["dry_run"]:
                message = f"Planned destinations: {result['planned']}\nNothing was written or deleted."
            else:
                message = (f"New copies: {result['copied']}\n"
                           f"Verified destinations: {result['verified']}\n"
                           f"Already present: {result['already_present']}\n"
                           f"Errors: {result['errors']}")
                self.on_dashboard_refresh()
                self.on_gallery_refresh()
            if result["errors"]:
                QMessageBox.warning(self, "Import needs attention", message)
            else:
                QMessageBox.information(self, "Import result", message)
        self._post_ui(show_result)

    def on_start_background(self) -> None:
        cfg = self._get_runtime_or_message()
        if cfg is None:
            return
        try:
            self.sync_service.start(cfg)
            self._set_bg_running(True)
            self._persist_settings(show_message=False)
        except Exception as exc:
            QMessageBox.critical(self, "Cannot start background sync", str(exc))
            self._set_bg_running(False)

    def on_stop_background(self) -> None:
        self.sync_service.stop()
        self._set_bg_running(False)

    def on_service_command(self, command: str) -> None:
        if sys.platform != "win32" and not sys.platform.startswith("linux"):
            QMessageBox.information(
                self,
                "Service unavailable",
                "Service controls are available on Windows Services and Linux systemd.",
            )
            return
        if not self._persist_settings(show_message=False):
            return
        cmd = [str(self._console_python_executable()), str(self.script_dir / "photo_manager_service.py"), command]
        self._start_worker(f"service-{command}", self._run_subprocess_worker, cmd, f"service-{command}")

    def on_google_save_credentials(self) -> None:
        if self._write_google_credentials_from_fields(show_message=True):
            self._persist_settings(show_message=False)

    def on_google_auth(self) -> None:
        if not self._validate_google_credentials():
            return
        if not self._persist_settings(show_message=False):
            return
        cmd = [
            str(self._console_python_executable()),
            str(self.script_dir / "photo_manager_google_drive.py"),
            "auth",
            "--credentials",
            str(self._google_credentials_path()),
            "--token",
            str(self._google_token_path()),
        ]
        self._refresh_google_status_label("Google OAuth starting. Complete the browser sign-in when it opens.")
        self._start_worker("google-auth", self._run_google_drive_command_worker, cmd, "google-auth")

    def on_google_plan_upload(self) -> None:
        cfg = self._get_runtime_or_message()
        if cfg is None:
            return
        if not self._persist_settings(show_message=False):
            return
        plan_out = self._google_plan_path(cfg, "upload")
        cmd = self._google_base_command("plan", cfg, include_auth=False)
        cmd += ["--plan-out", str(plan_out)]
        self._refresh_google_status_label(f"Building upload plan: {plan_out}")
        self._start_worker("google-plan-upload", self._run_google_drive_command_worker, cmd, "google-plan-upload")

    def on_google_upload(self) -> None:
        cfg = self._get_runtime_or_message()
        if cfg is None or not self._validate_google_credentials():
            return
        if not self._persist_settings(show_message=False):
            return
        answer = QMessageBox.question(
            self,
            "Confirm Google Drive upload",
            (
                "Upload new or changed local files to Google Drive?\n\n"
                "Run Plan Upload first if you have not reviewed the CSV plan."
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        plan_out = self._google_plan_path(cfg, "upload")
        cmd = self._google_base_command("upload", cfg)
        cmd += ["--plan-out", str(plan_out), "--execute"]
        self._refresh_google_status_label("Google Drive upload queued.")
        self._start_worker("google-upload", self._run_google_drive_command_worker, cmd, "google-upload")

    def on_google_plan_download(self) -> None:
        cfg = self._get_runtime_or_message()
        if cfg is None or not self._validate_google_credentials():
            return
        if not self._persist_settings(show_message=False):
            return
        plan_out = self._google_plan_path(cfg, "download")
        cmd = self._google_base_command("download-plan", cfg)
        cmd += ["--plan-out", str(plan_out)]
        if self.google_overwrite_check.isChecked():
            cmd.append("--overwrite")
        self._refresh_google_status_label(f"Building download plan: {plan_out}")
        self._start_worker("google-plan-download", self._run_google_drive_command_worker, cmd, "google-plan-download")

    def on_google_download(self) -> None:
        cfg = self._get_runtime_or_message()
        if cfg is None or not self._validate_google_credentials():
            return
        if not self._persist_settings(show_message=False):
            return
        overwrite = self.google_overwrite_check.isChecked()
        prompt = "Download missing Google Drive files into the local library?"
        if overwrite:
            prompt += "\n\nOverwrite is enabled. Changed local files may be replaced."
        else:
            prompt += "\n\nChanged local files will be reported as conflicts and skipped."
        answer = QMessageBox.question(
            self,
            "Confirm Google Drive download",
            prompt,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        plan_out = self._google_plan_path(cfg, "download")
        cmd = self._google_base_command("download", cfg)
        cmd += ["--plan-out", str(plan_out), "--execute"]
        if overwrite:
            cmd.append("--overwrite")
        self._refresh_google_status_label("Google Drive download queued.")
        self._start_worker("google-download", self._run_google_drive_command_worker, cmd, "google-download")

    def on_generate_lan_pairing_code(self) -> None:
        self.lan_pairing_code_edit.setText(self._new_pairing_code())
        self._refresh_lan_urls()
        self._persist_settings(show_message=False)

    def on_start_lan_receiver(self) -> None:
        cfg = self._get_runtime_or_message()
        if cfg is None:
            return
        try:
            port = self._lan_port()
            inbox = self._resolve_lan_inbox(cfg)
        except Exception as exc:
            QMessageBox.critical(self, "Phone Connect settings", str(exc))
            return
        code = self.lan_pairing_code_edit.text().strip()
        if not code:
            code = self._new_pairing_code()
            self.lan_pairing_code_edit.setText(code)
        if self.lan_server is not None:
            self.lan_server.stop()
        self.lan_server = MobileTransferServer(
            inbox,
            port=port,
            pairing_code=code,
            log=self.log,
            on_upload=self._on_lan_upload,
        )
        try:
            actual_port = self.lan_server.start()
        except Exception as exc:
            self.lan_server = None
            QMessageBox.critical(self, "Could not start Phone Connect", str(exc))
            self._refresh_lan_status()
            return
        self.lan_port_edit.setText(str(actual_port))
        self._persist_settings(show_message=False)
        self._refresh_lan_status()
        self._refresh_lan_urls()

    def on_stop_lan_receiver(self) -> None:
        if self.lan_server is not None:
            self.lan_server.stop()
            self.lan_server = None
        self._refresh_lan_status()
        self._refresh_lan_urls()

    def _on_lan_upload(self, path: Path, size: int, client: str) -> None:
        self.lan_recent_uploads.append(path)
        self._post_ui(lambda: self._add_lan_upload_row(path, size, client))

    def _add_lan_upload_row(self, path: Path, size: int, client: str) -> None:
        item = QListWidgetItem(f"{datetime.now().strftime('%H:%M:%S')}  {path.name}  {human_bytes(size)}  {client}")
        item.setToolTip(str(path))
        self.lan_recent_list.insertItem(0, item)
        while self.lan_recent_list.count() > 200:
            self.lan_recent_list.takeItem(self.lan_recent_list.count() - 1)
        self.on_dashboard_refresh()

    def on_open_lan_inbox(self) -> None:
        cfg = self._get_runtime_or_message()
        if cfg is None:
            return
        inbox = self._resolve_lan_inbox(cfg)
        inbox.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(inbox)))

    def on_sync_lan_inbox(self) -> None:
        cfg = self._get_runtime_or_message()
        if cfg is None:
            return
        inbox = self._resolve_lan_inbox(cfg)
        if not inbox.exists():
            QMessageBox.information(self, "Phone Inbox", f"Inbox does not exist yet:\n{inbox}")
            return
        answer = QMessageBox.question(
            self,
            "Sort Phone Inbox",
            (
                "Copy phone uploads into year folders and remove verified inbox originals?\n\n"
                f"Inbox:\n{inbox}"
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            return
        phone_cfg = RuntimeConfig(
            root=cfg.root,
            source=inbox,
            date_source=cfg.date_source,
            recursive=True,
            include_nonmedia=cfg.include_nonmedia,
            full_hash=cfg.full_hash,
            dry_run=cfg.dry_run,
            delete_after_sync=True,
            watch_delete=False,
            sync_allowed_hours=cfg.sync_allowed_hours,
            sync_weekly_hours=cfg.sync_weekly_hours,
            settle_seconds=cfg.settle_seconds,
            stable_checks=cfg.stable_checks,
            poll_interval=cfg.poll_interval,
            blur_csv=cfg.blur_csv,
            blur_threshold=cfg.blur_threshold,
            blur_top=cfg.blur_top,
            auto_delete_max=cfg.auto_delete_max,
            auto_delete_hard=cfg.auto_delete_hard,
        )
        self._start_worker("phone-inbox-sync", self._run_phone_inbox_sync_worker, phone_cfg)

    def _run_phone_inbox_sync_worker(self, cfg: RuntimeConfig) -> None:
        run_batch_sync(cfg, self.log)
        self._post_ui(lambda: (self.on_dashboard_refresh(), self.on_gallery_refresh()))

    def _validate_google_credentials(self) -> bool:
        credentials = self._google_credentials_path()
        if credentials.exists():
            return True
        if self.google_client_id_edit.text().strip() or self.google_client_secret_edit.text().strip():
            return self._write_google_credentials_from_fields(show_message=False)
        QMessageBox.critical(
            self,
            "Missing Google OAuth file",
            (
                "Paste Client ID and Client secret, or choose a Google OAuth desktop client JSON first.\n\n"
                "Google Drive access to private user files cannot use only an API key."
            ),
        )
        self.workspace_tabs.setCurrentWidget(self.cloud_tab)
        return False

    def on_rebuild_index(self) -> None:
        cfg = self._get_runtime_or_message()
        if cfg is None:
            return
        if not cfg.root.exists():
            QMessageBox.critical(self, "Invalid root", f"Root folder does not exist:\n{cfg.root}")
            return
        self._start_worker("index-rebuild", self._run_index_rebuild_worker, cfg)

    def on_import_blur_index(self) -> None:
        cfg = self._get_runtime_or_message()
        if cfg is None:
            return
        if not cfg.blur_csv.exists():
            QMessageBox.critical(self, "Missing CSV", f"Blur CSV does not exist:\n{cfg.blur_csv}")
            return
        self._start_worker("index-blur-import", self._run_import_blur_index_worker, cfg)

    def _run_index_rebuild_worker(self, cfg: RuntimeConfig) -> None:
        self.log(f"index: rebuilding {default_index_path(cfg.root)}")
        rebuild_index(
            cfg.root,
            include_nonmedia=cfg.include_nonmedia,
            compute_hash=cfg.full_hash,
            date_source=cfg.date_source,
            log=self.log,
        )

    def _run_import_blur_index_worker(self, cfg: RuntimeConfig) -> None:
        import_blur_csv(
            cfg.root,
            cfg.blur_csv,
            threshold=cfg.blur_threshold,
            log=self.log,
        )

    def _console_python_executable(self) -> Path:
        exe = Path(sys.executable)
        if exe.name.lower() == "pythonw.exe":
            python = exe.with_name("python.exe")
            if python.exists():
                return python
        return exe

    def _set_bg_running(self, running: bool) -> None:
        if hasattr(self, "start_background_btn"):
            self.start_background_btn.setVisible(not running)
        if hasattr(self, "stop_background_btn"):
            self.stop_background_btn.setVisible(running)
        if running:
            self.bg_status_label.setText("Watching active")
            self.bg_status_label.setStyleSheet(
                "color: #99f6e4; background: #062f2a; border: 1px solid #14b8a6; "
                "border-radius: 8px; padding: 6px 14px; font-weight: 900;"
            )
        else:
            self.bg_status_label.setText("Watching paused")
            self.bg_status_label.setStyleSheet(
                "color: #fbbf24; background: #2a1d08; border: 1px solid #7c4b10; "
                "border-radius: 8px; padding: 6px 14px; font-weight: 900;"
            )

    def on_blur_scan(self) -> None:
        cfg = self._get_runtime_or_message()
        if cfg is None:
            return
        cmd = [
            sys.executable,
            str(self.script_dir / BLUR_SCRIPT_NAME),
            "scan",
            "--root",
            str(cfg.root),
            "--out",
            str(cfg.blur_csv),
            "--threshold",
            str(cfg.blur_threshold),
        ]
        if cfg.blur_top > 0:
            cmd += ["--top", str(cfg.blur_top)]
        self._start_worker("blur-scan", self._run_blur_scan_worker, cfg, cmd)

    def on_blur_review(self) -> None:
        cfg = self._get_runtime_or_message()
        if cfg is None:
            return
        if not cfg.blur_csv.exists():
            QMessageBox.critical(self, "Missing CSV", f"Blur CSV does not exist:\n{cfg.blur_csv}")
            return
        cmd = [
            sys.executable,
            str(self.script_dir / BLUR_SCRIPT_NAME),
            "review",
            "--csv",
            str(cfg.blur_csv),
        ]
        try:
            subprocess.Popen(cmd, cwd=str(self.script_dir))
            self.log(f"Opened manual blur review for: {cfg.blur_csv}")
        except Exception as exc:
            QMessageBox.critical(self, "Failed to open review", str(exc))

    def on_blur_auto_delete(self) -> None:
        cfg = self._get_runtime_or_message()
        if cfg is None:
            return
        if not cfg.blur_csv.exists():
            QMessageBox.critical(self, "Missing CSV", f"Blur CSV does not exist:\n{cfg.blur_csv}")
            return

        mode = "HARD DELETE" if cfg.auto_delete_hard else "RECYCLE BIN"
        answer = QMessageBox.question(
            self,
            "Confirm auto-delete",
            (
                f"Auto-delete blurred photos from:\n{cfg.blur_csv}\n\n"
                f"Threshold <= {cfg.blur_threshold}\n"
                f"Max files: {cfg.auto_delete_max} (0 means no limit)\n"
                f"Mode: {mode}\n\n"
                "Continue?"
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self._start_worker("blur-auto-delete", self._run_blur_auto_delete_worker, cfg)

    def _run_blur_scan_worker(self, cfg: RuntimeConfig, cmd: List[str]) -> None:
        self._run_subprocess_worker(cmd, "blur-scan")
        if cfg.blur_csv.exists():
            import_blur_csv(
                cfg.root,
                cfg.blur_csv,
                threshold=cfg.blur_threshold,
                log=self.log,
            )

    def _run_subprocess_worker(self, cmd: List[str], tag: str) -> None:
        self.log(f"{tag}: running command: {' '.join(cmd)}")
        proc = subprocess.Popen(
            cmd,
            cwd=str(self.script_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                self.log(f"{tag}: {line}")
        rc = proc.wait()
        if rc != 0:
            raise RuntimeError(f"{tag}: process exited with code {rc}")
        self.log(f"{tag}: completed.")

    def _run_google_drive_command_worker(self, cmd: List[str], tag: str) -> None:
        self._run_subprocess_worker(cmd, tag)
        self._post_ui(lambda: self._refresh_google_status_label(f"{tag}: completed. See Diagnostics for details."))

    def _run_blur_auto_delete_worker(self, cfg: RuntimeConfig) -> None:
        decision_map = self._load_blur_decisions(cfg.blur_csv)
        candidates: List[tuple[float, Path]] = []

        with cfg.blur_csv.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                raw_path = row.get("path", "").strip()
                if not raw_path:
                    continue
                p = Path(raw_path)
                if not p.is_absolute():
                    p = (cfg.root / p).resolve()
                else:
                    p = p.resolve()

                status = decision_map.get(str(p), PENDING_STATUS)
                if status != PENDING_STATUS:
                    continue

                score = self._safe_float(row.get("score", "0"), 0.0)
                if score > cfg.blur_threshold:
                    continue

                candidates.append((score, p))

        candidates.sort(key=lambda item: item[0])
        if cfg.auto_delete_max > 0:
            candidates = candidates[: cfg.auto_delete_max]

        if not candidates:
            self.log("blur-auto-delete: no pending candidates matched current threshold.")
            return

        if not cfg.auto_delete_hard and send2trash is None:
            raise RuntimeError("send2trash is not installed. Install with: py -m pip install send2trash")

        deleted = 0
        missing = 0
        failed = 0
        for score, path in candidates:
            if not path.exists():
                missing += 1
                self._append_blur_decision(cfg.blur_csv, path, "missing", score)
                try:
                    update_blur_status(cfg.root, path, status="missing", score=score)
                except Exception as exc:
                    self.log(f"blur-auto-delete: index status skipped for {path.name}: {exc}")
                continue

            try:
                if cfg.auto_delete_hard:
                    path.unlink(missing_ok=True)
                    self._append_blur_decision(cfg.blur_csv, path, "deleted", score)
                    try:
                        update_blur_status(cfg.root, path, status="deleted", score=score)
                    except Exception as exc:
                        self.log(f"blur-auto-delete: index status skipped for {path.name}: {exc}")
                else:
                    send2trash(str(path))
                    self._append_blur_decision(cfg.blur_csv, path, "trashed", score)
                    try:
                        update_blur_status(cfg.root, path, status="trashed", score=score)
                    except Exception as exc:
                        self.log(f"blur-auto-delete: index status skipped for {path.name}: {exc}")
                deleted += 1
            except Exception as exc:
                failed += 1
                self._append_blur_decision(cfg.blur_csv, path, "error", score, extra={"error": str(exc)})
                try:
                    update_blur_status(cfg.root, path, status="error", score=score)
                except Exception as index_exc:
                    self.log(f"blur-auto-delete: index status skipped for {path.name}: {index_exc}")
                self.log(f"blur-auto-delete: failed for {path.name}: {exc}")

        self.log(
            f"blur-auto-delete complete: targeted={len(candidates)}, deleted_or_trashed={deleted}, missing={missing}, failed={failed}"
        )

    def _load_blur_decisions(self, csv_path: Path) -> Dict[str, str]:
        decisions_path = self._decisions_path_for(csv_path)
        statuses: Dict[str, str] = {}
        if not decisions_path.exists():
            return statuses
        with decisions_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                raw_path = str(rec.get("path", "")).strip()
                status = str(rec.get("status", "")).strip().lower()
                if not raw_path or not status:
                    continue
                try:
                    key = str(Path(raw_path).resolve())
                except Exception:
                    key = raw_path
                statuses[key] = status
        return statuses

    def _decisions_path_for(self, csv_path: Path) -> Path:
        return csv_path.with_suffix(csv_path.suffix + ".decisions.jsonl")

    def _append_blur_decision(
        self,
        csv_path: Path,
        photo_path: Path,
        status: str,
        score: float,
        extra: Optional[dict] = None,
    ) -> None:
        decisions_path = self._decisions_path_for(csv_path)
        decisions_path.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": time.time(),
            "path": str(photo_path),
            "status": status,
            "score": score,
            "source": "photo_manager_qt",
        }
        if extra:
            rec.update(extra)
        with decisions_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _safe_float(self, raw: str, fallback: float) -> float:
        try:
            return float(raw)
        except Exception:
            return fallback

    def on_browse_root(self) -> None:
        start = self.root_edit.text().strip() or str(self.script_dir)
        path = QFileDialog.getExistingDirectory(self, "Choose root folder", start)
        if path:
            self.root_edit.setText(path)
            self._refresh_google_drive_status()

    def on_use_google_drive_root(self) -> None:
        drive_folder = detect_google_drive_folder()
        if drive_folder is None:
            checked = "\n".join(str(path) for path in google_drive_folder_candidates())
            QMessageBox.information(
                self,
                "Google Drive folder not found",
                "No mounted Google Drive folder was found.\n\n"
                "Mount Google Drive with rclone, Google Drive for desktop, or your file manager first. "
                "You can also click Browse and choose the folder manually.\n\n"
                f"Checked:\n{checked}",
            )
            self.log("google-drive: no mounted folder detected")
            self._refresh_google_drive_status()
            return

        library_folder = drive_folder / GOOGLE_DRIVE_LIBRARY_FOLDER_NAME
        try:
            library_folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.critical(
                self,
                "Google Drive folder error",
                f"Could not create the PhotoManagerPro library folder:\n{library_folder}\n\n{exc}",
            )
            self.log(f"google-drive: could not prepare library folder: {exc}")
            return

        self.root_edit.setText(str(library_folder))
        self._refresh_google_drive_status()
        self.log(f"google-drive: root set to {library_folder}")

    def on_open_google_drive_root(self) -> None:
        drive_folder = detect_google_drive_folder()
        if drive_folder is None:
            self._refresh_google_drive_status()
            return
        library_folder = default_google_drive_library_folder()
        target = library_folder if library_folder is not None and library_folder.exists() else drive_folder
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def on_browse_source(self) -> None:
        start = self.root_edit.text().strip() or str(self.script_dir)
        path = QFileDialog.getExistingDirectory(self, "Choose source folder", start)
        if path:
            self.source_edit.setText(path)

    def on_browse_lan_inbox(self) -> None:
        start = self.lan_inbox_edit.text().strip() or self.root_edit.text().strip() or str(self.script_dir)
        path = QFileDialog.getExistingDirectory(self, "Choose phone inbox", start)
        if path:
            self.lan_inbox_edit.setText(path)
            self._refresh_lan_status()

    def on_browse_blur_csv(self) -> None:
        start = self.root_edit.text().strip() or str(self.script_dir)
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Choose blur CSV",
            str(Path(start) / "blur_candidates.csv"),
            "CSV files (*.csv);;All files (*.*)",
        )
        if path:
            self.blur_csv_edit.setText(path)

    def on_browse_google_credentials(self) -> None:
        current = self.google_credentials_edit.text().strip()
        start = str(Path(current).expanduser().parent) if current else str(default_google_credentials_path().parent)
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose Google OAuth desktop client JSON",
            start,
            "JSON files (*.json);;All files (*.*)",
        )
        if path:
            self.google_credentials_edit.setText(path)
            if not self.google_token_edit.text().strip():
                self.google_token_edit.setText(str(default_google_token_path()))
            self.google_client_id_edit.clear()
            self.google_client_secret_edit.clear()
            self._load_google_credentials_fields()
            self._refresh_google_status_label()

    def closeEvent(self, event: QCloseEvent) -> None:
        if (
            not self._allow_real_close
            and self.tray_icon is not None
            and self.minimize_to_tray_check.isChecked()
        ):
            event.ignore()
            self.hide()
            self.tray_icon.showMessage(
                "Photo Manager Pro",
                "Still running in the system tray.",
                QSystemTrayIcon.MessageIcon.Information,
                2500,
            )
            return
        self.sync_service.stop()
        if self.lan_server is not None:
            self.lan_server.stop()
            self.lan_server = None
        if self.tray_icon is not None:
            self.tray_icon.hide()
        super().closeEvent(event)
        app = QApplication.instance()
        if app is not None:
            QTimer.singleShot(0, app.quit)


def main() -> None:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--minimized", action="store_true", help="Start hidden in the system tray.")
    args, qt_args = parser.parse_known_args()

    sys.argv = [sys.argv[0], *qt_args]
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    single_instance = SingleInstanceGuard(SINGLE_INSTANCE_SERVER_NAME)
    if single_instance.notify_existing_instance():
        sys.exit(0)
    activation_state: Dict[str, object] = {"window": None, "pending": False}

    def activate_primary_instance() -> None:
        window = activation_state.get("window")
        if isinstance(window, PhotoManagerWindow):
            window._show_from_tray()
        else:
            activation_state["pending"] = True

    single_instance.listen(activate_primary_instance)
    app.aboutToQuit.connect(single_instance.close)

    app_icon = load_app_icon()
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)
    win = PhotoManagerWindow(app_icon=app_icon)
    activation_state["window"] = win
    if win.tray_icon is not None:
        app.setQuitOnLastWindowClosed(False)
    if args.minimized:
        if win.tray_icon is None:
            win.showMinimized()
        else:
            win.hide()
            win.log("Started minimized to tray.")
    else:
        win.show()
    if activation_state.get("pending"):
        QTimer.singleShot(0, win._show_from_tray)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
