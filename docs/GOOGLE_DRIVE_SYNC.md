# Google Drive Sync Direction

Google Drive synchronization is planned as an optional cloud backend. The default app should remain local-first: installing Photo Manager Pro should not create cloud credentials, upload files, or require a Google account.

## Goals

- Sync selected photo-library folders to Google Drive.
- Run uploads/downloads in the same background service model as local sync.
- Keep an auditable dry-run plan before transferring files.
- Store cloud sync state in SQLite so retries, conflicts, and reports are durable.

## Suggested Module Shape

```text
photo_manager_cloud.py          Provider-neutral sync jobs, queue, state, and conflict model
photo_manager_google_drive.py   Google Drive API implementation and OAuth flow
photo_manager_reports.py        Human-readable cloud sync reports
```

## Google Drive Behavior

The Google Drive backend should support:

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
