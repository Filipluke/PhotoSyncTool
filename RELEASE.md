# Releasing Photo Manager Pro

Current release: **Photo Manager Pro v0.2.1**. User-facing notes are in [RELEASE_NOTES.md](RELEASE_NOTES.md).

## Local validation

Use a disposable virtual environment and sample library:

```sh
python -m pip install -e ".[dev]"
python -m ruff check .
QT_QPA_PLATFORM=offscreen python -m pytest
python -m build
python -m twine check dist/*
```

On Windows, set `QT_QPA_PLATFORM=offscreen` in the shell environment. CI requires PySide6 to import, so missing Qt cannot silently skip GUI validation.

## Public release

1. Update the package version in `pyproject.toml`, the GUI fallback, and the Inno Setup version. Update CHANGELOG, RELEASE_NOTES, this document, and the website links.
2. Review and push the tested changes to `main`. Wait for CI to pass.
3. Tag the reviewed commit with the matching `vX.Y.Z` and push that tag.
4. `publish.yml` validates the tag and runs Linux Python 3.10/3.13 and Windows Python 3.13 tests, including Qt startup and built-wheel checks.
5. It builds the wheel, source distribution, and Windows EXE. The EXE must remain running during its automated startup check.
6. The existing `pypi` environment and Trusted Publisher for **publish.yml** publish the Python distributions. The dependent GitHub release job attaches the same distributions, EXE, and SHA256SUMS.
7. Verify the public PyPI JSON, install the published wheel in a fresh environment, check the GitHub release assets, and verify the deployed Pages website.

A manual release workflow must be dispatched on the matching tag, never `main`. Windows EXE can be built independently using its manual workflow, but that does not publish a release. No second workflow depends on a bot-created `release.published` event.

## Recovery

If a pre-publication job fails, fix and rerun it before publishing. Never overwrite a published PyPI version or move a published tag. If PyPI succeeds but GitHub release creation fails, rerun only the release job (or create the release using the already-built artifacts); do not rebuild/upload different bytes under the same version. A released regression needs a new patch version. Repository/site rollback uses a forward revert commit, preserving history.

The Inno Setup definition is retained for local installer builds; this workflow distributes a standalone EXE, not a signed installer. See [scripts/build_release.sh](scripts/build_release.sh) for optional local builds.
