# Roadmap

Photo Manager Pro is an alpha desktop app. The near-term goal is to make the current workflows reliable on real Windows and Linux machines before adding heavier AI or cloud features.

## 0.2 - Release Hardening

- Test the Windows executable on a clean Windows machine or VM.
- Test Linux PyPI/source installs on a clean Linux desktop or VM.
- Test the Inno Setup installer flow end to end.
- Harden Windows Service install/start/stop/uninstall behavior.
- Harden Linux systemd user-service install/start/stop/uninstall behavior.
- Add automated GUI smoke tests for startup, settings save, sync, index rebuild, and gallery load.
- Add a small fixture media library for repeatable integration tests.
- Add a signed release plan for the executable and installer.

## 0.3 - Library Maintenance

- Add a synchronization report export from the GUI.
- Add pause controls and separate schedules for sync, blur analysis, and AI metadata.
- Add an integrity audit for missing, moved, duplicated, and changed files.
- Add repair previews for safe rename, move, and index cleanup operations.
- Add smarter conflict handling when two files map to the same destination.

## 0.4 - Discovery And Review

- Add smart albums based on year, folder, file type, tags, blur score, and review state.
- Add EXIF/GPS browsing and map-friendly exports.
- Add stronger video metadata support and video thumbnails.
- Add perceptual duplicate detection for visually similar but not byte-identical files.
- Add album/export workflows for sharing resized copies or static HTML galleries.

## 0.5 - Optional AI

- Keep the default app lightweight and local.
- Add optional ONNX Runtime embeddings for semantic image search.
- Cache tags, captions, OCR text, and embeddings in SQLite.
- Add a backend selector for local heuristic, ONNX, or API-powered metadata.
- Keep Torch/GPU dependencies outside the default install.

## 0.6 - Optional Cloud Sync

- Add a Google Drive backend as an explicit optional feature, not part of the local default workflow.
- Use OAuth with user-selected scopes and store tokens in the per-user config/cache area.
- Add a dry-run upload/download plan before transferring files.
- Support resumable uploads, retry queues, bandwidth limits, and conflict handling.
- Cache Drive file IDs, hashes, modified times, and sync state in SQLite.
- Keep local file organization, duplicate review, and delete safety rules separate from cloud deletion.

## Future Packaging

- Consider migrating flat modules into a `src/photosync_tool/` package after the next stable release.
- Keep compatibility entry points for `photo-manager-pro`, `photo-manager-service`, `photo-manager-index`, `photo-blur-tool`, and `photo-sorter`.
- Split GUI code into smaller modules once the test harness can protect behavior.
- Consider `photo_manager_cloud.py` and provider-specific modules such as `photo_manager_google_drive.py` once the local service layer is stable.
