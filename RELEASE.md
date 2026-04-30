# Release Guide

This project is prepared for PyPI publishing through GitHub Actions and PyPI Trusted Publishing.

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
python -m build
python -m twine check dist/*
```

## Publish

1. Update `version` in `pyproject.toml`.
2. Update `CHANGELOG.md`.
3. Commit and push to `main`.
4. Create a GitHub Release for a matching tag, for example `v0.1.0`.
5. The `.github/workflows/publish.yml` workflow builds and uploads the package to PyPI.

## Install After Release

```powershell
python -m pip install photosync-tool
photo-manager-pro
```

Before a public release, choose and add a project license if this package should be open source.
