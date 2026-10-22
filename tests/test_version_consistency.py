from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def project_version() -> str:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"$', pyproject, re.MULTILINE)
    assert match is not None
    return match.group(1)


def test_release_version_is_consistent() -> None:
    version = project_version()

    installer = (ROOT / "installer" / "PhotoManagerPro.iss").read_text(encoding="utf-8")
    gui = (ROOT / "photo_manager_qt.py").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    release = (ROOT / "RELEASE.md").read_text(encoding="utf-8")

    assert f'#define MyAppVersion "{version}"' in installer
    assert f'return "{version}"' in gui
    assert re.search(rf"^## {re.escape(version)} - \d{{4}}-\d{{2}}-\d{{2}}$", changelog, re.MULTILINE)
    assert f"Photo Manager Pro v{version}" in release


def test_public_downloads_match_current_release() -> None:
    version = project_version()
    page = (ROOT / 'docs/index.html').read_text(encoding='utf-8')
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    assert set(re.findall(r'releases/(?:download|tag)/v([0-9.]+)', page)) == {version}
    assert set(re.findall(r'photosync-tool==([0-9.]+)', page + readme)) == {version}
    assert f'project/photosync-tool/{version}/' in page
    assert f'data-release-version>{version}<' in page
    assert 'Development preview; current downloads' in page
