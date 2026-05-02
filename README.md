# Photo Manager Pro

Photo Manager Pro is a local desktop tool for organizing photos and videos. It has a PySide6 GUI, one-shot sync, background folder watching, blur detection, a local SQLite index, duplicate review, and safe cleanup tools.

The project is currently alpha. The main workflow works, but distribution still needs clean-machine testing, more service-mode hardening, and better automated GUI checks.

## What Works

- Sort files from a source folder into year-based folders.
- Detect dates from EXIF, filename patterns like `20210924_132556.jpg`, then `mtime` or `ctime`.
- Copy files with verification by fast fingerprint or full SHA256.
- Optionally delete source files after successful synchronization.
- Background sync mode powered by `watchdog`.
- CSV synchronization log.
- Blur scanning with OpenCV.
- Local SQLite metadata index for files, sync events, blur scores, and AI metadata.
- Automatic background sync after app launch.
- Optional app startup on Windows login.
- Single-instance startup guard, so reopening the app focuses the existing process instead of creating another tray icon.
- Headless runner and Windows Service commands.
- Dashboard with library totals, year counts, top folders, sync errors, and recent events.
- Thumbnail gallery with cached thumbnails, filters, preview, and text/tag search.
- Duplicate review tab that scans by content hash, supports scan cancellation, and queues removals for review.
- Safe Delete Queue with cancel, CSV export, and recycle-bin deletion.
- Local Light AI pass for rough tags, captions, optional OCR, and search.

## Running From Source

```powershell
py -m pip install -r requirements.txt
py photo_manager_gui.py
```

`photo_manager_gui.py` is just the launcher. The main GUI lives in `photo_manager_qt.py`.

After the first PyPI release, installation should look like this:

```powershell
python -m pip install photosync-tool
photo-manager-pro
```

## Configuration

User settings are stored outside the repository/application folder.

On Windows, the default config path is:

```text
%APPDATA%\PhotoManagerPro\photo_manager_config.json
```

The headless service uses the same default config path, and writes its log to:

```text
%APPDATA%\PhotoManagerPro\photo_manager_service.log
```

If an old `photo_manager_config.json` exists next to the source files, the GUI can still read it as a legacy fallback. New saves go to the AppData location.

## Windows EXE

The Windows distribution target is `PhotoManagerPro.exe`. The GitHub release workflow builds it with PyInstaller, uploads it as an artifact, and attaches it to GitHub Releases.

Local build:

```powershell
py -m pip install --upgrade ".[exe]"
pyinstaller --noconfirm --onefile --windowed --name PhotoManagerPro --icon photosync_tool_assets/photo_manager_icon.ico --add-data "photosync_tool_assets;photosync_tool_assets" photo_manager_gui.py
```

The executable is written to `dist/PhotoManagerPro.exe`. `build/` and `dist/` are build outputs and should not be committed.

## Release Build Script

The Bash release script builds the local release set:

- Python source/wheel distributions for PyPI in `dist/`,
- `dist/PhotoManagerPro.exe`,
- `release/PhotoManagerProSetup-<version>.exe` when Inno Setup `iscc` is available.

```bash
PYTHON=py scripts/build_release.sh
```

Run it from Git Bash, WSL, or another Bash environment.

Publishing to PyPI is opt-in:

```bash
PYTHON=py scripts/build_release.sh --upload-pypi
```

## Windows Installer

An Inno Setup script is available at `installer/PhotoManagerPro.iss`. It packages the built executable into a regular Windows installer.

Build order:

```powershell
py -m pip install --upgrade ".[exe]"
pyinstaller --noconfirm --onefile --windowed --name PhotoManagerPro --icon photosync_tool_assets/photo_manager_icon.ico --add-data "photosync_tool_assets;photosync_tool_assets" photo_manager_gui.py
iscc installer\PhotoManagerPro.iss
```

The installer output is written to `release/`.

## Tests

```powershell
py -m pip install -e ".[dev]"
py -m pytest
```

The current tests cover date parsing, copy verification, safe destination naming, media filtering, and sync schedule logic.

## Screenshots

Screenshots are not committed yet. The capture plan and recommended filenames are documented in `docs/SCREENSHOTS.md`.

## GitHub Pages

The static GitHub Pages site lives in `docs/`. The page is deployed by `.github/workflows/pages.yml` and expects screenshots in `docs/screenshots/`.

Start with:

- `docs/screenshots/dashboard.png`
- `docs/screenshots/gallery.png`
- `docs/screenshots/duplicates.png`

## Library Index

The app keeps a local SQLite index named `photo_manager_index.sqlite3` inside the selected photo root. The index is local and disposable: it can be rebuilt from the library, sync logs, and blur CSVs.

The index stores:

- media file paths, sizes, timestamps, years, dimensions, status, and optional quick hashes,
- sync events from batch sync, background sync, and the headless service,
- blur scores imported from `blur_tool.py`,
- captions, tags, OCR text, and future AI embedding data.

In the GUI, `Library Index -> Rebuild Index` scans the current root folder. `Import Blur CSV` imports existing blur scan results. Batch sync, background sync, Windows service, blur scan, and blur auto-delete all update the index.

Dashboard, Gallery, Duplicates, Delete Queue, and Light AI use this same index. After changing the root folder or importing a large existing library, rebuild the index first.

## Gallery, Duplicates, And Delete Queue

Gallery thumbnails are cached in `.photo_manager_cache/thumbnails` inside the selected root. The cache is ignored by the indexer and can be deleted safely; it will be rebuilt as needed.

Duplicate Review does not delete files directly. It adds candidates to the Safe Delete Queue. From there, decisions can be cancelled, exported to CSV, or moved to the system recycle bin.

## Light AI

Light AI is currently a local heuristic backend, not a heavy model. It can tag likely screenshots, documents, food, landscapes, people, and low-quality photos. OCR is optional.

Optional OCR install:

```powershell
python -m pip install "photosync-tool[ai]"
```

OCR also needs a local Tesseract installation.

## Startup And Background Work

The GUI has two startup options:

- `Autostart background on launch` starts folder watching after the app launches.
- `Open on Windows startup` adds an entry to `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`, so the app starts when the current user logs in.

There is also a headless entry point in `photo_manager_service.py`. It can run a one-shot sync, run as a foreground watcher, or expose Windows Service commands through `pywin32`:

```powershell
photo-manager-service once
photo-manager-service run
photo-manager-service install
photo-manager-service start
photo-manager-service stop
```

Service mode is implemented, but still needs real-world install/start/stop testing on a clean machine and better setup around logs, permissions, and uninstall behavior.

## Schedule

The GUI has a `Sync hours` field and a simple weekly schedule editor. The hour field accepts one or more ranges:

- `0-24` means all day.
- `8-18` means sync only during that time window.
- `22-7` means an overnight window crossing midnight.
- `8-12,14-18` means multiple windows in one day.

In background sync mode, files detected outside the allowed window are queued and processed when the window opens again.

Still open:

- separate schedules for sync, blur analysis, and AI metadata,
- a `pause until` mode,
- restricting heavier work to nighttime,
- a more durable retry queue for background tasks.

## AI Direction

Full Torch should not be a hard dependency for the desktop app. The better path is to keep heavier AI optional:

1. Start with an API backend or ONNX Runtime as an optional backend.
2. Use SQLite as the cache for tags, captions, embeddings, and review decisions.
3. For local models, use `onnxruntime` with CLIP/SigLIP embeddings for similarity and search.
4. Add `torch` later as a separate `requirements-ai.txt` package only when training or GPU-heavy models are needed.

Torch is powerful, but heavy. For a desktop tool that should stay easy to run, ONNX Runtime or an API backend is the more practical default.

## Still Open

- Test and harden Windows Service install/start/stop on a clean Windows machine.
- Test and polish the Inno Setup installer flow.
- Add a signed release flow for `PhotoManagerPro.exe` and the installer.
- Add automated GUI smoke tests.
- Add a small fixture library for sync/index/gallery tests.
- Add synchronization report export from the GUI.
- Add pause controls and separate schedules for sync, blur, and AI work.
- Add an optional hard-AI panel: ONNX/SigLIP or CLIP embeddings, backend, model, batch size, GPU/CPU, and embedding cache.

## License

This project is licensed under the MIT License.
