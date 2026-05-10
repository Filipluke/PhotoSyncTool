# Contributing

Photo Manager Pro is an alpha desktop project, so changes should keep the core local-photo workflow reliable before expanding the feature surface.

## Development Setup

```powershell
python -m pip install -e ".[dev]"
python -m pre_commit install
python -m ruff check .
python -m pytest
```

For runtime GUI work, install the regular application dependencies:

```powershell
python -m pip install -e .
python photo_manager_gui.py
```

## Quality Bar

- Keep public documentation, release notes, workflow names, and user-facing GitHub content in English.
- Prefer focused changes with tests for core behavior, indexing, sync decisions, and destructive file operations.
- Do not commit private photos, personal filesystem paths, generated indexes, sync logs, build outputs, or local configuration.
- Treat delete flows as safety-critical: queue files for review before moving anything to the recycle bin.
- Keep heavier AI backends optional so the desktop app stays practical to install and run.

## Pull Request Checklist

- Keep changes focused on one feature, bug fix, or documentation improvement.
- Add or update tests when behavior changes.
- Avoid committing generated build outputs from `build/`, `dist/`, or `release/`.
- Update `CHANGELOG.md` under `Unreleased` for user-visible changes.
- Include screenshots when the GUI or GitHub Pages site changes.

## Release Checks

Before preparing a release, run:

```powershell
python -m pytest
python -m ruff check .
python -m build
python -m twine check dist/*.tar.gz dist/*.whl
```

Windows executable, installer, and service behavior still need manual smoke testing on a clean Windows machine or VM before public release promotion.

## Release Notes

Use `RELEASE.md` for operational release steps and `CHANGELOG.md` for user-facing history.
