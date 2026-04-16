#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import csv
import json
import queue
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

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
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QCloseEvent
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QFileDialog,
        QFormLayout,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QListWidget,
        QScrollArea,
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


CONFIG_FILE_NAME = "photo_manager_config.json"
SYNC_LOG_NAME = "photo_manager_sync_log.csv"
BLUR_SCRIPT_NAME = "blur_tool.py"

DATE_SOURCES = ("exif", "mtime", "ctime")
PENDING_STATUS = "pending"


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
    settle_seconds: float
    stable_checks: int
    poll_interval: float
    blur_csv: str
    blur_threshold: float
    blur_top: int
    auto_delete_max: int
    auto_delete_hard: bool
    autostart_background: bool


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
    settle_seconds: float
    stable_checks: int
    poll_interval: float
    blur_csv: Path
    blur_threshold: float
    blur_top: int
    auto_delete_max: int
    auto_delete_hard: bool


class SyncWatchService:
    def __init__(self, app: "PhotoManagerWindow") -> None:
        self.app = app
        self.observer = None
        self.running = False
        self.cfg: Optional[RuntimeConfig] = None
        self._recent: Dict[str, float] = {}
        self._lock = threading.Lock()

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
        self.app.log(f"Background sync started. Watching: {cfg.source}")

    def stop(self) -> None:
        if not self.running:
            return
        try:
            if self.observer is not None:
                self.observer.stop()
                self.observer.join(timeout=5)
        finally:
            self.observer = None
            self.running = False
            self.cfg = None
            self.app.log("Background sync stopped.")

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
            self.app.append_sync_records(cfg.root, records)
            self.app.log(f"[watch][dry-run] {path.name} -> {len(action.dsts)} destinations")
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
                self.app.log(f"[watch] Copy failed: {path.name} -> {dst} ({exc})")

        all_ok = True
        for dst in copied_dsts:
            try:
                ok, _method = verify_copy(path, dst, full_hash=cfg.full_hash)
                if not ok:
                    all_ok = False
                    self.app.log(f"[watch] Verify failed: {path.name} -> {dst.name}")
            except Exception as exc:
                all_ok = False
                self.app.log(f"[watch] Verify error for {dst.name}: {exc}")

        self.app.append_sync_records(cfg.root, records)

        if all_ok:
            msg = f"[watch] Synced: {path.name} -> year {action.year}"
            if "snapshot" in action.flags:
                msg += " + SnapShots"
            if "snapchat" in action.flags:
                msg += " + Snapchat"
            self.app.log(msg)
        else:
            self.app.log(f"[watch] Sync finished with verification issues for: {path.name}")

        if all_ok and cfg.watch_delete:
            try:
                path.unlink(missing_ok=True)
                self.app.log(f"[watch] Deleted source after sync: {path.name}")
            except Exception as exc:
                self.app.log(f"[watch] Could not delete source {path.name}: {exc}")


class PhotoManagerWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.script_dir = Path(__file__).resolve().parent
        self.config_path = self.script_dir / CONFIG_FILE_NAME

        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.log_lock = threading.Lock()
        self.worker_thread: Optional[threading.Thread] = None
        self.worker_name = ""
        self.sync_service = SyncWatchService(self)

        self.sync_log_columns = ["ts", "mode", "src", "dst", "year", "flags", "status"]

        self.setWindowTitle("Photo Manager Pro")
        self.resize(1240, 900)
        self._build_ui()
        self._apply_theme()

        loaded_cfg = self._load_config()
        self._apply_config(loaded_cfg)
        self.log("Configuration loaded.")
        self._set_bg_running(False)
        self.on_compare_preview()

        self.log_timer = QTimer(self)
        self.log_timer.timeout.connect(self._drain_log_queue)
        self.log_timer.start(150)

        if self.autostart_background_check.isChecked():
            QTimer.singleShot(500, self.on_start_background)

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        main_layout = QVBoxLayout(root)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        top_bar = QGroupBox("Operations")
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(10, 8, 10, 8)
        top_layout.setSpacing(8)

        self.compare_btn = QPushButton("Compare Preview")
        self.compare_btn.setObjectName("primaryAction")
        self.compare_btn.setMinimumHeight(38)
        self.run_sync_btn = QPushButton("Synchronize")
        self.run_sync_btn.setObjectName("primaryAction")
        self.run_sync_btn.setMinimumHeight(38)
        self.start_background_btn = QPushButton("Start BG")
        self.stop_background_btn = QPushButton("Stop BG")
        self.bg_status_label = QLabel("Background sync: stopped")
        self.bg_status_label.setObjectName("statusLabel")

        top_layout.addWidget(self.compare_btn)
        top_layout.addWidget(self.run_sync_btn)
        top_layout.addSpacing(8)
        top_layout.addWidget(self.start_background_btn)
        top_layout.addWidget(self.stop_background_btn)
        top_layout.addSpacing(14)
        top_layout.addWidget(self.bg_status_label)
        top_layout.addStretch(1)
        main_layout.addWidget(top_bar)

        workspace_splitter = QSplitter()
        workspace_splitter.setChildrenCollapsible(False)

        left_panel = QWidget()
        left_panel.setObjectName("settingsPanel")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        config_group = QGroupBox("Configuration")
        config_layout = QFormLayout(config_group)
        config_layout.setSpacing(8)
        self.root_edit, root_row = self._path_row(self.on_browse_root)
        self.source_edit, source_row = self._path_row(self.on_browse_source)
        self.blur_csv_edit, blur_csv_row = self._path_row(self.on_browse_blur_csv)
        config_layout.addRow("Root folder", root_row)
        config_layout.addRow("Source folder", source_row)
        config_layout.addRow("Blur CSV", blur_csv_row)

        self.save_settings_btn = QPushButton("Save Settings")
        self.save_settings_btn.setObjectName("secondaryAction")
        config_layout.addRow("", self.save_settings_btn)
        left_layout.addWidget(config_group)

        sync_group = QGroupBox("Sync Parameters")
        sync_layout = QGridLayout(sync_group)
        sync_layout.setHorizontalSpacing(8)
        sync_layout.setVerticalSpacing(6)

        self.date_source_combo = QComboBox()
        self.date_source_combo.addItems(list(DATE_SOURCES))
        self.settle_edit = QLineEdit("1.5")
        self.stable_checks_edit = QLineEdit("3")
        self.poll_interval_edit = QLineEdit("0.5")

        sync_layout.addWidget(QLabel("Date source"), 0, 0)
        sync_layout.addWidget(self.date_source_combo, 0, 1)
        sync_layout.addWidget(QLabel("Settle"), 1, 0)
        sync_layout.addWidget(self.settle_edit, 1, 1)
        sync_layout.addWidget(QLabel("Stable checks"), 2, 0)
        sync_layout.addWidget(self.stable_checks_edit, 2, 1)
        sync_layout.addWidget(QLabel("Poll interval"), 3, 0)
        sync_layout.addWidget(self.poll_interval_edit, 3, 1)

        self.recursive_check = QCheckBox("Recursive source scan")
        self.include_nonmedia_check = QCheckBox("Include non-media files")
        self.full_hash_check = QCheckBox("Verify with full SHA256")
        self.dry_run_check = QCheckBox("Dry-run mode")
        self.delete_after_sync_check = QCheckBox("Delete source after batch sync")
        self.watch_delete_check = QCheckBox("Delete source in background sync")
        self.autostart_background_check = QCheckBox("Autostart background on launch")

        sync_layout.addWidget(self.recursive_check, 4, 0, 1, 2)
        sync_layout.addWidget(self.include_nonmedia_check, 5, 0, 1, 2)
        sync_layout.addWidget(self.full_hash_check, 6, 0, 1, 2)
        sync_layout.addWidget(self.dry_run_check, 7, 0, 1, 2)
        sync_layout.addWidget(self.delete_after_sync_check, 8, 0, 1, 2)
        sync_layout.addWidget(self.watch_delete_check, 9, 0, 1, 2)
        sync_layout.addWidget(self.autostart_background_check, 10, 0, 1, 2)
        left_layout.addWidget(sync_group)

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
        self.blur_autodelete_btn = QPushButton("Auto Delete")
        self.blur_autodelete_btn.setObjectName("dangerAction")
        blur_layout.addWidget(self.blur_scan_btn, 4, 0, 1, 2)
        blur_layout.addWidget(self.blur_review_btn, 5, 0, 1, 2)
        blur_layout.addWidget(self.blur_autodelete_btn, 6, 0, 1, 2)
        left_layout.addWidget(blur_group)
        left_layout.addStretch(1)

        left_scroll = QScrollArea()
        left_scroll.setObjectName("settingsScroll")
        left_scroll.viewport().setObjectName("settingsViewport")
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setMinimumWidth(340)
        left_scroll.setWidget(left_panel)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        compare_group = QGroupBox("Compare Workspace")
        compare_layout = QVBoxLayout(compare_group)
        compare_layout.setContentsMargins(8, 8, 8, 8)
        compare_layout.setSpacing(6)

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

        compare_layout.addWidget(preview_split)
        right_layout.addWidget(compare_group, stretch=1)

        workspace_splitter.addWidget(left_scroll)
        workspace_splitter.addWidget(right_panel)
        workspace_splitter.setSizes([360, 860])
        main_layout.addWidget(workspace_splitter, stretch=1)

        log_group = QGroupBox("Activity Log")
        log_layout = QVBoxLayout(log_group)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(90)
        log_layout.addWidget(self.log_view)
        main_layout.addWidget(log_group)

        self.save_settings_btn.clicked.connect(self.on_save_settings)
        self.compare_btn.clicked.connect(self.on_compare_preview)
        self.run_sync_btn.clicked.connect(self.on_run_sync_now)
        self.start_background_btn.clicked.connect(self.on_start_background)
        self.stop_background_btn.clicked.connect(self.on_stop_background)
        self.blur_scan_btn.clicked.connect(self.on_blur_scan)
        self.blur_review_btn.clicked.connect(self.on_blur_review)
        self.blur_autodelete_btn.clicked.connect(self.on_blur_auto_delete)

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
            QMainWindow { background: #191c22; color: #d7dde8; }
            QWidget { color: #d7dde8; }
            QLabel { color: #cdd4e2; }
            QScrollArea#settingsScroll {
                border: none;
                background: #191c22;
            }
            QWidget#settingsViewport, QWidget#settingsPanel {
                background: #191c22;
            }
            QScrollBar:vertical {
                background: #15181e;
                border-left: 1px solid #3a3f49;
                width: 12px;
                margin: 0;
            }
            QScrollBar::handle:vertical {
                background: #526078;
                border-radius: 5px;
                min-height: 28px;
            }
            QScrollBar::handle:vertical:hover { background: #65748e; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                border: none;
                background: transparent;
                height: 0;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: #15181e;
            }
            QGroupBox {
                border: 1px solid #3a3f49;
                border-radius: 7px;
                margin-top: 14px;
                font-weight: 600;
                background: #21252d;
                color: #d7dde8;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 6px;
                color: #9ab6ff;
            }
            QLineEdit, QComboBox, QListWidget, QPlainTextEdit {
                border: 1px solid #414854;
                border-radius: 5px;
                padding: 5px;
                background: #15181e;
                color: #d8deea;
                selection-background-color: #1c4ca6;
            }
            QListWidget { font-family: Consolas, 'Courier New', monospace; }
            QLabel#pathLabel {
                color: #9ea8b8;
                padding: 2px 4px;
                border: 1px solid #3a404b;
                border-radius: 4px;
                background: #171a20;
            }
            QPushButton {
                border: 1px solid #4b515e;
                border-radius: 6px;
                padding: 7px 12px;
                background: #2d323c;
                color: #e8edf8;
                font-weight: 600;
            }
            QPushButton:hover { background: #383e4a; border-color: #61708a; }
            QPushButton:pressed { background: #242a33; }
            QPushButton#primaryAction {
                background: #0f3f9c;
                border-color: #2f6ad8;
                color: #f0f6ff;
                font-size: 14px;
                font-weight: 700;
            }
            QPushButton#primaryAction:hover { background: #1852be; }
            QPushButton#secondaryAction {
                background: #2a3448;
                border-color: #4a5e84;
                color: #dce8ff;
            }
            QPushButton#secondaryAction:hover { background: #334464; }
            QPushButton#dangerAction {
                background: #6d1f25;
                border-color: #a33f49;
                color: #ffe9ea;
            }
            QPushButton#dangerAction:hover { background: #8a2831; }
            QPushButton#toolbarButton {
                background: #2a3039;
                border-color: #434a58;
            }
            QCheckBox { padding: 2px; color: #cdd4e2; }
            QLabel#statusLabel { color: #df6f78; font-weight: 700; }
            """
        )

    def _default_config(self) -> AppConfig:
        return AppConfig(
            root_dir=str(self.script_dir),
            source_dir="Sorting folder",
            date_source="exif",
            recursive=True,
            include_nonmedia=False,
            full_hash=False,
            dry_run=False,
            delete_after_sync=False,
            watch_delete=False,
            settle_seconds=1.5,
            stable_checks=3,
            poll_interval=0.5,
            blur_csv="blur_candidates.csv",
            blur_threshold=120.0,
            blur_top=0,
            auto_delete_max=50,
            auto_delete_hard=False,
            autostart_background=False,
        )

    def _load_config(self) -> AppConfig:
        cfg = self._default_config()
        if not self.config_path.exists():
            return cfg
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
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
        self.settle_edit.setText(str(cfg.settle_seconds))
        self.stable_checks_edit.setText(str(cfg.stable_checks))
        self.poll_interval_edit.setText(str(cfg.poll_interval))
        self.blur_threshold_edit.setText(str(cfg.blur_threshold))
        self.blur_top_edit.setText(str(cfg.blur_top))
        self.auto_delete_max_edit.setText(str(cfg.auto_delete_max))
        self.auto_delete_hard_check.setChecked(bool(cfg.auto_delete_hard))

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
            settle_seconds=self._parse_float(self.settle_edit.text(), "Settle seconds", 0.0),
            stable_checks=self._parse_int(self.stable_checks_edit.text(), "Stable checks", 1),
            poll_interval=self._parse_float(self.poll_interval_edit.text(), "Poll interval", 0.05),
            blur_csv=self.blur_csv_edit.text().strip(),
            blur_threshold=self._parse_float(self.blur_threshold_edit.text(), "Blur threshold", 0.0),
            blur_top=self._parse_int(self.blur_top_edit.text(), "Blur top N", 0),
            auto_delete_max=self._parse_int(self.auto_delete_max_edit.text(), "Auto delete max", 0),
            auto_delete_hard=self.auto_delete_hard_check.isChecked(),
            autostart_background=self.autostart_background_check.isChecked(),
        )

    def _resolve_runtime_config(self) -> RuntimeConfig:
        cfg = self._build_config_from_widgets()
        if cfg.date_source not in DATE_SOURCES:
            raise ValueError("Date source must be one of: exif, mtime, ctime")

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

    def _get_runtime_or_message(self) -> Optional[RuntimeConfig]:
        try:
            return self._resolve_runtime_config()
        except Exception as exc:
            QMessageBox.critical(self, "Invalid settings", str(exc))
            return None

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

    def _start_worker(self, name: str, target, *args) -> None:
        if self.worker_thread is not None and self.worker_thread.is_alive():
            QMessageBox.information(self, "Task in progress", f"Task already running: {self.worker_name}")
            return

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

    def _persist_settings(self, show_message: bool) -> bool:
        try:
            cfg = self._build_config_from_widgets()
            self.config_path.write_text(json.dumps(cfg.__dict__, ensure_ascii=False, indent=2), encoding="utf-8")
            self.log(f"Settings saved to: {self.config_path}")
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
            self.log("compare-preview: source folder does not exist.")
        else:
            source_count = 0
            for p in iter_source_files(cfg.source, recursive=cfg.recursive):
                if not is_media_file(p, include_nonmedia=cfg.include_nonmedia):
                    continue
                source_count += 1
                if source_count <= 300:
                    try:
                        rel = p.relative_to(cfg.source)
                    except Exception:
                        rel = p
                    self.source_preview_list.addItem(str(rel))
            if source_count == 0:
                self.source_preview_list.addItem("No matching files found.")
            elif source_count > 300:
                self.source_preview_list.addItem(f"... and {source_count - 300} more files")

        if not cfg.root.exists():
            self.target_preview_list.addItem("Root folder does not exist.")
            self.log("compare-preview: root folder does not exist.")
        else:
            year_dirs = []
            for d in cfg.root.iterdir():
                if d.is_dir() and d.name.isdigit() and len(d.name) == 4:
                    year_dirs.append(d)
            year_dirs.sort(key=lambda d: d.name)

            if not year_dirs:
                self.target_preview_list.addItem("No YYYY year folders found in root.")
            else:
                for yd in year_dirs[:40]:
                    file_count = self._quick_file_count(yd, limit=20000)
                    self.target_preview_list.addItem(f"{yd.name}    [{file_count} files]")
                if len(year_dirs) > 40:
                    self.target_preview_list.addItem(f"... and {len(year_dirs) - 40} more year folders")

            for special in ("SnapShots", "Snapchat"):
                sp = cfg.root / special
                if sp.exists() and sp.is_dir():
                    cnt = self._quick_file_count(sp, limit=20000)
                    self.target_preview_list.addItem(f"{special}    [{cnt} files]")

        self.log("compare-preview: refreshed.")

    def _quick_file_count(self, folder: Path, limit: int) -> int:
        count = 0
        for _ in folder.rglob("*"):
            if _.is_file():
                count += 1
                if count >= limit:
                    return count
        return count

    def _run_sync_now_worker(self, cfg: RuntimeConfig) -> None:
        self.log(f"Batch sync root={cfg.root}")
        self.log(f"Batch sync source={cfg.source}")

        files: List[Path] = []
        seen = 0
        t0 = time.time()
        for path in iter_source_files(cfg.source, recursive=cfg.recursive):
            seen += 1
            if is_media_file(path, include_nonmedia=cfg.include_nonmedia):
                files.append(path)
            if seen % 400 == 0:
                self.log(f"scan: checked={seen}, matched={len(files)}")

        self.log(f"scan complete: checked={seen}, matched={len(files)}, elapsed={time.time() - t0:.1f}s")
        if not files:
            self.log("No files matched current settings.")
            return

        actions = []
        for idx, path in enumerate(files, start=1):
            try:
                actions.append(plan_action(cfg.root, path, date_source=cfg.date_source))
            except Exception as exc:
                self.log(f"plan skipped: {path} ({exc})")
            if idx % 400 == 0:
                self.log(f"planned actions: {idx}/{len(files)}")

        if not actions:
            self.log("No valid actions were generated.")
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
                    self.log(f"copy error: {action.src.name} -> {dst.name}: {exc}")

            if idx % 250 == 0 or idx == len(actions):
                self.log(f"copy progress: {idx}/{len(actions)}")

        log_path = self.append_sync_records(cfg.root, records)
        self.log(f"sync log updated: {log_path}")

        if cfg.dry_run:
            self.log("Dry-run complete. No files were copied or deleted.")
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
                    self.log(f"verify failed: {action.src.name} -> {dst.name}")
            if idx % 250 == 0 or idx == len(actions):
                self.log(f"verify progress: {idx}/{len(actions)}")

        if copy_errors > 0 or verify_failures > 0:
            self.log(f"Sync completed with errors (copy={copy_errors}, verify={verify_failures}).")
            self.log("Source deletion skipped due to errors.")
            return

        self.log("All copied files verified successfully.")
        if cfg.delete_after_sync:
            deleted = 0
            failed = 0
            for action in actions:
                try:
                    action.src.unlink(missing_ok=True)
                    deleted += 1
                except Exception as exc:
                    failed += 1
                    self.log(f"delete failed: {action.src} ({exc})")
            self.log(f"Source cleanup: deleted={deleted}, failed={failed}")

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

    def _set_bg_running(self, running: bool) -> None:
        if running:
            self.bg_status_label.setText("Background sync: running")
            self.bg_status_label.setStyleSheet("color: #0f5d12; font-weight: 600;")
        else:
            self.bg_status_label.setText("Background sync: stopped")
            self.bg_status_label.setStyleSheet("color: #7a1111; font-weight: 600;")

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
        self._start_worker("blur-scan", self._run_subprocess_worker, cmd, "blur-scan")

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
        self.log(f"{tag}: output CSV ready.")

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
                continue

            try:
                if cfg.auto_delete_hard:
                    path.unlink(missing_ok=True)
                    self._append_blur_decision(cfg.blur_csv, path, "deleted", score)
                else:
                    send2trash(str(path))
                    self._append_blur_decision(cfg.blur_csv, path, "trashed", score)
                deleted += 1
            except Exception as exc:
                failed += 1
                self._append_blur_decision(cfg.blur_csv, path, "error", score, extra={"error": str(exc)})
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

    def on_browse_source(self) -> None:
        start = self.root_edit.text().strip() or str(self.script_dir)
        path = QFileDialog.getExistingDirectory(self, "Choose source folder", start)
        if path:
            self.source_edit.setText(path)

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

    def closeEvent(self, event: QCloseEvent) -> None:
        self.sync_service.stop()
        super().closeEvent(event)


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = PhotoManagerWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
