from pathlib import Path

from sort_photos_script import (
    is_media_file,
    safe_destination_path,
    try_get_year_from_filename,
    verify_copy,
)


def test_try_get_year_from_filename_supports_phone_patterns() -> None:
    assert try_get_year_from_filename(Path("20210924_132556.jpg")) == 2021
    assert try_get_year_from_filename(Path("IMG_20221204_225309.png")) == 2022
    assert try_get_year_from_filename(Path("VID-20200315-WA0001.mp4")) == 2020


def test_try_get_year_from_filename_rejects_invalid_dates() -> None:
    assert try_get_year_from_filename(Path("IMG_20211304_225309.jpg")) is None
    assert try_get_year_from_filename(Path("IMG_20210232_225309.jpg")) is None
    assert try_get_year_from_filename(Path("notes_18991231.txt")) is None


def test_safe_destination_path_adds_suffix_when_file_exists(tmp_path: Path) -> None:
    existing = tmp_path / "photo.jpg"
    existing.write_bytes(b"already here")

    assert safe_destination_path(tmp_path, "photo.jpg") == tmp_path / "photo__1.jpg"

    (tmp_path / "photo__1.jpg").write_bytes(b"also here")
    assert safe_destination_path(tmp_path, "photo.jpg") == tmp_path / "photo__2.jpg"


def test_verify_copy_detects_equal_and_changed_files(tmp_path: Path) -> None:
    src = tmp_path / "src.jpg"
    dst = tmp_path / "dst.jpg"
    src.write_bytes(b"same bytes")
    dst.write_bytes(b"same bytes")

    ok, method = verify_copy(src, dst)
    assert ok is True
    assert method == "quick_fp"

    dst.write_bytes(b"different")
    ok, reason = verify_copy(src, dst)
    assert ok is False
    assert reason in {"size_mismatch", "quick_fp"}


def test_is_media_file_filters_temp_and_non_media(tmp_path: Path) -> None:
    photo = tmp_path / "photo.JPG"
    text = tmp_path / "notes.txt"
    temp = tmp_path / "download.jpg.crdownload"
    photo.write_bytes(b"x")
    text.write_bytes(b"x")
    temp.write_bytes(b"x")

    assert is_media_file(photo, include_nonmedia=False) is True
    assert is_media_file(text, include_nonmedia=False) is False
    assert is_media_file(text, include_nonmedia=True) is True
    assert is_media_file(temp, include_nonmedia=True) is False
