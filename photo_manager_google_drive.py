#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import mimetypes
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, Optional

from photo_manager_core import default_config_path, user_config_dir
from photo_manager_index import connect, default_index_path, iter_library_files, normalize_path, relative_to_root
from sort_photos_script import MEDIA_EXTS, is_media_file, quick_fingerprint


PROVIDER = "google_drive"
DEFAULT_REMOTE_ROOT_NAME = "Photo Manager Pro"
DEFAULT_CREDENTIALS_NAME = "google_drive_credentials.json"
DEFAULT_TOKEN_NAME = "google_drive_token.json"
DRIVE_FOLDER_MIME = "application/vnd.google-apps.folder"
DEFAULT_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
GOOGLE_CERT_URL = "https://www.googleapis.com/oauth2/v1/certs"

Logger = Callable[[str], None]


class GoogleDriveDependencyError(RuntimeError):
    pass


@dataclass
class DrivePlanEntry:
    local_path: Path
    relative_path: str
    remote_path: str
    remote_file_id: str
    size_bytes: int
    mtime_ns: int
    quick_hash: str
    action: str
    reason: str


@dataclass
class RemoteDriveItem:
    file_id: str
    name: str
    relative_path: str
    remote_path: str
    mime_type: str
    size_bytes: int
    md5_checksum: str
    modified_time: str
    web_view_link: str


@dataclass
class DriveDownloadEntry:
    remote_file_id: str
    local_path: Path
    relative_path: str
    remote_path: str
    size_bytes: int
    md5_checksum: str
    action: str
    reason: str


@dataclass
class DriveSyncStats:
    scanned: int = 0
    planned: int = 0
    skipped: int = 0
    uploaded: int = 0
    downloaded: int = 0
    conflicts: int = 0
    failed: int = 0


def default_credentials_path() -> Path:
    return user_config_dir() / DEFAULT_CREDENTIALS_NAME


def default_token_path() -> Path:
    return user_config_dir() / DEFAULT_TOKEN_NAME


def desktop_credentials_payload(client_id: str, client_secret: str, *, project_id: str = "") -> dict:
    client_id = client_id.strip()
    client_secret = client_secret.strip()
    project_id = project_id.strip() or "photo-manager-pro"
    if not client_id:
        raise ValueError("Google OAuth client ID is required.")
    if not client_secret:
        raise ValueError("Google OAuth client secret is required.")
    return {
        "installed": {
            "client_id": client_id,
            "project_id": project_id,
            "auth_uri": GOOGLE_AUTH_URI,
            "token_uri": GOOGLE_TOKEN_URI,
            "auth_provider_x509_cert_url": GOOGLE_CERT_URL,
            "client_secret": client_secret,
            "redirect_uris": ["http://localhost"],
        }
    }


def write_desktop_credentials_file(
    path: Path,
    client_id: str,
    client_secret: str,
    *,
    project_id: str = "",
) -> Path:
    path = path.expanduser().resolve()
    payload = desktop_credentials_payload(client_id, client_secret, project_id=project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def utc_now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


def _log(log: Optional[Logger], message: str) -> None:
    if log is not None:
        log(message)


def ensure_cloud_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS cloud_sync_state (
            provider TEXT NOT NULL,
            local_path TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            remote_path TEXT NOT NULL,
            remote_file_id TEXT,
            size_bytes INTEGER NOT NULL DEFAULT 0,
            mtime_ns INTEGER NOT NULL DEFAULT 0,
            quick_hash TEXT,
            status TEXT NOT NULL,
            uploaded_at TEXT NOT NULL,
            details_json TEXT,
            PRIMARY KEY (provider, local_path)
        );

        CREATE INDEX IF NOT EXISTS idx_cloud_sync_state_provider_status
            ON cloud_sync_state(provider, status);
        CREATE INDEX IF NOT EXISTS idx_cloud_sync_state_relative_path
            ON cloud_sync_state(relative_path);
        """
    )
    conn.commit()


def get_cloud_state(conn: sqlite3.Connection, provider: str = PROVIDER) -> dict[str, sqlite3.Row]:
    ensure_cloud_schema(conn)
    rows = conn.execute(
        """
        SELECT local_path, relative_path, remote_path, remote_file_id, size_bytes, mtime_ns, quick_hash, status
        FROM cloud_sync_state
        WHERE provider = ?
        """,
        (provider,),
    ).fetchall()
    return {str(row["local_path"]): row for row in rows}


def record_cloud_upload(
    conn: sqlite3.Connection,
    entry: DrivePlanEntry,
    *,
    remote_file_id: str,
    details: Optional[dict] = None,
    provider: str = PROVIDER,
) -> None:
    ensure_cloud_schema(conn)
    conn.execute(
        """
        INSERT INTO cloud_sync_state (
            provider, local_path, relative_path, remote_path, remote_file_id,
            size_bytes, mtime_ns, quick_hash, status, uploaded_at, details_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(provider, local_path) DO UPDATE SET
            relative_path=excluded.relative_path,
            remote_path=excluded.remote_path,
            remote_file_id=excluded.remote_file_id,
            size_bytes=excluded.size_bytes,
            mtime_ns=excluded.mtime_ns,
            quick_hash=excluded.quick_hash,
            status=excluded.status,
            uploaded_at=excluded.uploaded_at,
            details_json=excluded.details_json
        """,
        (
            provider,
            normalize_path(entry.local_path),
            entry.relative_path,
            entry.remote_path,
            remote_file_id,
            entry.size_bytes,
            entry.mtime_ns,
            entry.quick_hash,
            "uploaded",
            utc_now(),
            json.dumps(details or {}, sort_keys=True),
        ),
    )


def record_cloud_download(
    conn: sqlite3.Connection,
    entry: DriveDownloadEntry,
    *,
    mtime_ns: int,
    details: Optional[dict] = None,
    provider: str = PROVIDER,
) -> None:
    ensure_cloud_schema(conn)
    conn.execute(
        """
        INSERT INTO cloud_sync_state (
            provider, local_path, relative_path, remote_path, remote_file_id,
            size_bytes, mtime_ns, quick_hash, status, uploaded_at, details_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(provider, local_path) DO UPDATE SET
            relative_path=excluded.relative_path,
            remote_path=excluded.remote_path,
            remote_file_id=excluded.remote_file_id,
            size_bytes=excluded.size_bytes,
            mtime_ns=excluded.mtime_ns,
            quick_hash=excluded.quick_hash,
            status=excluded.status,
            uploaded_at=excluded.uploaded_at,
            details_json=excluded.details_json
        """,
        (
            provider,
            normalize_path(entry.local_path),
            entry.relative_path,
            entry.remote_path,
            entry.remote_file_id,
            entry.size_bytes,
            mtime_ns,
            "",
            "downloaded",
            utc_now(),
            json.dumps(details or {}, sort_keys=True),
        ),
    )


def iter_upload_candidates(root: Path, *, recursive: bool = True, include_nonmedia: bool = False) -> Iterable[Path]:
    root = root.expanduser().resolve()
    if recursive:
        yield from iter_library_files(root, include_nonmedia=include_nonmedia)
        return

    for path in root.iterdir():
        if is_media_file(path, include_nonmedia=include_nonmedia):
            yield path


def _state_matches(row: sqlite3.Row, *, size_bytes: int, mtime_ns: int, quick_hash_value: str) -> bool:
    if int(row["size_bytes"]) != size_bytes:
        return False
    if int(row["mtime_ns"]) != mtime_ns:
        return False
    recorded_hash = str(row["quick_hash"] or "")
    if recorded_hash and quick_hash_value and recorded_hash != quick_hash_value:
        return False
    return str(row["status"]) in {"uploaded", "downloaded", "synced"}


def build_upload_plan(
    root: Path,
    *,
    remote_root_name: str = DEFAULT_REMOTE_ROOT_NAME,
    recursive: bool = True,
    include_nonmedia: bool = False,
    compute_hash: bool = False,
    limit: int = 0,
    state: Optional[dict[str, sqlite3.Row]] = None,
) -> tuple[list[DrivePlanEntry], DriveSyncStats]:
    root = root.expanduser().resolve()
    plan: list[DrivePlanEntry] = []
    stats = DriveSyncStats()
    state = state or {}

    for path in iter_upload_candidates(root, recursive=recursive, include_nonmedia=include_nonmedia):
        stats.scanned += 1
        resolved = path.resolve()
        stat = resolved.stat()
        relative = relative_to_root(root, resolved).replace("\\", "/")
        quick_hash_value = quick_fingerprint(resolved) if compute_hash else ""
        state_row = state.get(normalize_path(resolved))
        remote_path = f"{remote_root_name.rstrip('/')}/{relative}"

        remote_file_id = str(state_row["remote_file_id"] or "") if state_row is not None else ""

        if state_row is not None and _state_matches(
            state_row,
            size_bytes=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            quick_hash_value=quick_hash_value,
        ):
            stats.skipped += 1
            action = "skip"
            reason = "unchanged"
        else:
            stats.planned += 1
            action = "update" if remote_file_id else "upload"
            reason = "changed" if remote_file_id else "new"

        plan.append(
            DrivePlanEntry(
                local_path=resolved,
                relative_path=relative,
                remote_path=remote_path,
                remote_file_id=remote_file_id,
                size_bytes=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                quick_hash=quick_hash_value,
                action=action,
                reason=reason,
            )
        )

        if limit > 0 and len(plan) >= limit:
            break

    return plan, stats


def write_plan_csv(plan: Iterable[DrivePlanEntry], out_path: Path) -> Path:
    out_path = out_path.expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "action",
                "reason",
                "local_path",
                "relative_path",
                "remote_path",
                "remote_file_id",
                "size_bytes",
                "mtime_ns",
                "quick_hash",
            ],
        )
        writer.writeheader()
        for entry in plan:
            writer.writerow(
                {
                    "action": entry.action,
                    "reason": entry.reason,
                    "local_path": str(entry.local_path),
                    "relative_path": entry.relative_path,
                    "remote_path": entry.remote_path,
                    "remote_file_id": entry.remote_file_id,
                    "size_bytes": entry.size_bytes,
                    "mtime_ns": entry.mtime_ns,
                    "quick_hash": entry.quick_hash,
                }
            )
    return out_path


def remote_is_media(relative_path: str, include_nonmedia: bool = False) -> bool:
    if include_nonmedia:
        return True
    return PurePosixPath(relative_path).suffix.lower() in MEDIA_EXTS


def local_md5(path: Path) -> str:
    h = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def resolve_download_target(root: Path, relative_path: str) -> Path:
    root = root.expanduser().resolve()
    parts = PurePosixPath(relative_path).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"Unsafe Google Drive relative path: {relative_path}")
    target = root.joinpath(*parts).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"Google Drive path escapes the local root: {relative_path}")
    return target


def build_download_plan(
    root: Path,
    remote_files: Iterable[RemoteDriveItem],
    *,
    include_nonmedia: bool = False,
    compute_hash: bool = False,
    overwrite: bool = False,
    limit: int = 0,
) -> tuple[list[DriveDownloadEntry], DriveSyncStats]:
    root = root.expanduser().resolve()
    plan: list[DriveDownloadEntry] = []
    stats = DriveSyncStats()

    for item in remote_files:
        if not remote_is_media(item.relative_path, include_nonmedia=include_nonmedia):
            continue

        stats.scanned += 1
        local_path = resolve_download_target(root, item.relative_path)
        action = "download"
        reason = "missing_local"

        if local_path.exists():
            if not local_path.is_file():
                action = "conflict"
                reason = "local_path_not_file"
                stats.conflicts += 1
            else:
                local_size = local_path.stat().st_size
                same_size = item.size_bytes > 0 and local_size == item.size_bytes
                same_hash = False
                if compute_hash and item.md5_checksum:
                    same_hash = local_md5(local_path) == item.md5_checksum

                if same_hash or (same_size and not compute_hash):
                    action = "skip"
                    reason = "unchanged_hash" if same_hash else "unchanged_size_unverified"
                    stats.skipped += 1
                elif overwrite:
                    action = "download"
                    reason = "overwrite_local_changed"
                else:
                    action = "conflict"
                    reason = "local_file_exists_different"
                    stats.conflicts += 1

        if action == "download":
            stats.planned += 1

        plan.append(
            DriveDownloadEntry(
                remote_file_id=item.file_id,
                local_path=local_path,
                relative_path=item.relative_path,
                remote_path=item.remote_path,
                size_bytes=item.size_bytes,
                md5_checksum=item.md5_checksum,
                action=action,
                reason=reason,
            )
        )

        if limit > 0 and len(plan) >= limit:
            break

    return plan, stats


def write_download_plan_csv(plan: Iterable[DriveDownloadEntry], out_path: Path) -> Path:
    out_path = out_path.expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "action",
                "reason",
                "local_path",
                "relative_path",
                "remote_path",
                "remote_file_id",
                "size_bytes",
                "md5_checksum",
            ],
        )
        writer.writeheader()
        for entry in plan:
            writer.writerow(
                {
                    "action": entry.action,
                    "reason": entry.reason,
                    "local_path": str(entry.local_path),
                    "relative_path": entry.relative_path,
                    "remote_path": entry.remote_path,
                    "remote_file_id": entry.remote_file_id,
                    "size_bytes": entry.size_bytes,
                    "md5_checksum": entry.md5_checksum,
                }
            )
    return out_path


def require_google_api():
    try:
        from google.auth.transport.requests import Request  # type: ignore
        from google.oauth2.credentials import Credentials  # type: ignore
        from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore
        from googleapiclient.discovery import build  # type: ignore
        from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload  # type: ignore
    except Exception as exc:
        raise GoogleDriveDependencyError(
            "Google Drive support requires optional dependencies. "
            'Install them with: python -m pip install -e ".[cloud]"'
        ) from exc
    return Request, Credentials, InstalledAppFlow, build, MediaFileUpload, MediaIoBaseDownload


def load_google_credentials(credentials_path: Path, token_path: Path, *, scopes: list[str] = DEFAULT_SCOPES):
    Request, Credentials, InstalledAppFlow, _build, _MediaFileUpload, _MediaIoBaseDownload = require_google_api()

    credentials_path = credentials_path.expanduser().resolve()
    token_path = token_path.expanduser().resolve()
    if not credentials_path.exists():
        raise FileNotFoundError(
            f"Google OAuth desktop credentials were not found: {credentials_path}\n"
            "Create an OAuth client for a desktop app in Google Cloud, download the JSON file, "
            "and save it at this path or pass --credentials."
        )

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), scopes)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), scopes)
            creds = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")

    return creds


def build_drive_service(credentials_path: Path, token_path: Path):
    _Request, _Credentials, _InstalledAppFlow, build, _MediaFileUpload, _MediaIoBaseDownload = require_google_api()
    creds = load_google_credentials(credentials_path, token_path)
    return build("drive", "v3", credentials=creds)


def drive_query_literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


class GoogleDriveClient:
    def __init__(self, service) -> None:
        self.service = service
        self._folder_cache: dict[tuple[Optional[str], str], str] = {}

    def find_folder(self, name: str, parent_id: Optional[str] = None) -> Optional[str]:
        query = [
            f"mimeType = {drive_query_literal(DRIVE_FOLDER_MIME)}",
            f"name = {drive_query_literal(name)}",
            "trashed = false",
        ]
        if parent_id:
            query.append(f"{drive_query_literal(parent_id)} in parents")

        result = (
            self.service.files()
            .list(q=" and ".join(query), fields="files(id, name)", spaces="drive", pageSize=10)
            .execute()
        )
        files = result.get("files", [])
        if not files:
            return None
        return str(files[0]["id"])

    def create_folder(self, name: str, parent_id: Optional[str] = None) -> str:
        body = {"name": name, "mimeType": DRIVE_FOLDER_MIME}
        if parent_id:
            body["parents"] = [parent_id]
        result = self.service.files().create(body=body, fields="id").execute()
        return str(result["id"])

    def ensure_folder(self, name: str, parent_id: Optional[str] = None) -> str:
        key = (parent_id, name)
        cached = self._folder_cache.get(key)
        if cached:
            return cached
        folder_id = self.find_folder(name, parent_id=parent_id)
        if folder_id is None:
            folder_id = self.create_folder(name, parent_id=parent_id)
        self._folder_cache[key] = folder_id
        return folder_id

    def ensure_folder_path(self, folder_parts: Iterable[str], parent_id: Optional[str] = None) -> str:
        current = parent_id
        for part in folder_parts:
            if not part:
                continue
            current = self.ensure_folder(part, parent_id=current)
        if current is None:
            raise RuntimeError("Folder path did not produce a Drive folder ID.")
        return current

    def list_children(self, parent_id: str) -> list[dict]:
        query = [f"{drive_query_literal(parent_id)} in parents", "trashed = false"]
        items: list[dict] = []
        page_token = None
        while True:
            request = self.service.files().list(
                q=" and ".join(query),
                fields=(
                    "nextPageToken, "
                    "files(id, name, mimeType, size, md5Checksum, modifiedTime, webViewLink)"
                ),
                spaces="drive",
                pageSize=1000,
                pageToken=page_token,
            )
            result = request.execute()
            items.extend(result.get("files", []))
            page_token = result.get("nextPageToken")
            if not page_token:
                return items

    def _remote_item_from_api(self, item: dict, relative_path: str, remote_root_name: str) -> Optional[RemoteDriveItem]:
        mime_type = str(item.get("mimeType", ""))
        if mime_type == DRIVE_FOLDER_MIME or mime_type.startswith("application/vnd.google-apps."):
            return None
        return RemoteDriveItem(
            file_id=str(item["id"]),
            name=str(item.get("name", "")),
            relative_path=relative_path,
            remote_path=f"{remote_root_name.rstrip('/')}/{relative_path}",
            mime_type=mime_type,
            size_bytes=int(item.get("size") or 0),
            md5_checksum=str(item.get("md5Checksum") or ""),
            modified_time=str(item.get("modifiedTime") or ""),
            web_view_link=str(item.get("webViewLink") or ""),
        )

    def iter_files_one_level(self, root_folder_id: str, remote_root_name: str) -> Iterable[RemoteDriveItem]:
        for item in self.list_children(root_folder_id):
            name = str(item.get("name", ""))
            if not name:
                continue
            remote_item = self._remote_item_from_api(item, name, remote_root_name)
            if remote_item is not None:
                yield remote_item

    def iter_files_recursive(self, root_folder_id: str, remote_root_name: str) -> Iterable[RemoteDriveItem]:
        stack: list[tuple[str, str]] = [(root_folder_id, "")]
        while stack:
            parent_id, parent_relative = stack.pop()
            for item in self.list_children(parent_id):
                name = str(item.get("name", ""))
                if not name:
                    continue
                relative = f"{parent_relative}/{name}" if parent_relative else name
                mime_type = str(item.get("mimeType", ""))
                if mime_type == DRIVE_FOLDER_MIME:
                    stack.append((str(item["id"]), relative))
                    continue
                remote_item = self._remote_item_from_api(item, relative, remote_root_name)
                if remote_item is not None:
                    yield remote_item

    def upload_file(self, local_path: Path, folder_id: str) -> dict:
        _Request, _Credentials, _InstalledAppFlow, _build, MediaFileUpload, _MediaIoBaseDownload = require_google_api()
        mimetype, _encoding = mimetypes.guess_type(str(local_path))
        media = MediaFileUpload(str(local_path), mimetype=mimetype or "application/octet-stream", resumable=True)
        body = {"name": local_path.name, "parents": [folder_id]}
        return (
            self.service.files()
            .create(
                body=body,
                media_body=media,
                fields="id, name, size, md5Checksum, modifiedTime, webViewLink",
            )
            .execute()
        )

    def update_file(self, file_id: str, local_path: Path) -> dict:
        _Request, _Credentials, _InstalledAppFlow, _build, MediaFileUpload, _MediaIoBaseDownload = require_google_api()
        mimetype, _encoding = mimetypes.guess_type(str(local_path))
        media = MediaFileUpload(str(local_path), mimetype=mimetype or "application/octet-stream", resumable=True)
        body = {"name": local_path.name}
        return (
            self.service.files()
            .update(
                fileId=file_id,
                body=body,
                media_body=media,
                fields="id, name, size, md5Checksum, modifiedTime, webViewLink",
            )
            .execute()
        )

    def download_file(self, file_id: str, local_path: Path) -> None:
        _Request, _Credentials, _InstalledAppFlow, _build, _MediaFileUpload, MediaIoBaseDownload = require_google_api()
        local_path.parent.mkdir(parents=True, exist_ok=True)
        request = self.service.files().get_media(fileId=file_id)
        with local_path.open("wb") as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _status, done = downloader.next_chunk()


def run_drive_upload(
    root: Path,
    *,
    credentials_path: Path = default_credentials_path(),
    token_path: Path = default_token_path(),
    remote_root_name: str = DEFAULT_REMOTE_ROOT_NAME,
    remote_parent_id: Optional[str] = None,
    recursive: bool = True,
    include_nonmedia: bool = False,
    compute_hash: bool = False,
    execute: bool = False,
    limit: int = 0,
    plan_out: Optional[Path] = None,
    log: Optional[Logger] = print,
) -> DriveSyncStats:
    root = root.expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Local root does not exist: {root}")

    with closing(connect(default_index_path(root))) as conn:
        state = get_cloud_state(conn)
        plan, stats = build_upload_plan(
            root,
            remote_root_name=remote_root_name,
            recursive=recursive,
            include_nonmedia=include_nonmedia,
            compute_hash=compute_hash,
            limit=limit,
            state=state,
        )

        if plan_out is not None:
            out = write_plan_csv(plan, plan_out)
            _log(log, f"Google Drive plan written: {out}")

        _log(
            log,
            "Google Drive plan: "
            f"scanned={stats.scanned}, upload={stats.planned}, skipped={stats.skipped}, execute={execute}",
        )

        if not execute:
            _log(log, "Dry-run complete. Nothing was uploaded.")
            return stats

        service = build_drive_service(credentials_path, token_path)
        drive = GoogleDriveClient(service)
        remote_root_id = drive.ensure_folder(remote_root_name, parent_id=remote_parent_id)

        for idx, entry in enumerate(plan, start=1):
            if entry.action not in {"upload", "update"}:
                continue
            try:
                if entry.action == "update" and entry.remote_file_id:
                    result = drive.update_file(entry.remote_file_id, entry.local_path)
                else:
                    parent_parts = entry.relative_path.split("/")[:-1]
                    folder_id = (
                        drive.ensure_folder_path(parent_parts, parent_id=remote_root_id)
                        if parent_parts
                        else remote_root_id
                    )
                    result = drive.upload_file(entry.local_path, folder_id)
                record_cloud_upload(conn, entry, remote_file_id=str(result.get("id", "")), details=result)
                stats.uploaded += 1
                action_label = "updated" if entry.action == "update" else "uploaded"
                _log(log, f"{action_label} {stats.uploaded}/{stats.planned}: {entry.relative_path}")
            except Exception as exc:
                stats.failed += 1
                _log(log, f"{entry.action} failed: {entry.relative_path} ({exc})")

            if idx % 100 == 0:
                conn.commit()

        conn.commit()
        _log(
            log,
            "Google Drive upload finished: "
            f"uploaded={stats.uploaded}, failed={stats.failed}, skipped={stats.skipped}",
        )
        return stats


def run_drive_download(
    root: Path,
    *,
    credentials_path: Path = default_credentials_path(),
    token_path: Path = default_token_path(),
    remote_root_name: str = DEFAULT_REMOTE_ROOT_NAME,
    remote_parent_id: Optional[str] = None,
    recursive: bool = True,
    include_nonmedia: bool = False,
    compute_hash: bool = False,
    overwrite: bool = False,
    execute: bool = False,
    limit: int = 0,
    plan_out: Optional[Path] = None,
    log: Optional[Logger] = print,
) -> DriveSyncStats:
    root = root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)

    service = build_drive_service(credentials_path, token_path)
    drive = GoogleDriveClient(service)
    remote_root_id = drive.find_folder(remote_root_name, parent_id=remote_parent_id)
    if remote_root_id is None:
        _log(log, f"Google Drive folder not found: {remote_root_name}")
        return DriveSyncStats()

    if recursive:
        remote_files = list(drive.iter_files_recursive(remote_root_id, remote_root_name))
    else:
        remote_files = list(drive.iter_files_one_level(remote_root_id, remote_root_name))
    plan, stats = build_download_plan(
        root,
        remote_files,
        include_nonmedia=include_nonmedia,
        compute_hash=compute_hash,
        overwrite=overwrite,
        limit=limit,
    )

    if plan_out is not None:
        out = write_download_plan_csv(plan, plan_out)
        _log(log, f"Google Drive download plan written: {out}")

    _log(
        log,
        "Google Drive download plan: "
        f"scanned={stats.scanned}, download={stats.planned}, "
        f"skipped={stats.skipped}, conflicts={stats.conflicts}, execute={execute}",
    )

    if not execute:
        _log(log, "Dry-run complete. Nothing was downloaded.")
        return stats

    with closing(connect(default_index_path(root))) as conn:
        for idx, entry in enumerate(plan, start=1):
            if entry.action != "download":
                continue
            try:
                drive.download_file(entry.remote_file_id, entry.local_path)
                record_cloud_download(
                    conn,
                    entry,
                    mtime_ns=entry.local_path.stat().st_mtime_ns,
                    details={"remote_file_id": entry.remote_file_id, "md5_checksum": entry.md5_checksum},
                )
                stats.downloaded += 1
                _log(log, f"downloaded {stats.downloaded}/{stats.planned}: {entry.relative_path}")
            except Exception as exc:
                stats.failed += 1
                _log(log, f"download failed: {entry.relative_path} ({exc})")

            if idx % 100 == 0:
                conn.commit()

        conn.commit()

    _log(
        log,
        "Google Drive download finished: "
        f"downloaded={stats.downloaded}, failed={stats.failed}, "
        f"skipped={stats.skipped}, conflicts={stats.conflicts}",
    )
    return stats


def _resolve_root(raw: str) -> Path:
    if raw:
        return Path(raw).expanduser().resolve()

    cfg_path = default_config_path()
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            root_dir = data.get("root_dir")
            if root_dir:
                return Path(str(root_dir)).expanduser().resolve()
        except Exception:
            pass

    raise SystemExit("Pass --root or save a Photo Manager Pro configuration first.")


def _main() -> None:
    parser = argparse.ArgumentParser(description="Google Drive sync backend for Photo Manager Pro.")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default="", help="Local photo library root. Defaults to saved app config root.")
    common.add_argument("--remote-root", default=DEFAULT_REMOTE_ROOT_NAME, help="Google Drive folder name to create/use.")
    common.add_argument("--recursive", action=argparse.BooleanOptionalAction, default=True)
    common.add_argument("--include-nonmedia", action="store_true", help="Include non-media files.")
    common.add_argument("--compute-hash", action="store_true", help="Compute quick hashes for stronger change detection.")
    common.add_argument("--limit", type=int, default=0, help="Limit number of planned local files, mostly for testing.")
    common.add_argument("--plan-out", default="", help="Optional CSV path for the generated plan.")

    auth = sub.add_parser("auth", help="Run OAuth and save a local refresh token.")
    auth.add_argument("--credentials", default=str(default_credentials_path()), help="OAuth desktop client JSON path.")
    auth.add_argument("--token", default=str(default_token_path()), help="Local token JSON path.")

    sub.add_parser("plan", parents=[common], help="Build a local Google Drive upload plan without auth.")

    upload = sub.add_parser("upload", parents=[common], help="Upload new/changed local files to Google Drive.")
    upload.add_argument("--credentials", default=str(default_credentials_path()), help="OAuth desktop client JSON path.")
    upload.add_argument("--token", default=str(default_token_path()), help="Local token JSON path.")
    upload.add_argument("--parent-id", default="", help="Optional Drive parent folder ID for the remote root.")
    upload.add_argument("--execute", action="store_true", help="Actually upload. Without this flag, upload is a dry-run.")

    download_plan = sub.add_parser(
        "download-plan",
        parents=[common],
        help="Build a Google Drive download plan. Requires auth so remote files can be listed.",
    )
    download_plan.add_argument("--credentials", default=str(default_credentials_path()), help="OAuth desktop client JSON path.")
    download_plan.add_argument("--token", default=str(default_token_path()), help="Local token JSON path.")
    download_plan.add_argument("--parent-id", default="", help="Optional Drive parent folder ID for the remote root.")
    download_plan.add_argument("--overwrite", action="store_true", help="Plan overwrites for changed local files.")

    download = sub.add_parser("download", parents=[common], help="Download missing remote files from Google Drive.")
    download.add_argument("--credentials", default=str(default_credentials_path()), help="OAuth desktop client JSON path.")
    download.add_argument("--token", default=str(default_token_path()), help="Local token JSON path.")
    download.add_argument("--parent-id", default="", help="Optional Drive parent folder ID for the remote root.")
    download.add_argument("--overwrite", action="store_true", help="Overwrite changed local files instead of reporting conflicts.")
    download.add_argument("--execute", action="store_true", help="Actually download. Without this flag, download is a dry-run.")

    args = parser.parse_args()

    if args.command == "auth":
        creds = load_google_credentials(Path(args.credentials), Path(args.token))
        print(f"Google Drive OAuth token saved: {Path(args.token).expanduser().resolve()}")
        print(f"Scopes: {', '.join(getattr(creds, 'scopes', None) or DEFAULT_SCOPES)}")
        return

    root = _resolve_root(args.root)
    plan_out = Path(args.plan_out) if args.plan_out else None

    if args.command == "plan":
        with closing(connect(default_index_path(root))) as conn:
            state = get_cloud_state(conn)
            plan, stats = build_upload_plan(
                root,
                remote_root_name=args.remote_root,
                recursive=args.recursive,
                include_nonmedia=args.include_nonmedia,
                compute_hash=args.compute_hash,
                limit=args.limit,
                state=state,
            )
        if plan_out is not None:
            out = write_plan_csv(plan, plan_out)
            print(f"Google Drive plan written: {out}")
        print(f"Google Drive plan: scanned={stats.scanned}, upload={stats.planned}, skipped={stats.skipped}")
        return

    if args.command == "upload":
        run_drive_upload(
            root,
            credentials_path=Path(args.credentials),
            token_path=Path(args.token),
            remote_root_name=args.remote_root,
            remote_parent_id=args.parent_id or None,
            recursive=args.recursive,
            include_nonmedia=args.include_nonmedia,
            compute_hash=args.compute_hash,
            execute=args.execute,
            limit=args.limit,
            plan_out=plan_out,
            log=print,
        )
        return

    if args.command in {"download-plan", "download"}:
        run_drive_download(
            root,
            credentials_path=Path(args.credentials),
            token_path=Path(args.token),
            remote_root_name=args.remote_root,
            remote_parent_id=args.parent_id or None,
            recursive=args.recursive,
            include_nonmedia=args.include_nonmedia,
            compute_hash=args.compute_hash,
            overwrite=args.overwrite,
            execute=args.command == "download" and args.execute,
            limit=args.limit,
            plan_out=plan_out,
            log=print,
        )
        return

def main() -> None:
    try:
        _main()
    except (FileNotFoundError, GoogleDriveDependencyError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
