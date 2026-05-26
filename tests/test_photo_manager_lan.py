from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from photo_manager_lan import MobileTransferServer, lan_endpoint_urls, sanitize_filename, unique_upload_path


def test_sanitize_filename_strips_paths_and_control_chars() -> None:
    assert sanitize_filename("../DCIM/\x00IMG_001.JPG") == "IMG_001.JPG"
    assert sanitize_filename("  .  ") == "photo"


def test_unique_upload_path_adds_suffix(tmp_path: Path) -> None:
    first = unique_upload_path(tmp_path, "IMG_001.jpg")
    first.write_bytes(b"existing")

    assert unique_upload_path(tmp_path, "IMG_001.jpg").name == "IMG_001__1.jpg"


def test_lan_endpoint_urls_include_loopback() -> None:
    assert "http://127.0.0.1:47832" in lan_endpoint_urls(47832)


def test_mobile_transfer_server_accepts_paired_upload(tmp_path: Path) -> None:
    received = []
    server = MobileTransferServer(
        tmp_path / "inbox",
        port=0,
        pairing_code="123456",
        on_upload=lambda path, size, client: received.append((path, size, client)),
    )
    port = server.start()
    try:
        filename = urllib.parse.quote("IMG_20260514_120000.jpg")
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/upload?filename={filename}",
            data=b"photo-bytes",
            method="POST",
            headers={"X-PhotoManager-Code": "123456", "Content-Type": "application/octet-stream"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))

        saved = Path(payload["saved_path"])
        assert response.status == 201
        assert saved.read_bytes() == b"photo-bytes"
        assert saved.parent.parent == tmp_path / "inbox"
        assert received and received[0][0] == saved
    finally:
        server.stop()


def test_mobile_transfer_server_rejects_bad_pairing_code(tmp_path: Path) -> None:
    server = MobileTransferServer(tmp_path / "inbox", port=0, pairing_code="123456")
    port = server.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/upload?filename=IMG_001.jpg",
            data=b"photo-bytes",
            method="POST",
            headers={"X-PhotoManager-Code": "000000"},
        )
        try:
            urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
        else:
            raise AssertionError("upload without a valid pairing code should fail")
    finally:
        server.stop()

