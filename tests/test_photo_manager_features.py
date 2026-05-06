import csv
from pathlib import Path

import pytest

from photo_manager_features import DuplicateScanCancelled, export_sync_report, list_gallery_items, scan_duplicates
from photo_manager_index import index_sync_records, rebuild_index
from fixtures import create_demo_library


def test_scan_duplicates_finds_exact_matches_in_root(tmp_path: Path) -> None:
    (tmp_path / "one.jpg").write_bytes(b"same image bytes")
    (tmp_path / "two.jpg").write_bytes(b"same image bytes")

    actions = scan_duplicates(tmp_path)

    assert len(actions) == 1
    assert {actions[0].keep.name, actions[0].remove.name} == {"one.jpg", "two.jpg"}
    assert actions[0].reason == "same_sha256"


def test_scan_duplicates_ignores_internal_cache(tmp_path: Path) -> None:
    cache = tmp_path / ".photo_manager_cache" / "thumbnails"
    cache.mkdir(parents=True)
    (cache / "one.jpg").write_bytes(b"same")
    (cache / "two.jpg").write_bytes(b"same")

    assert scan_duplicates(tmp_path) == []


def test_scan_duplicates_can_be_cancelled(tmp_path: Path) -> None:
    year_dir = tmp_path / "2026"
    year_dir.mkdir()
    (year_dir / "one.jpg").write_bytes(b"same")
    (year_dir / "two.jpg").write_bytes(b"same")

    with pytest.raises(DuplicateScanCancelled):
        scan_duplicates(tmp_path, should_cancel=lambda: True)


def test_demo_fixture_indexes_gallery_media(tmp_path: Path) -> None:
    demo = create_demo_library(tmp_path)
    (demo.root / "blur_candidates.csv.decisions.jsonl").write_text('{"status": "keep"}\n', encoding="utf-8")

    stats = rebuild_index(demo.root, include_nonmedia=True)
    gallery = list_gallery_items(demo.root, limit=20)

    assert stats.indexed == 3
    assert stats.failed == 0
    assert {item.name for item in gallery} >= {"library_photo.jpg", "duplicate.jpg", "duplicate__1.jpg"}
    assert {action.remove.name for action in scan_duplicates(demo.root)} == {"duplicate__1.jpg"}


def test_export_sync_report_writes_indexed_sync_events(tmp_path: Path) -> None:
    src = tmp_path / "source.jpg"
    dst = tmp_path / "2026" / "source.jpg"
    src.write_bytes(b"image bytes")
    dst.parent.mkdir()
    dst.write_bytes(b"image bytes")

    index_sync_records(
        tmp_path,
        [
            {
                "mode": "batch",
                "src": str(src),
                "dst": str(dst),
                "year": 2026,
                "flags": "verified",
                "status": "copied",
            }
        ],
    )

    out = export_sync_report(tmp_path, tmp_path / "report.csv")

    with out.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 1
    assert rows[0]["mode"] == "batch"
    assert rows[0]["status"] == "copied"
    assert rows[0]["year"] == "2026"
    assert rows[0]["src"] == str(src)
    assert rows[0]["dst"] == str(dst)
