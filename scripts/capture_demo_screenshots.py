#!/usr/bin/env python3
"""Capture sanitized GitHub Pages screenshots from a disposable demo library."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402
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
}


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("DejaVuSans-Bold.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw_demo_photo(path: Path, *, title: str, subtitle: str, palette: tuple[str, str, str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    width, height = 1600, 1000
    image = Image.new("RGB", (width, height), palette[0])
    draw = ImageDraw.Draw(image)

    band_height = height // 5
    for index, color in enumerate(palette):
        y0 = index * band_height
        draw.rectangle((0, y0, width, y0 + band_height + 80), fill=color)

    draw.polygon([(0, height), (width * 0.46, height * 0.5), (width, height)], fill=palette[1])
    draw.polygon([(width, 0), (width * 0.56, height * 0.42), (width, height * 0.18)], fill=palette[2])
    draw.rectangle((96, 116, width - 96, height - 116), outline="#e5edf7", width=8)
    draw.line((130, height - 210, width - 130, height - 210), fill="#14b8a6", width=7)
    draw.text((140, height - 340), title, fill="#f8fbff", font=_font(74))
    draw.text((144, height - 246), subtitle, fill="#d8e2ee", font=_font(34))
    image.save(path, quality=92)


def _prepare_demo_library(workspace: Path) -> tuple[Path, Path, dict[str, Path]]:
    root = workspace / "Demo Library"
    source = workspace / "Incoming Camera Roll"
    root.mkdir(parents=True)
    source.mkdir(parents=True)

    images = {
        "hero": root / "2026" / "Aurora_Workspace_20260506.jpg",
        "gallery": root / "2026" / "Neon_Gallery_20260506.jpg",
        "archive": root / "2025" / "Archive_Frame_20250314.jpg",
        "keeper": root / "2026" / "Duplicate_Master_20260506.jpg",
        "duplicate": root / "2026" / "Duplicate_Master_20260506__dup.jpg",
        "incoming": source / "Camera_Import_20260506.jpg",
    }

    _draw_demo_photo(
        images["hero"],
        title="Indexed Library",
        subtitle="Dashboard-ready demo media",
        palette=("#11131a", "#123449", "#0f766e", "#2f230d"),
    )
    _draw_demo_photo(
        images["gallery"],
        title="Gallery Preview",
        subtitle="Safe sample metadata",
        palette=("#10131a", "#0b2535", "#14b8a6", "#7f1d1d"),
    )
    _draw_demo_photo(
        images["archive"],
        title="Archive 2025",
        subtitle="Year folder workflow",
        palette=("#0b0f16", "#1b2635", "#38bdf8", "#2f230d"),
    )
    _draw_demo_photo(
        images["keeper"],
        title="Duplicate Pair",
        subtitle="Keeper candidate",
        palette=("#10131a", "#0f766e", "#0b2535", "#f59e0b"),
    )
    shutil.copy2(images["keeper"], images["duplicate"])
    _draw_demo_photo(
        images["incoming"],
        title="Incoming Import",
        subtitle="Source folder sample",
        palette=("#07090d", "#14202c", "#14b8a6", "#38bdf8"),
    )

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
            "caption": "Neon desktop workspace used as a safe gallery demo.",
            "tags": ["gallery", "indexed", "demo"],
            "details": {"backend": "demo-screenshot"},
        },
    )
    upsert_ai_metadata(
        root,
        images["hero"],
        {
            "caption": "Dashboard sample with local indexing enabled.",
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


def capture(output_dir: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="photo-manager-pro-screens-") as tmp:
        root, source, _images = _prepare_demo_library(Path(tmp))

        gui.default_config_path = lambda: Path(tmp) / "photo_manager_config.json"
        gui.default_photo_root = lambda: root

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
        window.resize(1440, 900)
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
                if window.gallery_list.item(row).text().startswith("Aurora_Workspace"):
                    target_row = row
                    break
            window.gallery_list.setCurrentRow(target_row)
            window.on_gallery_selected(window.gallery_list.currentItem(), None)
            selected_path = Path(str(window.gallery_list.currentItem().data(Qt.ItemDataRole.UserRole)))
            window.gallery_details.setPlainText(
                "\n".join(
                    [
                        _safe_label(root, selected_path),
                        "Relative: 2026/Aurora_Workspace_20260506.jpg",
                        "Year: 2026",
                        "Status: present",
                        "Tags: dashboard, local, release",
                        "Caption: Dashboard sample with local indexing enabled.",
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

        window.close()
        app.processEvents()


def main() -> None:
    output_dir = REPO_ROOT / "docs" / "screenshots"
    capture(output_dir)
    print(f"Updated screenshots in {output_dir}")


if __name__ == "__main__":
    main()
