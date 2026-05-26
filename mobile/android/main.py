#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import os
import shutil
import threading
from http.client import HTTPConnection, HTTPSConnection
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import quote, urlparse

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, RoundedRectangle
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.tabbedpanel import TabbedPanel, TabbedPanelItem
from kivy.uix.textinput import TextInput


APP_NAME = "Photo Manager Mobile"
DEFAULT_INCOMING = "PhotoManagerPro/Incoming"
DEFAULT_DESKTOP_URL = ""
PAIRING_HEADER = "X-PhotoManager-Code"
MEDIA_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".bmp",
    ".tif",
    ".tiff",
    ".heic",
    ".heif",
    ".mp4",
    ".mov",
    ".m4v",
    ".avi",
    ".mkv",
    ".3gp",
}


def external_storage_root() -> Path:
    root = os.environ.get("EXTERNAL_STORAGE")
    if root:
        return Path(root)
    sdcard = Path("/sdcard")
    if sdcard.exists():
        return sdcard
    return Path.home()


def default_incoming_folder() -> Path:
    return external_storage_root() / DEFAULT_INCOMING


def request_android_permissions(log: Callable[[str], None]) -> None:
    try:
        from android.permissions import Permission, request_permissions  # type: ignore
    except Exception:
        return

    permissions = [
        Permission.READ_EXTERNAL_STORAGE,
        Permission.WRITE_EXTERNAL_STORAGE,
    ]
    for name in ("READ_MEDIA_IMAGES", "READ_MEDIA_VIDEO", "INTERNET", "ACCESS_NETWORK_STATE", "ACCESS_WIFI_STATE"):
        value = getattr(Permission, name, None)
        if value:
            permissions.append(value)
    request_permissions(permissions, lambda _permissions, _grants: log("Permissions updated."))


def media_paths(paths: Iterable[str], log: Callable[[str], None]):
    for raw in paths:
        src = Path(str(raw))
        if not src.exists():
            log(f"Skipped missing path: {raw}")
            continue
        if src.is_dir():
            log(f"Skipped folder: {src.name}")
            continue
        if src.suffix.lower() not in MEDIA_EXTENSIONS:
            log(f"Skipped non-media file: {src.name}")
            continue
        yield src


def copy_media(paths: Iterable[str], target_dir: Path, log: Callable[[str], None]) -> int:
    target_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for src in media_paths(paths, log):
        dst = target_dir / src.name
        counter = 1
        while dst.exists():
            dst = target_dir / f"{src.stem}__{counter}{src.suffix}"
            counter += 1
        shutil.copy2(src, dst)
        copied += 1
        log(f"Copied: {src.name}")
    return copied


def normalize_base_url(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        raise ValueError("Desktop URL is empty.")
    if not text.startswith(("http://", "https://")):
        text = f"http://{text}"
    parsed = urlparse(text)
    if not parsed.hostname:
        raise ValueError("Desktop URL is invalid.")
    return text.rstrip("/")


def connection_for(url: str, timeout: int = 20):
    parsed = urlparse(url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    cls = HTTPSConnection if parsed.scheme == "https" else HTTPConnection
    return cls(parsed.hostname, port, timeout=timeout), parsed


def read_json_response(response) -> dict:
    body = response.read().decode("utf-8", errors="replace")
    try:
        payload = json.loads(body or "{}")
    except json.JSONDecodeError:
        payload = {"raw": body}
    if response.status >= 400:
        raise RuntimeError(payload.get("error") or f"HTTP {response.status}")
    return payload


def check_desktop(base_url: str) -> dict:
    base = normalize_base_url(base_url)
    conn, parsed = connection_for(base, timeout=8)
    path = (parsed.path.rstrip("/") or "") + "/health"
    try:
        conn.request("GET", path)
        return read_json_response(conn.getresponse())
    finally:
        conn.close()


def upload_one(src: Path, base_url: str, pairing_code: str, log: Callable[[str], None]) -> dict:
    base = normalize_base_url(base_url)
    conn, parsed = connection_for(base, timeout=120)
    upload_path = (parsed.path.rstrip("/") or "") + f"/upload?filename={quote(src.name)}"
    size = src.stat().st_size
    try:
        conn.putrequest("POST", upload_path)
        conn.putheader("Content-Type", "application/octet-stream")
        conn.putheader("Content-Length", str(size))
        if pairing_code.strip():
            conn.putheader(PAIRING_HEADER, pairing_code.strip())
        conn.endheaders()
        sent = 0
        with src.open("rb") as f:
            while True:
                chunk = f.read(512 * 1024)
                if not chunk:
                    break
                conn.send(chunk)
                sent += len(chunk)
        payload = read_json_response(conn.getresponse())
        log(f"Sent: {src.name} ({format_bytes(sent)})")
        return payload
    finally:
        conn.close()


def upload_media(paths: Iterable[str], base_url: str, pairing_code: str, log: Callable[[str], None]) -> int:
    uploaded = 0
    for src in media_paths(paths, log):
        upload_one(src, base_url, pairing_code, log)
        uploaded += 1
    return uploaded


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


class Card(BoxLayout):
    def __init__(self, bg=(0.08, 0.08, 0.12, 1), **kwargs) -> None:
        super().__init__(**kwargs)
        with self.canvas.before:
            Color(*bg)
            self._rect = RoundedRectangle(radius=[dp(12)])
        self.bind(pos=self._sync_rect, size=self._sync_rect)

    def _sync_rect(self, *_args) -> None:
        self._rect.pos = self.pos
        self._rect.size = self.size


class PhotoManagerMobile(BoxLayout):
    def __init__(self, **kwargs) -> None:
        super().__init__(orientation="vertical", spacing=dp(12), padding=dp(14), **kwargs)
        Window.clearcolor = (0.045, 0.043, 0.06, 1)
        self.selected_paths: list[str] = []

        self.add_widget(self._build_header())
        self.tabs = TabbedPanel(do_default_tab=False, tab_height=dp(44))
        self.tabs.background_color = (0.045, 0.043, 0.06, 1)
        self.tabs.add_widget(self._tab("Send", self._build_send_tab()))
        self.tabs.add_widget(self._tab("Connect", self._build_connect_tab()))
        self.tabs.add_widget(self._tab("Inbox", self._build_inbox_tab()))
        self.tabs.add_widget(self._tab("Log", self._build_log_tab()))
        self.add_widget(self.tabs)

    def _build_header(self) -> Card:
        header = Card(orientation="vertical", padding=dp(14), spacing=dp(3), size_hint_y=None, height=dp(92))
        title = Label(
            text=APP_NAME,
            size_hint_y=None,
            height=dp(34),
            font_size="23sp",
            bold=True,
            color=(0.95, 0.97, 1, 1),
            halign="left",
        )
        title.bind(size=title.setter("text_size"))
        self.status_label = Label(
            text="Ready",
            size_hint_y=None,
            height=dp(28),
            font_size="14sp",
            color=(0.62, 0.72, 0.84, 1),
            halign="left",
        )
        self.status_label.bind(size=self.status_label.setter("text_size"))
        header.add_widget(title)
        header.add_widget(self.status_label)
        return header

    def _tab(self, title: str, content) -> TabbedPanelItem:
        item = TabbedPanelItem(text=title)
        item.add_widget(content)
        return item

    def _label(self, text: str, height: int = 26, bold: bool = False) -> Label:
        label = Label(
            text=text,
            size_hint_y=None,
            height=dp(height),
            font_size="14sp",
            bold=bold,
            color=(0.86, 0.9, 0.96, 1),
            halign="left",
            valign="middle",
        )
        label.bind(size=label.setter("text_size"))
        return label

    def _input(self, text: str = "", hint: str = "", password: bool = False) -> TextInput:
        return TextInput(
            text=text,
            hint_text=hint,
            password=password,
            multiline=False,
            size_hint_y=None,
            height=dp(48),
            background_color=(0.06, 0.065, 0.09, 1),
            foreground_color=(0.94, 0.96, 1, 1),
            cursor_color=(0.35, 0.86, 0.78, 1),
            hint_text_color=(0.45, 0.52, 0.62, 1),
            padding=[dp(12), dp(12), dp(12), dp(10)],
        )

    def _button(self, text: str, style: str = "default") -> Button:
        colors = {
            "primary": (0.08, 0.72, 0.65, 1),
            "secondary": (0.05, 0.36, 0.55, 1),
            "quiet": (0.1, 0.11, 0.16, 1),
            "warm": (0.9, 0.37, 0.12, 1),
            "default": (0.12, 0.14, 0.2, 1),
        }
        return Button(
            text=text,
            size_hint_y=None,
            height=dp(48),
            background_normal="",
            background_down="",
            background_color=colors.get(style, colors["default"]),
            color=(0.96, 0.98, 1, 1),
            bold=True,
            font_size="14sp",
        )

    def _build_send_tab(self) -> ScrollView:
        scroll = ScrollView()
        body = BoxLayout(orientation="vertical", spacing=dp(12), padding=[0, dp(12), 0, dp(12)], size_hint_y=None)
        body.bind(minimum_height=body.setter("height"))

        picker = Card(orientation="vertical", padding=dp(14), spacing=dp(10), size_hint_y=None)
        picker.bind(minimum_height=picker.setter("height"))
        picker.add_widget(self._label("Media", bold=True))
        self.selection_label = self._label("No files selected", height=30)
        picker.add_widget(self.selection_label)
        pick_btn = self._button("Choose Media", "primary")
        pick_btn.bind(on_release=lambda _btn: self.choose_media())
        picker.add_widget(pick_btn)

        action_grid = GridLayout(cols=2, spacing=dp(8), size_hint_y=None, height=dp(48))
        send_btn = self._button("Send To Desktop", "warm")
        copy_btn = self._button("Copy Local", "secondary")
        send_btn.bind(on_release=lambda _btn: self.upload_selected())
        copy_btn.bind(on_release=lambda _btn: self.copy_selected())
        action_grid.add_widget(send_btn)
        action_grid.add_widget(copy_btn)
        picker.add_widget(action_grid)
        body.add_widget(picker)

        scroll.add_widget(body)
        return scroll

    def _build_connect_tab(self) -> ScrollView:
        scroll = ScrollView()
        body = BoxLayout(orientation="vertical", spacing=dp(12), padding=[0, dp(12), 0, dp(12)], size_hint_y=None)
        body.bind(minimum_height=body.setter("height"))

        card = Card(orientation="vertical", padding=dp(14), spacing=dp(10), size_hint_y=None)
        card.bind(minimum_height=card.setter("height"))
        card.add_widget(self._label("Desktop", bold=True))
        self.desktop_url_input = self._input(DEFAULT_DESKTOP_URL, "http://192.168.1.10:47832")
        self.pairing_code_input = self._input("", "Pairing code")
        card.add_widget(self.desktop_url_input)
        card.add_widget(self.pairing_code_input)
        test_btn = self._button("Test Connection", "secondary")
        test_btn.bind(on_release=lambda _btn: self.check_connection())
        card.add_widget(test_btn)
        body.add_widget(card)

        scroll.add_widget(body)
        return scroll

    def _build_inbox_tab(self) -> ScrollView:
        scroll = ScrollView()
        body = BoxLayout(orientation="vertical", spacing=dp(12), padding=[0, dp(12), 0, dp(12)], size_hint_y=None)
        body.bind(minimum_height=body.setter("height"))

        card = Card(orientation="vertical", padding=dp(14), spacing=dp(10), size_hint_y=None)
        card.bind(minimum_height=card.setter("height"))
        card.add_widget(self._label("Phone Inbox", bold=True))
        self.target_input = self._input(str(default_incoming_folder()), "Phone inbox folder")
        card.add_widget(self.target_input)
        open_btn = self._button("Open Folder", "quiet")
        open_btn.bind(on_release=lambda _btn: self.open_target())
        card.add_widget(open_btn)
        body.add_widget(card)

        scroll.add_widget(body)
        return scroll

    def _build_log_tab(self) -> ScrollView:
        scroll = ScrollView()
        self.log_output = Label(
            text="Ready.",
            size_hint_y=None,
            font_size="13sp",
            color=(0.78, 0.84, 0.92, 1),
            halign="left",
            valign="top",
            padding=[dp(10), dp(10)],
        )
        self.log_output.bind(texture_size=lambda _instance, value: setattr(self.log_output, "height", value[1] + dp(24)))
        self.log_output.bind(width=lambda instance, value: setattr(instance, "text_size", (value, None)))
        scroll.add_widget(self.log_output)
        return scroll

    def run_worker(self, name: str, target: Callable[[], None]) -> None:
        self.set_status(f"{name}...")
        thread = threading.Thread(target=target, name=f"PhotoManagerMobile-{name}", daemon=True)
        thread.start()

    def set_status(self, message: str) -> None:
        Clock.schedule_once(lambda _dt: setattr(self.status_label, "text", message), 0)

    def log(self, message: str) -> None:
        Clock.schedule_once(lambda _dt: self._append_log(message), 0)

    def _append_log(self, message: str) -> None:
        existing = self.log_output.text.strip()
        lines = [line for line in existing.splitlines() if line]
        lines.append(message)
        self.log_output.text = "\n".join(lines[-220:])

    def choose_media(self) -> None:
        try:
            from plyer import filechooser  # type: ignore
        except Exception as exc:
            self.log(f"File chooser unavailable: {exc}")
            return

        self.log("Opening media picker...")
        try:
            filechooser.open_file(
                on_selection=self.on_media_selected,
                multiple=True,
                filters=[["Media files", "*.jpg", "*.jpeg", "*.png", "*.webp", "*.heic", "*.mp4", "*.mov"]],
            )
        except TypeError:
            filechooser.open_file(on_selection=self.on_media_selected, multiple=True)
        except Exception as exc:
            self.log(f"Could not open picker: {exc}")

    def on_media_selected(self, selection: list[str]) -> None:
        self.selected_paths = list(selection or [])
        count = len(self.selected_paths)
        self.selection_label.text = f"Selected files: {count}" if count else "No files selected"
        self.set_status(f"Selected {count} file(s)" if count else "Selection cancelled")
        self.log(f"Selected {count} file(s)." if count else "Selection cancelled.")

    def copy_selected(self) -> None:
        if not self.selected_paths:
            self.log("Choose media first.")
            return

        def worker() -> None:
            target = Path(self.target_input.text.strip() or str(default_incoming_folder())).expanduser()
            try:
                copied = copy_media(self.selected_paths, target, self.log)
            except Exception as exc:
                self.log(f"Copy failed: {exc}")
                self.set_status("Copy failed")
                return
            self.set_status(f"Copied {copied} file(s)")
            self.log(f"Done. Incoming folder: {target}")

        self.run_worker("Copying", worker)

    def upload_selected(self) -> None:
        if not self.selected_paths:
            self.log("Choose media first.")
            return

        def worker() -> None:
            try:
                uploaded = upload_media(
                    self.selected_paths,
                    self.desktop_url_input.text,
                    self.pairing_code_input.text,
                    self.log,
                )
            except Exception as exc:
                self.log(f"Upload failed: {exc}")
                self.set_status("Upload failed")
                return
            self.set_status(f"Sent {uploaded} file(s)")
            self.log(f"Upload complete: {uploaded} file(s).")

        self.run_worker("Uploading", worker)

    def check_connection(self) -> None:
        def worker() -> None:
            try:
                payload = check_desktop(self.desktop_url_input.text)
            except Exception as exc:
                self.log(f"Connection failed: {exc}")
                self.set_status("Desktop unavailable")
                return
            status = payload.get("status", "unknown")
            self.set_status(f"Desktop {status}")
            self.log(f"Desktop response: {payload.get('app', 'Photo Manager Pro')} / {status}")

        self.run_worker("Checking", worker)

    def open_target(self) -> None:
        target = Path(self.target_input.text.strip() or str(default_incoming_folder())).expanduser()
        target.mkdir(parents=True, exist_ok=True)
        try:
            from plyer import filechooser  # type: ignore

            filechooser.open_file(path=str(target))
        except Exception:
            self.log(f"Target folder: {target}")


class PhotoManagerMobileApp(App):
    def build(self):
        root = PhotoManagerMobile()
        Clock.schedule_once(lambda _dt: request_android_permissions(root.log), 0.5)
        return root


if __name__ == "__main__":
    PhotoManagerMobileApp().run()
