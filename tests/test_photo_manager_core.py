import sys
from datetime import datetime
from pathlib import Path

import pytest

from photo_manager_core import (
    RuntimeConfig,
    default_config_path,
    default_photo_root,
    is_sync_time_allowed,
    parse_hour_windows,
    parse_weekly_hours,
    serialize_weekly_hours,
    user_config_dir,
    weekly_schedule_summary,
)


def runtime_config(tmp_path: Path, *, hours: str = "0-24", weekly: str = "") -> RuntimeConfig:
    return RuntimeConfig(
        root=tmp_path,
        source=tmp_path / "source",
        date_source="exif",
        recursive=True,
        include_nonmedia=False,
        full_hash=False,
        dry_run=False,
        delete_after_sync=False,
        watch_delete=False,
        sync_allowed_hours=hours,
        sync_weekly_hours=weekly,
        settle_seconds=1.5,
        stable_checks=3,
        poll_interval=0.5,
        blur_csv=tmp_path / "blur_candidates.csv",
        blur_threshold=120.0,
        blur_top=0,
        auto_delete_max=50,
        auto_delete_hard=False,
    )


def test_user_config_dir_uses_appdata_on_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    appdata = tmp_path / "Roaming"
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    assert user_config_dir() == appdata / "PhotoManagerPro"
    assert default_config_path() == appdata / "PhotoManagerPro" / "photo_manager_config.json"


def test_default_photo_root_prefers_pictures(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pictures = tmp_path / "Pictures"
    pictures.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    assert default_photo_root() == pictures


def test_parse_hour_windows_accepts_daytime_and_overnight_ranges() -> None:
    assert parse_hour_windows("8-12,14-18") == [(8, 12), (14, 18)]
    assert parse_hour_windows("22-7") == [(22, 7)]


@pytest.mark.parametrize("raw", ["8", "24-25", "12-12", "x-y"])
def test_parse_hour_windows_rejects_invalid_ranges(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_hour_windows(raw)


def test_is_sync_time_allowed_uses_daily_ranges(tmp_path: Path) -> None:
    cfg = runtime_config(tmp_path, hours="8-18")

    assert is_sync_time_allowed(cfg, datetime(2026, 5, 4, 9, 0)) is True
    assert is_sync_time_allowed(cfg, datetime(2026, 5, 4, 19, 0)) is False


def test_is_sync_time_allowed_supports_overnight_ranges(tmp_path: Path) -> None:
    cfg = runtime_config(tmp_path, hours="22-7")

    assert is_sync_time_allowed(cfg, datetime(2026, 5, 4, 23, 0)) is True
    assert is_sync_time_allowed(cfg, datetime(2026, 5, 5, 3, 0)) is True
    assert is_sync_time_allowed(cfg, datetime(2026, 5, 5, 12, 0)) is False


def test_weekly_schedule_overrides_daily_fallback(tmp_path: Path) -> None:
    cfg = runtime_config(tmp_path, hours="0-24", weekly="mon=8-10\ntue=")

    assert is_sync_time_allowed(cfg, datetime(2026, 5, 4, 9, 0)) is True
    assert is_sync_time_allowed(cfg, datetime(2026, 5, 4, 11, 0)) is False
    assert is_sync_time_allowed(cfg, datetime(2026, 5, 5, 9, 0)) is False
    assert is_sync_time_allowed(cfg, datetime(2026, 5, 6, 9, 0)) is True


def test_weekly_schedule_summary_counts_allowed_hours() -> None:
    schedule = parse_weekly_hours("mon=8-10;tue=22-1;wed=")

    assert schedule["mon"] == [(8, 10)]
    assert schedule["tue"] == [(22, 1)]
    assert "wed" in schedule
    assert "mon=8-10" in serialize_weekly_hours(schedule)
    assert weekly_schedule_summary("mon=8-10;tue=22-1;wed=", "0-24") == (
        "Weekly schedule: 5/168 h allowed, 5 blocked day(s)"
    )
