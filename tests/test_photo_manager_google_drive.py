from pathlib import Path

from photo_manager_google_drive import (
    DrivePlanEntry,
    RemoteDriveItem,
    build_download_plan,
    build_upload_plan,
    connect,
    desktop_credentials_payload,
    default_index_path,
    drive_query_literal,
    get_cloud_state,
    record_cloud_upload,
    resolve_download_target,
    write_desktop_credentials_file,
    write_download_plan_csv,
    write_plan_csv,
)


def create_image(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake-jpeg-bytes-for-planning-tests")
    return path


def test_build_upload_plan_filters_internal_files_and_media(tmp_path: Path) -> None:
    root = tmp_path / "library"
    photo = create_image(root / "2026" / "IMG_20260512_120000.jpg")
    create_image(root / ".photo_manager_cache" / "thumb.jpg")
    (root / "photo_manager_sync_log.csv").write_text("private-ish log", encoding="utf-8")
    (root / "notes.txt").write_text("not media", encoding="utf-8")

    plan, stats = build_upload_plan(root, remote_root_name="Remote")

    assert stats.scanned == 1
    assert stats.planned == 1
    assert [entry.local_path for entry in plan] == [photo.resolve()]
    assert plan[0].relative_path == "2026/IMG_20260512_120000.jpg"
    assert plan[0].remote_path == "Remote/2026/IMG_20260512_120000.jpg"
    assert plan[0].action == "upload"


def test_build_upload_plan_skips_unchanged_uploaded_state(tmp_path: Path) -> None:
    root = tmp_path / "library"
    photo = create_image(root / "2026" / "IMG_20260512_120000.jpg").resolve()
    stat = photo.stat()
    entry = DrivePlanEntry(
        local_path=photo,
        relative_path="2026/IMG_20260512_120000.jpg",
        remote_path="Remote/2026/IMG_20260512_120000.jpg",
        remote_file_id="",
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        quick_hash="",
        action="upload",
        reason="new_or_changed",
    )

    with connect(default_index_path(root)) as conn:
        record_cloud_upload(conn, entry, remote_file_id="drive-file-id")
        conn.commit()
        state = get_cloud_state(conn)

    plan, stats = build_upload_plan(root, remote_root_name="Remote", state=state)

    assert stats.scanned == 1
    assert stats.planned == 0
    assert stats.skipped == 1
    assert plan[0].action == "skip"
    assert plan[0].reason == "unchanged"
    assert plan[0].remote_file_id == "drive-file-id"


def test_build_upload_plan_updates_changed_uploaded_state(tmp_path: Path) -> None:
    root = tmp_path / "library"
    photo = create_image(root / "2026" / "IMG_20260512_120000.jpg").resolve()
    stat = photo.stat()
    entry = DrivePlanEntry(
        local_path=photo,
        relative_path="2026/IMG_20260512_120000.jpg",
        remote_path="Remote/2026/IMG_20260512_120000.jpg",
        remote_file_id="",
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        quick_hash="",
        action="upload",
        reason="new",
    )

    with connect(default_index_path(root)) as conn:
        record_cloud_upload(conn, entry, remote_file_id="drive-file-id")
        conn.commit()
        photo.write_bytes(b"changed bytes")
        state = get_cloud_state(conn)

    plan, stats = build_upload_plan(root, remote_root_name="Remote", state=state)

    assert stats.planned == 1
    assert plan[0].action == "update"
    assert plan[0].reason == "changed"
    assert plan[0].remote_file_id == "drive-file-id"


def test_write_plan_csv_exports_reviewable_rows(tmp_path: Path) -> None:
    root = tmp_path / "library"
    photo = create_image(root / "photo.jpg").resolve()
    plan, _stats = build_upload_plan(root, remote_root_name="Remote")

    out = write_plan_csv(plan, tmp_path / "plan.csv")

    text = out.read_text(encoding="utf-8")
    assert "action,reason,local_path,relative_path,remote_path,remote_file_id,size_bytes,mtime_ns,quick_hash" in text
    assert str(photo) in text
    assert "Remote/photo.jpg" in text


def remote_item(relative_path: str, *, size_bytes: int = 11, md5_checksum: str = "") -> RemoteDriveItem:
    return RemoteDriveItem(
        file_id=f"id-{relative_path}",
        name=Path(relative_path).name,
        relative_path=relative_path,
        remote_path=f"Remote/{relative_path}",
        mime_type="image/jpeg",
        size_bytes=size_bytes,
        md5_checksum=md5_checksum,
        modified_time="2026-05-12T10:00:00Z",
        web_view_link="",
    )


def test_build_download_plan_downloads_missing_remote_media(tmp_path: Path) -> None:
    root = tmp_path / "library"
    plan, stats = build_download_plan(root, [remote_item("2026/photo.jpg")])

    assert stats.scanned == 1
    assert stats.planned == 1
    assert stats.conflicts == 0
    assert plan[0].action == "download"
    assert plan[0].reason == "missing_local"
    assert plan[0].local_path == (root / "2026" / "photo.jpg").resolve()


def test_build_download_plan_reports_local_conflict_without_overwrite(tmp_path: Path) -> None:
    root = tmp_path / "library"
    local = create_image(root / "2026" / "photo.jpg")

    plan, stats = build_download_plan(root, [remote_item("2026/photo.jpg", size_bytes=local.stat().st_size + 10)])

    assert stats.planned == 0
    assert stats.conflicts == 1
    assert plan[0].action == "conflict"
    assert plan[0].reason == "local_file_exists_different"


def test_build_download_plan_can_plan_overwrite(tmp_path: Path) -> None:
    root = tmp_path / "library"
    local = create_image(root / "2026" / "photo.jpg")

    plan, stats = build_download_plan(
        root,
        [remote_item("2026/photo.jpg", size_bytes=local.stat().st_size + 10)],
        overwrite=True,
    )

    assert stats.planned == 1
    assert stats.conflicts == 0
    assert plan[0].action == "download"
    assert plan[0].reason == "overwrite_local_changed"


def test_build_download_plan_skips_same_size_when_hash_not_requested(tmp_path: Path) -> None:
    root = tmp_path / "library"
    local = create_image(root / "2026" / "photo.jpg")

    plan, stats = build_download_plan(root, [remote_item("2026/photo.jpg", size_bytes=local.stat().st_size)])

    assert stats.planned == 0
    assert stats.skipped == 1
    assert plan[0].action == "skip"
    assert plan[0].reason == "unchanged_size_unverified"


def test_write_download_plan_csv_exports_reviewable_rows(tmp_path: Path) -> None:
    root = tmp_path / "library"
    plan, _stats = build_download_plan(root, [remote_item("2026/photo.jpg")])

    out = write_download_plan_csv(plan, tmp_path / "download-plan.csv")

    text = out.read_text(encoding="utf-8")
    assert "action,reason,local_path,relative_path,remote_path,remote_file_id,size_bytes,md5_checksum" in text
    assert "Remote/2026/photo.jpg" in text


def test_resolve_download_target_rejects_path_escape(tmp_path: Path) -> None:
    try:
        resolve_download_target(tmp_path, "../escape.jpg")
    except ValueError as exc:
        assert "Unsafe Google Drive relative path" in str(exc)
    else:
        raise AssertionError("Expected unsafe path to be rejected")


def test_drive_query_literal_escapes_quotes_and_backslashes() -> None:
    assert drive_query_literal("Bob's \\ Photos") == "'Bob\\'s \\\\ Photos'"


def test_desktop_credentials_payload_matches_google_installed_app_shape() -> None:
    payload = desktop_credentials_payload("client-id.apps.googleusercontent.com", "secret", project_id="demo")

    installed = payload["installed"]
    assert installed["client_id"] == "client-id.apps.googleusercontent.com"
    assert installed["client_secret"] == "secret"
    assert installed["project_id"] == "demo"
    assert installed["token_uri"] == "https://oauth2.googleapis.com/token"
    assert installed["redirect_uris"] == ["http://localhost"]


def test_write_desktop_credentials_file_creates_parent_and_json(tmp_path: Path) -> None:
    path = write_desktop_credentials_file(tmp_path / "config" / "google.json", "client-id", "secret")

    text = path.read_text(encoding="utf-8")
    assert '"installed"' in text
    assert '"client_id": "client-id"' in text
    assert '"client_secret": "secret"' in text
