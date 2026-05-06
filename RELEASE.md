# Release Guide And Notes

This project is prepared for PyPI publishing through GitHub Actions and PyPI Trusted Publishing.

Release notes belong in two places:

- `CHANGELOG.md` for the versioned history in the repository.
- The GitHub Release body for the user-facing release summary.

This file is the operational checklist for building, testing, and publishing a release.

## PyPI Pending Publisher

On PyPI, go to `Account settings -> Publishing -> Add a new pending publisher -> GitHub` and use:

- PyPI Project Name: `photosync-tool`
- Owner: `Filipluke`
- Repository name: `PhotoSyncTool`
- Workflow name: `publish.yml`
- Environment name: `pypi`

The project name is the value from `pyproject.toml` and must match exactly. Pending publishers do not reserve names, so the first successful upload creates the project.

## GitHub Environment

In GitHub, create an environment named `pypi`:

1. Open `Settings -> Environments`.
2. Create `pypi`.
3. Optionally add required reviewers before deployment.

## Local Verification

```powershell
python -m pip install -e ".[dev]"
python -m pytest
python -m build
python -m twine check dist/*
```

## Local Release Script

For a full local release build from Git Bash:

```bash
PYTHON=py scripts/build_release.sh
```

The script requires Bash, for example Git Bash or WSL on Windows.

The script builds PyPI distributions, runs `twine check`, builds `dist/PhotoManagerPro.exe`, and builds the Inno Setup installer when `iscc` is available in `PATH`.

PyPI upload is not automatic. To upload after the checks pass:

```bash
PYTHON=py scripts/build_release.sh --upload-pypi
```

## Windows EXE Verification

Build the executable:

```powershell
python -m pip install --upgrade ".[exe]"
pyinstaller --noconfirm --onefile --windowed --name PhotoManagerPro --icon photosync_tool_assets/photo_manager_icon.ico --add-data "photosync_tool_assets;photosync_tool_assets" photo_manager_gui.py
```

Smoke test on the development machine:

```powershell
.\dist\PhotoManagerPro.exe
```

Manual checks:

1. The app starts without a console error.
2. The icon appears in the window/taskbar.
3. Settings can be saved to `%APPDATA%\PhotoManagerPro\photo_manager_config.json`.
4. A small demo source folder can be synchronized into a demo root folder.
5. `Library Index -> Rebuild Index` works on the demo root.
6. Dashboard and Gallery load after indexing.
7. Duplicate scan and Safe Delete Queue work on copied demo files.
8. Closing and reopening the app does not lose settings.

Clean-machine test:

1. Copy only `dist\PhotoManagerPro.exe` to a Windows machine or VM without the repo.
2. Run the executable.
3. Repeat the manual checks above using a small disposable demo folder.
4. Record any missing DLL, antivirus, permission, startup, or settings persistence issues.

## Windows Service Verification

Service mode should be tested on a clean Windows machine or VM because it depends on Windows permissions and `pywin32`.

The default config path is `%APPDATA%\PhotoManagerPro\photo_manager_config.json`. Create and save settings from the GUI first, or pass an explicit config path when running foreground/one-shot checks:

```powershell
photo-manager-service once --config "$env:APPDATA\PhotoManagerPro\photo_manager_config.json"
photo-manager-service run --config "$env:APPDATA\PhotoManagerPro\photo_manager_config.json"
```

From an elevated PowerShell:

```powershell
python -m pip install photosync-tool
photo-manager-service once
photo-manager-service install
photo-manager-service start
Get-Service PhotoManagerProService
photo-manager-service stop
photo-manager-service uninstall
```

Manual checks:

1. `once` runs a single sync and writes `photo_manager_service.log`.
2. `install` creates a Windows service named `PhotoManagerProService`.
3. `start` changes the service to running.
4. File changes in the configured source folder are picked up.
5. `stop` stops folder watching.
6. `uninstall` removes the service cleanly.
7. Logs explain failures clearly enough to debug configuration or permission problems.

## Windows Installer

The installer uses Inno Setup. Install Inno Setup locally first, then build the exe and compile the installer script:

```powershell
python -m pip install --upgrade ".[exe]"
pyinstaller --noconfirm --onefile --windowed --name PhotoManagerPro --icon photosync_tool_assets/photo_manager_icon.ico --add-data "photosync_tool_assets;photosync_tool_assets" photo_manager_gui.py
iscc installer\PhotoManagerPro.iss
```

The installer is written to `release\PhotoManagerProSetup-<version>.exe`.

Installer smoke test:

1. Run the setup file.
2. Confirm the app installs under the current user profile.
3. Launch the app from the Start Menu shortcut.
4. Optionally create and test the desktop shortcut.
5. Uninstall from Windows Apps settings or the Start Menu uninstall shortcut.

## Code Signing

Code signing means attaching a trusted digital certificate to the `.exe` or installer. It tells Windows that the file came from a known publisher and was not modified after signing.

Unsigned builds still run, but Windows SmartScreen may warn users because the publisher is unknown. Signing is not required for a portfolio alpha release, but it becomes useful for public distribution.

For later production-style releases, sign the installer rather than only the raw PyInstaller exe.

## Publish

1. Update `version` in `pyproject.toml`.
2. Update `CHANGELOG.md`.
3. Commit and push to `main`.
4. Create a GitHub Release for a matching tag, for example `v0.1.2`.
5. The `.github/workflows/publish.yml` workflow builds and uploads the package to PyPI.
6. The `.github/workflows/windows-exe.yml` workflow builds `PhotoManagerPro.exe` and attaches it to the GitHub Release.

## GitHub Release Notes Template

```markdown
## Photo Manager Pro v0.1.2

Alpha release focused on local photo organization, sync, indexing, and review workflows.

### Added

- PySide6 desktop GUI.
- Year-based photo/video sorting with EXIF, filename, and timestamp fallback.
- One-shot sync and background folder watching.
- CSV sync log, dashboard sync report export, and local SQLite library index.
- Dashboard, thumbnail gallery, duplicate review, and Safe Delete Queue.
- Blur scanning with OpenCV.
- Local Light AI tags/captions with optional OCR.
- Windows executable build workflow.
- CI coverage for package build, lint, tests, version consistency, and GUI smoke startup.

### Known Gaps

- Windows Service mode needs more clean-machine testing.
- Installer flow is available through Inno Setup but still needs release testing.
- GUI smoke tests and screenshot fixtures are still limited.
```

## Install After Release

```powershell
python -m pip install photosync-tool
photo-manager-pro
```

The project license is MIT.
