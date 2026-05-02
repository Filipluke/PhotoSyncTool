# Changelog

## Unreleased

- Added MIT license metadata and `LICENSE`.
- Removed tracked Python bytecode cache files from the repository.
- Added pytest coverage for date parsing, copy verification, media filtering, and schedule logic.
- Added CI test execution with pytest.
- Added Inno Setup installer script for Windows installer builds.
- Expanded release documentation with exe, installer, Windows Service, screenshots, and signing notes.
- Moved the default GUI/service config location to the per-user AppData config directory.
- Added a single-instance guard so reopening the app activates the existing instance instead of creating extra tray icons.

## 0.1.0 - 2026-04-30

- Initial PyPI packaging foundation.
- PySide6 desktop GUI for local photo sync and blur review workflows.
- Headless/background sync support.
- SQLite library index for file metadata, sync events, blur scores, and future AI metadata.
- Added desktop app icon asset and packaged GUI resources.
- Added dashboard, thumbnail gallery, duplicate review, safe delete queue, and local Light AI metadata/search.
- Added Windows executable GitHub Actions workflow using PyInstaller.
