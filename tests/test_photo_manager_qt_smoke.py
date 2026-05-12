from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

import photo_manager_qt
from photo_manager_qt import PhotoManagerWindow


def _app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_main_window_builds_expected_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config" / "photo_manager_config.json"

    monkeypatch.setattr(photo_manager_qt, "default_photo_root", lambda: root)
    monkeypatch.setattr(photo_manager_qt, "default_config_path", lambda: config_path)
    monkeypatch.setattr(photo_manager_qt, "default_legacy_config_path", lambda: tmp_path / "legacy_config.json")

    app = _app()
    window = PhotoManagerWindow()
    app.processEvents()

    try:
        tab_names = [window.workspace_tabs.tabText(index) for index in range(window.workspace_tabs.count())]

        assert tab_names == [
            "Dashboard",
            "Gallery",
            "Duplicates",
            "Cleanup",
            "AI Metadata",
            "Sync Plan",
            "Cloud Sync",
            "Settings",
            "Diagnostics",
        ]
        assert window.windowTitle() == "Photo Manager Pro"
        assert window.workspace_tabs.currentIndex() == 0
        assert window.centralWidget().layout().spacing() == 0
        assert window.centralWidget().layout().contentsMargins().top() == 4
        assert window.dashboard_export_btn.text() == "Export Report"
        assert window.run_sync_btn.text() == "Sync Now"
        assert window.start_background_btn.text() == "Start Watching"
        assert window.google_auth_btn.text() == "Authenticate"
        assert window.google_save_credentials_btn.text() == "Save OAuth JSON"
        assert window.google_client_secret_edit.echoMode() == window.google_client_secret_edit.EchoMode.Password
        assert "OAuth" in window.google_status_label.text()
        if sys.platform == "win32":
            assert window.service_note_label.text() == "Uses Windows Service commands."
        elif sys.platform.startswith("linux"):
            assert window.service_note_label.text() == "Uses a systemd user service."
        assert window.root_edit.text() == str(root)
        assert "Library:" in window.root_summary_label.text()
        assert "Source folder does not exist." in [
            window.source_preview_list.item(index).text() for index in range(window.source_preview_list.count())
        ]
    finally:
        window._allow_real_close = True
        window.close()
        app.processEvents()
