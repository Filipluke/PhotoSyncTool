# Roadmap

Photo Manager Pro is an alpha desktop app. The near-term goal is to make the current workflows reliable on real Windows machines before adding heavier AI or cloud features.

## 0.2 - Release Hardening

- Test the Windows executable on a clean Windows machine or VM.
- Test the Inno Setup installer flow end to end.
- Harden Windows Service install/start/stop/uninstall behavior.
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

## Future Packaging

- Consider migrating flat modules into a `src/photosync_tool/` package after the next stable release.
- Keep compatibility entry points for `photo-manager-pro`, `photo-manager-service`, `photo-manager-index`, `photo-blur-tool`, and `photo-sorter`.
- Split GUI code into smaller modules once the test harness can protect behavior.
