#!/usr/bin/env python3
"""Capture sanitized GitHub Pages screenshots from a disposable demo library."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PIL import Image  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from photo_manager_features import (  # noqa: E402
    build_thumbnail,
    enqueue_delete,
    list_gallery_items,
    scan_duplicates,
    upsert_ai_metadata,
)
from photo_manager_index import (  # noqa: E402
    connect,
    default_index_path,
    index_sync_records,
    normalize_path,
    rebuild_index,
    relative_to_root,
    utc_now,
)
import photo_manager_qt as gui  # noqa: E402


SCREENSHOTS = {
    "dashboard": "dashboard.png",
    "gallery": "gallery.png",
    "duplicates": "duplicates.png",
    "settings": "settings.png",
    "diagnostics": "diagnostics.png",
    "cleanup": "cleanup.png",
}


def _copy_demo_photo(path: Path, filename: str) -> None:
    """Use bundled public-domain photographs; never fetch images during capture."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(REPO_ROOT / "docs" / "demo-photos" / filename) as image:
        image.convert("RGB").save(path, quality=92)


def _prepare_demo_library(workspace: Path) -> tuple[Path, Path, dict[str, Path]]:
    root = workspace / "Demo Library"
    source = workspace / "Incoming Camera Roll"
    root.mkdir(parents=True)
    source.mkdir(parents=True)

    images = {
        "hero": root / "2026" / "Ginger_Cat_20260506.jpg",
        "gallery": root / "2026" / "Calico_Cat_20260506.jpg",
        "archive": root / "2025" / "White_Cat_20250314.jpg",
        "keeper": root / "2026" / "Cat_Portrait_20260506.jpg",
        "duplicate": root / "2026" / "Cat_Portrait_20260506__dup.jpg",
        "incoming": source / "New_Cat_Photo_20260506.jpg",
    }

    for key, filename in (
        ("hero", "tabby-cat.webp"),
        ("gallery", "calico-cat.webp"),
        ("archive", "white-cat.webp"),
        ("keeper", "nopple-cat.webp"),
        ("incoming", "tabby-cat.webp"),
    ):
        _copy_demo_photo(images[key], filename)
    shutil.copy2(images["keeper"], images["duplicate"])

    rebuild_index(root, compute_hash=True)
    index_sync_records(
        root,
        [
            {
                "mode": "demo",
                "src": str(images["incoming"]),
                "dst": str(images["hero"]),
                "year": "2026",
                "flags": "copied,indexed",
                "status": "copied",
            },
            {
                "mode": "demo",
                "src": str(source / "Camera_Archive_20250314.jpg"),
                "dst": str(images["archive"]),
                "year": "2025",
                "flags": "copied,indexed",
                "status": "copied",
            },
        ],
        compute_hash=True,
    )
    with connect(default_index_path(root)) as conn:
        for row in conn.execute("SELECT path FROM media_files").fetchall():
            path = Path(str(row["path"]))
            if not path.is_relative_to(root):
                conn.execute("DELETE FROM media_files WHERE path = ?", (normalize_path(path),))
        conn.commit()
    upsert_ai_metadata(
        root,
        images["gallery"],
        {
            "caption": "A calico cat from the public-domain demo collection.",
            "tags": ["gallery", "indexed", "demo"],
            "details": {"backend": "demo-screenshot"},
        },
    )
    upsert_ai_metadata(
        root,
        images["hero"],
        {
            "caption": "A ginger cat from the public-domain demo collection.",
            "tags": ["dashboard", "local", "release"],
            "details": {"backend": "demo-screenshot"},
        },
    )
    enqueue_delete(
        root,
        images["duplicate"],
        reason="duplicate:same_sha256",
        source="demo-screenshot",
        details={"keep": str(images["keeper"])},
    )

    with connect(default_index_path(root)) as conn:
        blurry = images["archive"]
        stat = blurry.stat()
        conn.execute(
            """
            INSERT OR REPLACE INTO blur_results (
                path, score, threshold, status, width, height, filesize_bytes,
                mtime_epoch, source_csv, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalize_path(blurry),
                83.4,
                120.0,
                "candidate",
                1600,
                1000,
                stat.st_size,
                stat.st_mtime,
                "demo",
                utc_now(),
            ),
        )
        conn.commit()

    return root, source, images


def _safe_label(root: Path, path: Path) -> str:
    return "Demo Library/" + relative_to_root(root, path).replace(os.sep, "/")


def _process_events(app: QApplication) -> None:
    for _ in range(8):
        app.processEvents()


def _capture(window: gui.PhotoManagerWindow, app: QApplication, output_dir: Path, name: str) -> None:
    _process_events(app)
    pixmap = window.grab()
    target = output_dir / SCREENSHOTS[name]
    target.parent.mkdir(parents=True, exist_ok=True)
    if pixmap.isNull() or not pixmap.save(str(target), "PNG"):
        raise RuntimeError(f"Could not write screenshot: {target}")


def _select_tab(window: gui.PhotoManagerWindow, label: str) -> None:
    for index in range(window.workspace_tabs.count()):
        if window.workspace_tabs.tabText(index) == label:
            window.workspace_tabs.setCurrentIndex(index)
            return
    raise RuntimeError(f"Tab not found: {label}")


def capture(output_dir: Path, width: int = 1440, height: int = 900) -> None:
    with tempfile.TemporaryDirectory(prefix="photo-manager-pro-screens-") as tmp:
        root, source, _images = _prepare_demo_library(Path(tmp))

        gui.default_config_path = lambda: Path(tmp) / "photo_manager_config.json"
        gui.default_legacy_config_path = lambda: Path(tmp) / "legacy_config.json"
        # No user config, autostart, network receiver or cloud account during capture.
        gui.PhotoManagerWindow._load_config = gui.PhotoManagerWindow._default_config
        gui.default_photo_root = lambda: root
        gui.default_google_credentials_path = lambda: Path(tmp) / "credentials.json"
        gui.default_google_token_path = lambda: Path(tmp) / "token.json"
        gui.detect_google_drive_folder = lambda: None

        app = QApplication.instance() or QApplication([])
        app.setQuitOnLastWindowClosed(False)
        window = gui.PhotoManagerWindow()
        cfg = window._default_config()
        cfg.root_dir = str(root)
        cfg.source_dir = str(source)
        cfg.full_hash = True
        cfg.autostart_background = False
        cfg.start_minimized = False
        window._apply_config(cfg)
        window.root_summary_label.setText("Library: Demo Library | Source: Incoming Camera Roll")
        window.root_summary_label.setToolTip("")
        window.resize(width, height)
        window.show()

        _select_tab(window, "Dashboard")
        window.on_dashboard_refresh()
        _capture(window, app, output_dir, "dashboard")

        _select_tab(window, "Gallery")
        items = list_gallery_items(root, status="present", limit=20)
        payload = [(item, build_thumbnail(root, item.path, size=160)) for item in items]
        window._populate_gallery(payload)
        if window.gallery_list.count():
            target_row = 0
            for row in range(window.gallery_list.count()):
                if window.gallery_list.item(row).text().startswith("Ginger_Cat"):
                    target_row = row
                    break
            window.gallery_list.setCurrentRow(target_row)
            window.on_gallery_selected(window.gallery_list.currentItem(), None)
            selected_path = Path(str(window.gallery_list.currentItem().data(Qt.ItemDataRole.UserRole)))
            window.gallery_details.setPlainText(
                "\n".join(
                    [
                        _safe_label(root, selected_path),
                        "Relative: 2026/Ginger_Cat_20260506.jpg",
                        "Year: 2026",
                        "Status: present",
                        "Tags: dashboard, local, release",
                        "Caption: A ginger cat from the public-domain demo collection.",
                    ]
                )
            )
        _capture(window, app, output_dir, "gallery")

        _select_tab(window, "Duplicates")
        actions = scan_duplicates(root)
        window._populate_duplicates(actions)
        for row, action in enumerate(actions):
            window.duplicates_table.item(row, 0).setText(_safe_label(root, action.remove))
            window.duplicates_table.item(row, 1).setText(_safe_label(root, action.keep))
        if actions:
            window.duplicates_table.selectRow(0)
            window.on_duplicate_selected()
            action = actions[0]
            window.duplicate_details.setPlainText(
                "\n".join(
                    [
                        f"Keep: {_safe_label(root, action.keep)}",
                        f"Duplicate: {_safe_label(root, action.remove)}",
                        "Reason: same_sha256",
                        "Group: demo duplicate pair",
                        f"Size: {action.size_bytes:,} bytes",
                    ]
                )
            )
        _capture(window, app, output_dir, "duplicates")

        for title, name in (("Cleanup", "cleanup"), ("Settings", "settings"), ("Diagnostics", "diagnostics")):
            _select_tab(window, title)
            if name == "cleanup":
                window.on_delete_queue_refresh()
                for row in range(window.delete_table.rowCount()):
                    item = window.delete_table.item(row, 1)
                    item.setText(_safe_label(root, Path(item.text())))
            if name == "settings":
                window.root_edit.setText("Demo Library")
                window.source_edit.setText("Incoming Camera Roll")
                window.blur_csv_edit.setText("Demo Library/blur_candidates.csv")
            if name == "diagnostics":
                window._drain_log_queue()
                text = window.log_view.toPlainText().replace(str(root), "Demo Library").replace(str(source), "Incoming Camera Roll")
                window.log_view.setPlainText(text.replace(tmp, "Demo workspace"))
            _capture(window, app, output_dir, name)
        window._allow_real_close = True
        window.close()
        app.processEvents()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=REPO_ROOT / "docs" / "screenshots")
    parser.add_argument('--width', type=int, default=1440)
    parser.add_argument('--height', type=int, default=900)
    args = parser.parse_args()
    output_dir = args.output
    capture(output_dir, args.width, args.height)
    sources = ("photo_manager_qt.py", "photo_manager_theme.py", "scripts/capture_demo_screenshots.py")
    manifest = {"width": args.width, "height": args.height,
                "sources": {name: hashlib.sha256((REPO_ROOT / name).read_bytes()).hexdigest() for name in sources},
                "images": {name: hashlib.sha256((output_dir / name).read_bytes()).hexdigest()
                           for name in SCREENSHOTS.values()}}
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Updated screenshots in {output_dir}")


if __name__ == "__main__":
    main()
