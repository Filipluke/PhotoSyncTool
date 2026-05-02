from pathlib import Path

import pytest

from photo_manager_features import DuplicateScanCancelled, scan_duplicates


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
