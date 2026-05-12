# Google Drive Sync

Google Drive synchronization is an optional cloud backend. The default app remains local-first: installing Photo Manager Pro does not create cloud credentials, upload files, or require a Google account.

Current status: alpha CLI backend. It can build dry-run upload/download plans, run OAuth for a desktop app, create/use a Google Drive folder, upload new local files, update changed files that were previously uploaded, download missing remote files, and store local cloud state in the library SQLite index. It does not mirror deletes or resolve conflicts automatically, and it is not wired into the GUI yet.

## Goals

- Sync selected photo-library folders to Google Drive.
- Run uploads/downloads in the same background service model as local sync.
- Keep an auditable dry-run plan before transferring files.
- Store cloud sync state in SQLite so retries, conflicts, and reports are durable.

## Setup

Install the optional cloud dependencies:

```powershell
python -m pip install -e ".[cloud]"
```

Create a Google Cloud OAuth client for a desktop app. In the GUI, paste the Client ID and Client secret into the `Cloud Sync` tab and click `Save OAuth JSON`; the app writes the JSON file for you. You can also download the client JSON from Google Cloud and store it outside the repository. The default path is:

```text
%APPDATA%\PhotoManagerPro\google_drive_credentials.json
~/.config/PhotoManagerPro/google_drive_credentials.json
```

Run OAuth once:

```powershell
photo-manager-drive auth --credentials "%APPDATA%\PhotoManagerPro\google_drive_credentials.json"
```

The refresh token is saved next to the app config as `google_drive_token.json`.

The GUI has a `Cloud Sync` tab with the same setup fields and actions. Use it to paste Client ID/secret or choose an OAuth client JSON, authenticate, build upload/download plans, and run transfers. The app uses OAuth for user Drive access; a plain API key is not enough for private photo libraries.

## Upload Flow

Build a reviewable dry-run plan:

```powershell
photo-manager-drive plan --root "D:\Photos" --plan-out "D:\Photos\google-drive-plan.csv"
```

Run the upload only after reviewing the plan:

```powershell
photo-manager-drive upload --root "D:\Photos" --remote-root "Photo Manager Pro" --execute
```

Without `--execute`, `upload` is also a dry-run. The backend uses the non-sensitive `https://www.googleapis.com/auth/drive.file` scope and creates or updates files under the app-managed remote root folder.

## Download Flow

Build a reviewable download plan from the app-managed Drive folder:

```powershell
photo-manager-drive download-plan --root "D:\Photos" --plan-out "D:\Photos\google-drive-download-plan.csv"
```

Download missing remote files only after reviewing the plan:

```powershell
photo-manager-drive download --root "D:\Photos" --execute
```

If a local file already exists and appears different, the plan marks it as `conflict` and skips it. Passing `--overwrite` changes those entries into downloads, but this should only be used after reviewing the CSV plan and confirming backups.

Optional flags:

- `--include-nonmedia` includes non-media files.
- `--compute-hash` stores quick fingerprints for uploads and compares Drive MD5 hashes for downloads when available.
- `--limit N` limits local plan size for testing.
- `--parent-id DRIVE_FOLDER_ID` creates the remote root under an existing Drive folder that the app can access.

## Suggested Module Shape

```text
photo_manager_google_drive.py   Current upload planner, OAuth flow, Drive client, and state cache
photo_manager_cloud.py          Future provider-neutral sync jobs, queue, state, and conflict model
photo_manager_reports.py        Future human-readable cloud sync reports
```

## Google Drive Behavior

The finished Google Drive backend should support:

- OAuth authorization with explicit user consent,
- minimal scopes where practical,
- resumable uploads for large photo/video files,
- retry/backoff handling,
- bandwidth limits or scheduled transfer windows,
- local-to-drive and drive-to-local conflict previews,
- deletion safety that does not automatically mirror destructive local actions,
- SQLite cache of Drive file IDs, checksums, modified times, and sync status.

## Safety Rules

- Cloud sync should be opt-in and disabled by default.
- First run should show a dry-run plan.
- Upload, download, overwrite, and delete decisions should be visible in reports.
- Local duplicate cleanup and cloud deletion should stay separate until the user explicitly connects them.
- Credentials and tokens should live in the per-user app config/cache area, not in the repository.

The current implementation follows those rules for upload/download sync. Delete mirroring is intentionally not implemented.
