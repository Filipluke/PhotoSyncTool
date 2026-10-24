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
            "Phone Connect",
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
        assert window.lan_start_btn.text() == "Start Receiver"
        assert window.lan_sync_inbox_btn.text() == "Sort Inbox"
        assert window.lan_pairing_code_edit.text()
        assert window.google_client_secret_edit.echoMode() == window.google_client_secret_edit.EchoMode.Password
        assert "OAuth" in window.google_status_label.text()
        assert window.use_google_drive_btn.text() == "Use Google Drive"
        assert window.open_google_drive_btn.text() == "Open"
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


@pytest.mark.parametrize('dry_run', [False, True])
def test_gui_watcher_keeps_sources_on_failure_or_preview(tmp_path, monkeypatch, dry_run):
    from dataclasses import replace
    from types import SimpleNamespace
    import photo_manager_core as core
    from test_photo_manager_core import runtime_config
    cfg = replace(runtime_config(tmp_path), root=tmp_path / 'library', watch_delete=True, dry_run=dry_run)
    cfg.source.mkdir()
    source = cfg.source / 'IMG_20260101.jpg'
    source.write_bytes(b'photo')
    monkeypatch.setattr(photo_manager_qt, 'ensure_file_stable', lambda *a, **kw: True)
    def fail(*args, **kwargs):
        raise OSError('simulated write failure')
    monkeypatch.setattr(photo_manager_qt, 'copy_verified', fail)
    fake_app = SimpleNamespace(log=lambda _: None, is_sync_time_allowed=lambda _: True,
                               append_sync_records=core.append_sync_records)
    watcher = photo_manager_qt.SyncWatchService(fake_app)
    watcher.cfg = cfg
    watcher.process_path(source)
    assert source.read_bytes() == b'photo'
    if dry_run:
        assert not cfg.root.exists()


@pytest.mark.parametrize('size', [(1120, 720), (1920, 1080)])
def test_workspace_sizes_keyboard_and_packaged_fonts(monkeypatch, tmp_path, size):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtTest import QTest, QSignalSpy
    from PySide6.QtWidgets import QPushButton, QScrollArea
    (tmp_path / 'library').mkdir()
    monkeypatch.setattr(photo_manager_qt, 'default_photo_root', lambda: tmp_path / 'library')
    monkeypatch.setattr(photo_manager_qt, 'default_config_path', lambda: tmp_path / 'config.json')
    monkeypatch.setattr(photo_manager_qt, 'default_legacy_config_path', lambda: tmp_path / 'legacy.json')
    monkeypatch.setattr(photo_manager_qt, 'default_google_credentials_path', lambda: tmp_path / 'credentials.json')
    monkeypatch.setattr(photo_manager_qt, 'default_google_token_path', lambda: tmp_path / 'token.json')
    app = _app()
    window = PhotoManagerWindow()
    try:
        window.resize(*size)
        window.show()
        app.processEvents()
        assert (window.width(), window.height()) == size
        assert {'IBM Plex Sans', 'IBM Plex Mono'} <= set(QFontDatabase.families())
        for index in range(window.workspace_tabs.count()):
            window.workspace_tabs.setCurrentIndex(index)
            app.processEvents()
            page = window.workspace_tabs.currentWidget()
            for button in page.findChildren(QPushButton):
                if not button.isVisibleTo(page):
                    continue
                ancestor = button.parentWidget()
                in_scroll_area = False
                while ancestor and ancestor is not page:
                    in_scroll_area |= isinstance(ancestor, QScrollArea)
                    ancestor = ancestor.parentWidget()
                if not in_scroll_area:
                    top_left = button.mapTo(page, button.rect().topLeft())
                    bottom_right = button.mapTo(page, button.rect().bottomRight())
                    assert page.rect().contains(top_left), button.text()
                    assert page.rect().contains(bottom_right), button.text()
                    assert button.width() >= button.minimumSizeHint().width(), button.text()
        window.activateWindow()
        window.setFocus()
        assert QTest.qWaitForWindowActive(window, 1000)
        changed = QSignalSpy(window.workspace_tabs.currentChanged)
        QTest.keyClick(window, Qt.Key.Key_2, Qt.KeyboardModifier.AltModifier)
        app.processEvents()
        assert changed.count() or changed.wait(1000)
        assert window.workspace_tabs.currentIndex() == 1
        assert window.gallery_search_edit.accessibleName()
        assert window.gallery_queue_btn.objectName() == 'cautionAction'
        assert window.delete_trash_selected_btn.objectName() == 'dangerAction'
    finally:
        window._allow_real_close = True
        window.close()
        app.processEvents()


def test_cleanup_confirmation_defaults_to_no(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from PySide6.QtWidgets import QMessageBox
    (tmp_path / 'library').mkdir()
    monkeypatch.setattr(photo_manager_qt, 'default_photo_root', lambda: tmp_path / 'library')
    monkeypatch.setattr(photo_manager_qt, 'default_config_path', lambda: tmp_path / 'config.json')
    monkeypatch.setattr(photo_manager_qt, 'default_legacy_config_path', lambda: tmp_path / 'legacy.json')
    app = _app()
    window = PhotoManagerWindow()
    try:
        monkeypatch.setattr(window, '_get_runtime_or_message', lambda: SimpleNamespace(root=tmp_path))
        monkeypatch.setattr(window, '_selected_delete_ids', lambda: [1])
        monkeypatch.setattr(photo_manager_qt, 'list_delete_queue', lambda *a, **kw: [SimpleNamespace(id=1)])
        monkeypatch.setattr(window, '_start_worker', lambda *a: pytest.fail('Cancelled cleanup must not start a worker'))
        def decline(parent, title, message, buttons, default):
            assert default == QMessageBox.No
            assert 'recycle bin' in message
            return QMessageBox.No
        monkeypatch.setattr(QMessageBox, 'question', decline)
        window.on_delete_queue_trash_selected()
        window.on_delete_queue_trash_all()
    finally:
        window._allow_real_close = True
        window.close()
        app.processEvents()
