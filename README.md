# Photo Manager Pro

A local tool for organizing photos and videos. The app provides a PySide6 GUI, one-shot synchronization, background folder watching, and tools for detecting heavily blurred photos.

## Current Features

- Sort files from a source folder into year-based folders.
- Detect dates from EXIF with fallback to `mtime` or `ctime`.
- Copy files with verification by fast fingerprint or full SHA256.
- Optionally delete source files after successful synchronization.
- Background sync mode powered by `watchdog`.
- CSV synchronization log.
- Blur scanning with OpenCV.
- Automatic background sync after the app starts.
- Option to open the app on Windows startup.

## Running

```powershell
py -m pip install -r requirements.txt
py photo_manager_gui.py
```

`photo_manager_gui.py` is a lightweight launcher. The main application lives in `photo_manager_qt.py`.

## Startup And Background Work

The GUI has two separate startup options:

- `Autostart background on launch` starts folder watching after the app launches.
- `Open on Windows startup` adds an entry to `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`, so the app starts when the current user logs in.

This is not a full Windows service yet. The target design should split out a separate `photo_manager_service.py` process that reads the same `photo_manager_config.json`, runs without a window, and can be installed with `pywin32` or `NSSM`.

## Schedule

The GUI includes a `Sync hours` field. It accepts one or more hour ranges:

- `0-24` means all day.
- `8-18` means synchronization only during working hours.
- `22-7` means an overnight window that crosses midnight.
- `8-12,14-18` means multiple windows in one day.

In background sync mode, files detected outside the allowed window are queued and processed when the window opens again. The next step is a full calendar or weekly schedule view, for example:

- Weekdays.
- Hour ranges when synchronization is allowed.
- Separate windows for sync and AI/blur analysis.
- A "pause until hour X" mode.
- Restricting heavy work to nighttime.

Technically, the current hour field should eventually evolve into JSON with a list of time windows and task types.

## Local Or API AI

AI could add practical features such as:

- Photo tagging: people, documents, food, landscapes, screenshots, animals, cars.
- Captions for search.
- Semantic duplicate and near-duplicate detection.
- Photo quality scoring: sharpness, exposure, closed eyes, motion blur.
- Private or sensitive content detection before automatic moves.
- OCR for screenshots and documents.
- Automatic albums, such as vacations, university, work, or documents.

I would not start with full Torch as a hard application dependency. A better path:

1. Start with an API backend or ONNX Runtime as an optional backend.
2. For local models, use `onnxruntime` with CLIP/SigLIP embeddings for similarity and search.
3. Add `torch` later as an optional `requirements-ai.txt` package only when training or GPU-heavy models become necessary.

Torch is powerful, but heavy. For a desktop app that should remain stable and easy to run, ONNX Runtime or an external API will usually be less painful.

## Suggested Roadmap

- Real service/headless mode.
- Work schedule and pause controls.
- Background task queue with retry.
- SQLite metadata index.
- Duplicate view with thumbnails.
- Thumbnails and a fast gallery in the GUI.
- Search by date, folder, tags, and AI captions.
- Dedicated safe-delete panel with recycle bin and decision history.
- Synchronization report export.
- Optional AI panel: backend, model, batch size, GPU/CPU, embeddings cache.
