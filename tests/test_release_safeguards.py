from io import BytesIO
import hashlib
import importlib.util
import json
from pathlib import Path
from urllib.error import HTTPError
import pytest

spec = importlib.util.spec_from_file_location('check_pypi', Path(__file__).resolve().parents[1] / 'scripts/check_pypi_release.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


@pytest.fixture
def artifacts(tmp_path):
    for name in ('photosync_tool-1.2.3.whl', 'photosync_tool-1.2.3.tar.gz'):
        (tmp_path / name).write_bytes(name.encode())
    return tmp_path


def test_identical_pypi_artifacts_skip_upload(artifacts, monkeypatch):
    data = {'urls': [{'filename': p.name, 'digests': {'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}}
                     for p in artifacts.iterdir()]}
    monkeypatch.setattr(check, 'urlopen', lambda *a, **kw: BytesIO(json.dumps(data).encode()))
    assert check.publication_required(artifacts, '1.2.3') is False
    next(artifacts.iterdir()).write_bytes(b'different build')
    with pytest.raises(ValueError, match='already exists'):
        check.publication_required(artifacts, '1.2.3')


@pytest.mark.parametrize('code', [404, 403, 500])
def test_only_a_missing_version_permits_publication(artifacts, monkeypatch, code):
    def fail(*a, **kw):
        raise HTTPError('https://pypi.org', code, 'test', {}, None)
    monkeypatch.setattr(check, 'urlopen', fail)
    if code == 404:
        assert check.publication_required(artifacts, '1.2.3') is True
    else:
        with pytest.raises(HTTPError):
            check.publication_required(artifacts, '1.2.3')


def test_missing_artifacts_fail_closed(tmp_path):
    with pytest.raises(ValueError, match='exactly one'):
        check.publication_required(tmp_path, '1.2.3')
