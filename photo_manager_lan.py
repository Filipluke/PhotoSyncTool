#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import re
import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, List, Optional
from urllib.parse import parse_qs, urlparse

from sort_photos_script import MEDIA_EXTS


DEFAULT_LAN_PORT = 47832
DEFAULT_LAN_INBOX = "Phone Inbox"
MAX_UPLOAD_BYTES = 8 * 1024 * 1024 * 1024
PAIRING_HEADER = "X-PhotoManager-Code"
FILENAME_HEADER = "X-PhotoManager-Filename"
SIZE_HEADER = "X-PhotoManager-Size"

Logger = Callable[[str], None]
UploadCallback = Callable[[Path, int, str], None]


@dataclass
class UploadReceipt:
    filename: str
    saved_path: str
    bytes_received: int
    client: str
    received_at: str


def is_allowed_upload_name(filename: str) -> bool:
    return Path(filename).suffix.lower() in MEDIA_EXTS


def sanitize_filename(raw: str, fallback: str = "photo") -> str:
    name = Path(str(raw or "")).name.strip()
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    name = name.replace("/", "_").replace("\\", "_").strip(" .")
    if not name:
        name = fallback

    stem = Path(name).stem.strip(" .") or fallback
    suffix = Path(name).suffix
    if len(stem) > 120:
        stem = stem[:120].rstrip(" .") or fallback
    return f"{stem}{suffix}"


def unique_upload_path(folder: Path, filename: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    safe_name = sanitize_filename(filename)
    target = folder / safe_name
    stem = target.stem
    suffix = target.suffix
    counter = 1
    while target.exists():
        target = folder / f"{stem}__{counter}{suffix}"
        counter += 1
    return target


def default_lan_inbox(root: Path) -> Path:
    return root / DEFAULT_LAN_INBOX


def dated_inbox_folder(inbox: Path, when: Optional[datetime] = None) -> Path:
    current = when or datetime.now()
    return inbox / current.strftime("%Y-%m-%d")


def local_ip_candidates() -> List[str]:
    candidates: List[str] = []

    def add(ip: str) -> None:
        if not ip or ip == "0.0.0.0" or ":" in ip:
            return
        if ip not in candidates:
            candidates.append(ip)

    try:
        host = socket.gethostname()
        for ip in socket.gethostbyname_ex(host)[2]:
            add(ip)
    except OSError:
        pass

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            add(sock.getsockname()[0])
    except OSError:
        pass

    loopback = "127.0.0.1"
    if loopback not in candidates:
        candidates.append(loopback)
    return candidates


def lan_endpoint_urls(port: int) -> List[str]:
    return [f"http://{ip}:{port}" for ip in local_ip_candidates()]


class _UploadHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        incoming_dir: Path,
        pairing_code: str,
        log: Logger,
        on_upload: Optional[UploadCallback],
    ) -> None:
        super().__init__(server_address, _UploadHandler)
        self.incoming_dir = incoming_dir
        self.pairing_code = pairing_code.strip()
        self.log = log
        self.on_upload = on_upload


class _UploadHandler(BaseHTTPRequestHandler):
    server: _UploadHTTPServer

    def log_message(self, _format: str, *_args) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in {"", "/", "/health"}:
            self._send_json(
                200,
                {
                    "app": "Photo Manager Pro",
                    "status": "ready",
                    "upload_path": "/upload",
                    "requires_code": bool(self.server.pairing_code),
                    "inbox": str(self.server.incoming_dir),
                },
            )
            return
        self._send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/upload":
            self._send_json(404, {"error": "not_found"})
            return

        if not self._check_pairing_code(parsed):
            self._send_json(403, {"error": "pairing_code_required"})
            return

        raw_length = self.headers.get("Content-Length", "").strip()
        try:
            expected_size = int(raw_length)
        except ValueError:
            self._send_json(411, {"error": "content_length_required"})
            return
        if expected_size < 0 or expected_size > MAX_UPLOAD_BYTES:
            self._send_json(413, {"error": "upload_too_large", "max_bytes": MAX_UPLOAD_BYTES})
            return

        query = parse_qs(parsed.query)
        filename = (
            query.get("filename", [""])[0]
            or self.headers.get(FILENAME_HEADER, "")
            or f"upload-{int(time.time())}.jpg"
        )
        filename = sanitize_filename(filename)
        if not is_allowed_upload_name(filename):
            self._send_json(415, {"error": "unsupported_media_type", "filename": filename})
            return

        target = unique_upload_path(dated_inbox_folder(self.server.incoming_dir), filename)
        received = 0
        try:
            with target.open("wb") as out:
                remaining = expected_size
                while remaining > 0:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    out.write(chunk)
                    received += len(chunk)
                    remaining -= len(chunk)
        except Exception as exc:
            target.unlink(missing_ok=True)
            self._send_json(500, {"error": "write_failed", "details": str(exc)})
            return

        if received != expected_size:
            target.unlink(missing_ok=True)
            self._send_json(
                400,
                {
                    "error": "size_mismatch",
                    "expected_bytes": expected_size,
                    "received_bytes": received,
                },
            )
            return

        client = str(self.client_address[0])
        receipt = UploadReceipt(
            filename=target.name,
            saved_path=str(target),
            bytes_received=received,
            client=client,
            received_at=datetime.now().isoformat(timespec="seconds"),
        )
        self.server.log(f"phone-connect: received {target.name} ({received} bytes) from {client}")
        if self.server.on_upload is not None:
            self.server.on_upload(target, received, client)
        self._send_json(201, receipt.__dict__)

    def _check_pairing_code(self, parsed) -> bool:
        expected = self.server.pairing_code
        if not expected:
            return True
        query = parse_qs(parsed.query)
        provided = (
            self.headers.get(PAIRING_HEADER, "")
            or self.headers.get("X-PMP-Code", "")
            or query.get("code", [""])[0]
        )
        return provided.strip() == expected

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


class MobileTransferServer:
    def __init__(
        self,
        incoming_dir: Path,
        port: int = DEFAULT_LAN_PORT,
        pairing_code: str = "",
        log: Optional[Logger] = None,
        on_upload: Optional[UploadCallback] = None,
    ) -> None:
        self.incoming_dir = incoming_dir
        self.port = int(port)
        self.pairing_code = pairing_code.strip()
        self.log = log or (lambda _msg: None)
        self.on_upload = on_upload
        self._server: Optional[_UploadHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self._server is not None and self._thread is not None and self._thread.is_alive()

    @property
    def actual_port(self) -> int:
        if self._server is None:
            return self.port
        return int(self._server.server_address[1])

    def start(self) -> int:
        if self.running:
            return self.actual_port
        self.incoming_dir.mkdir(parents=True, exist_ok=True)
        self._server = _UploadHTTPServer(
            ("0.0.0.0", self.port),
            self.incoming_dir,
            self.pairing_code,
            self.log,
            self.on_upload,
        )
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="PhotoManagerLanReceiver",
            daemon=True,
        )
        self._thread.start()
        self.log(f"phone-connect: receiver listening on port {self.actual_port}")
        return self.actual_port

    def stop(self) -> None:
        server = self._server
        if server is None:
            return
        self._server = None
        server.shutdown()
        server.server_close()
        self.log("phone-connect: receiver stopped")
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._thread = None

