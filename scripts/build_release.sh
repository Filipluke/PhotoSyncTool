#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_CMD="${PYTHON:-python}"
UPLOAD_PYPI=0
SKIP_TESTS=0
SKIP_EXE=0
SKIP_INSTALLER=0
SKIP_PYPI_DIST=0

usage() {
  cat <<'EOF'
Usage: scripts/build_release.sh [options]

Builds local release artifacts:
  - PyPI distributions in dist/
  - Windows executable in dist/PhotoManagerPro.exe
  - Inno Setup installer in release/ when iscc is available

Options:
  --upload-pypi      Upload dist/*.tar.gz and dist/*.whl with twine after checks
  --skip-tests       Skip pytest
  --skip-exe         Skip PyInstaller executable build
  --skip-installer   Skip Inno Setup installer build
  --skip-pypi-dist   Skip Python sdist/wheel build
  -h, --help         Show this help

Environment:
  PYTHON=py          Override Python command, for example PYTHON=py
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --upload-pypi)
      UPLOAD_PYPI=1
      shift
      ;;
    --skip-tests)
      SKIP_TESTS=1
      shift
      ;;
    --skip-exe)
      SKIP_EXE=1
      shift
      ;;
    --skip-installer)
      SKIP_INSTALLER=1
      shift
      ;;
    --skip-pypi-dist)
      SKIP_PYPI_DIST=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

log() {
  printf '\n==> %s\n' "$1"
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

require_command "$PYTHON_CMD"

log "Installing build dependencies"
"$PYTHON_CMD" -m pip install --upgrade ".[dev,exe]"

if [[ "$SKIP_TESTS" -eq 0 ]]; then
  log "Running tests"
  "$PYTHON_CMD" -m pytest
fi

if [[ "$SKIP_PYPI_DIST" -eq 0 ]]; then
  log "Cleaning old PyPI distributions"
  mkdir -p dist
  rm -f dist/*.tar.gz dist/*.whl

  log "Building PyPI distributions"
  "$PYTHON_CMD" -m build

  log "Checking PyPI distributions"
  "$PYTHON_CMD" -m twine check dist/*.tar.gz dist/*.whl
fi

if [[ "$SKIP_EXE" -eq 0 ]]; then
  log "Building Windows executable"
  pyinstaller \
    --noconfirm \
    --onefile \
    --windowed \
    --name PhotoManagerPro \
    --icon photosync_tool_assets/photo_manager_icon.ico \
    --add-data "photosync_tool_assets;photosync_tool_assets" \
    photo_manager_gui.py
fi

if [[ "$SKIP_INSTALLER" -eq 0 ]]; then
  if command -v iscc >/dev/null 2>&1; then
    log "Building Windows installer"
    iscc installer/PhotoManagerPro.iss
  else
    log "Skipping installer build: Inno Setup compiler (iscc) not found in PATH"
    echo "Install Inno Setup and add iscc to PATH to build release/PhotoManagerProSetup-*.exe."
  fi
fi

if [[ "$UPLOAD_PYPI" -eq 1 ]]; then
  log "Uploading PyPI distributions"
  "$PYTHON_CMD" -m twine upload dist/*.tar.gz dist/*.whl
fi

log "Release artifacts"
find dist release -maxdepth 1 -type f 2>/dev/null | sort || true
