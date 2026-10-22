# Changelog

## Unreleased

- Allow Windows SHA256 verification to share an in-flight rename handle; preserve read permissions and fail on exclusive locks.

- Serialize SQLite schema migrations with write transactions and retry only transient WAL initialization contention within a bounded deadline; close failed connections.
- Cover concurrent imports across eight processes, concurrent feature migrations, bounded lock failure and atomic schema rollback.
- Introduce the offline IBM Plex archive design, compact library overview, grouped navigation, larger gallery and duplicate inspectors, readable settings and operation status.
- Align the website with the desktop and label screenshots as an unreleased development preview.
- Validate critical site assets and screenshot provenance, packaged fonts/icons, installed entry points and immutable PyPI artifact hashes.

## 0.2.1 - 2026-10-21

- Prevented import name collisions from overwriting different photos, including concurrent imports.
- Recognize already-present matching files on retry using full SHA256 verification.
- Copy through temporary files and store durable verification receipts in the existing SQLite index.
- Fixed source deletion after failed or partially successful watcher imports in the GUI and background service.
- Made import dry-runs leave the library unchanged and recheck originals before cleanup.
- Unified tag-driven tests, Windows executable builds, PyPI Trusted Publishing, and GitHub release assets.
- Redesigned the project website around real app screenshots, platform downloads, and clear alpha status.

## 0.2.0 - 2026-05-26

- Rebuilt the PySide6 desktop shell as a modern dark dashboard with a left sidebar, clearer top actions, status styling, metric cards, dashboard empty states, and local-first footer copy.
- Added Phone Connect for same-LAN phone-to-desktop media uploads with pairing codes, a desktop receiver tab, inbox sorting, and Android companion app source files.
- Refreshed GitHub Pages around the new dashboard, Phone Connect, cleanup, cloud planning, and local-first workflow.
- Updated release metadata for the 0.2.0 Windows executable and package build.

## 0.1.6 - 2026-05-12

- Added a Settings shortcut that detects mounted Google Drive folders and uses them as local sync targets.

## 0.1.5 - 2026-05-12

- Improved the Cloud Sync setup UI so users can paste Google OAuth Client ID and Client secret directly, then let the app save the credentials JSON locally.
- Tightened the top workspace layout so the tab bar sits higher and closer to the main dashboard content.

## 0.1.4 - 2026-05-12

- Added an optional Google Drive backend with desktop OAuth, dry-run upload/download planning, app-managed Drive folder creation, resumable uploads/updates, safe missing-file downloads, local SQLite cloud state, and the `photo-manager-drive` CLI entry point.
- Added a Cloud Sync GUI tab for Google OAuth setup and guarded upload/download actions.

## 0.1.3 - 2026-05-10

- Refreshed the desktop GUI with a dark futuristic product surface, command bar, dashboard-first workspace, dedicated Settings and Diagnostics tabs, summary metric cards, and safer cleanup action hierarchy.
- Updated the GitHub Pages visual system to match the dark desktop UI.
- Added repeatable sanitized screenshot capture automation for the GitHub Pages dashboard, gallery, and duplicate review images.
- Replaced the public GitHub Pages screenshots with current desktop views captured from a disposable demo library.
- Added Linux systemd user-service support for headless background synchronization.
- Updated GUI service controls to describe Windows Services and Linux systemd service modes.
- Expanded documentation and the GitHub Pages site for Windows/Linux usage.
- Added Google Drive synchronization to the public roadmap as a future optional cloud backend.

## 0.1.2 - 2026-05-10

- Added dashboard sync report export for indexed synchronization events.
- Hardened CI to install the project from scratch on Python 3.10 and 3.13 before running tests and package checks.
- Kept Windows installer and source-run GUI fallback versions aligned with the package version.
- Standardized remaining blur-tool prompts and comments to English for public repository polish.
- Added GUI smoke coverage for the main PySide6 workspace.
- Added a reusable test fixture library for sample photo library scenarios.
- Added release version consistency coverage.
- Added lightweight Ruff linting to CI and PyPI publish checks.
- Added public contribution and privacy/safety documentation.
- Added contributor guidelines, public roadmap, and GitHub issue templates.
- Installed Qt runtime libraries in the PyPI publish workflow before GUI smoke tests.
- Expanded README project metadata, layout, and development workflow sections.
- Narrowed PyPI metadata checks to Python distributions so local executable artifacts do not break `twine check`.
- Added ignore patterns for local notes, editor settings, and scratch files.

## 0.1.1 - 2026-05-03

- Added MIT license metadata and `LICENSE`.
- Removed tracked Python bytecode cache files from the repository.
- Added pytest coverage for date parsing, copy verification, media filtering, and schedule logic.
- Added CI test execution with pytest.
- Added Inno Setup installer script for Windows installer builds.
- Expanded release documentation with exe, installer, Windows Service, screenshots, and signing notes.
- Moved the default GUI/service config location to the per-user AppData config directory.
- Added a single-instance guard so reopening the app activates the existing instance instead of creating extra tray icons.
- Added a Bash release build script for PyPI distributions, Windows executable builds, and optional installer/PyPI upload steps.
- Added a GitHub Pages starter site with screenshot placeholders and a Pages deployment workflow.
- Added cancellation support for duplicate scans in the GUI.
- Fixed gallery preview orientation for photos with EXIF rotation metadata.
- Removed old standalone duplicate-cleanup scripts from the packaged project.

## 0.1.0 - 2026-04-30

- Initial PyPI packaging foundation.
- PySide6 desktop GUI for local photo sync and blur review workflows.
- Headless/background sync support.
- SQLite library index for file metadata, sync events, blur scores, and future AI metadata.
- Added desktop app icon asset and packaged GUI resources.
- Added dashboard, thumbnail gallery, duplicate review, safe delete queue, and local Light AI metadata/search.
- Added Windows executable GitHub Actions workflow using PyInstaller.
