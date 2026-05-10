#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from photo_manager_core import (
    BackgroundSyncService,
    default_app_config,
    default_config_path,
    user_config_dir,
    load_app_config,
    resolve_runtime_config,
    run_batch_sync,
)


SERVICE_NAME = "PhotoManagerProService"
SERVICE_DISPLAY_NAME = "Photo Manager Pro Background Service"
SERVICE_DESCRIPTION = "Runs Photo Manager Pro synchronization in the background without the GUI."
SERVICE_LOG_NAME = "photo_manager_service.log"
SYSTEMD_SERVICE_NAME = "photo-manager-pro.service"

try:
    import servicemanager  # type: ignore
    import win32event  # type: ignore
    import win32service  # type: ignore
    import win32serviceutil  # type: ignore
except Exception:
    servicemanager = None
    win32event = None
    win32service = None
    win32serviceutil = None


class FileLogger:
    def __init__(self, path: Path, echo: bool = True) -> None:
        self.path = path
        self.echo = echo
        self._lock = threading.Lock()

    def __call__(self, msg: str) -> None:
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        if self.echo:
            print(line, flush=True)


def script_dir() -> Path:
    return Path(__file__).resolve().parent


def load_runtime_config(config_path: Optional[Path] = None):
    base_dir = script_dir()
    config = config_path or default_config_path()
    app_cfg = load_app_config(config, default_app_config(base_dir))
    return resolve_runtime_config(app_cfg, base_dir)


def run_foreground(config_path: Optional[Path] = None, echo: bool = True) -> None:
    logger = FileLogger(user_config_dir() / SERVICE_LOG_NAME, echo=echo)
    cfg = load_runtime_config(config_path)

    stop_event = threading.Event()
    runner = BackgroundSyncService(logger)

    def stop(_signum=None, _frame=None) -> None:
        logger("Stop requested.")
        stop_event.set()

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, stop)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, stop)

    logger("Service foreground mode starting.")
    runner.start(cfg)
    try:
        while not stop_event.wait(1.0):
            pass
    finally:
        runner.stop()
        logger("Service foreground mode stopped.")


def run_once(config_path: Optional[Path] = None, echo: bool = True) -> None:
    logger = FileLogger(user_config_dir() / SERVICE_LOG_NAME, echo=echo)
    cfg = load_runtime_config(config_path)
    logger("One-shot sync starting.")
    run_batch_sync(cfg, logger)
    logger("One-shot sync finished.")


def systemd_user_unit_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "systemd" / "user"


def systemd_unit_path() -> Path:
    return systemd_user_unit_dir() / SYSTEMD_SERVICE_NAME


def systemd_quote(value: object) -> str:
    text = str(value)
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_systemd_unit(config_path: Optional[Path] = None) -> str:
    resolved_config = config_path or default_config_path()
    script = Path(__file__).resolve()
    exec_args = [
        sys.executable,
        str(script),
        "run",
        "--config",
        str(resolved_config),
    ]
    exec_start = " ".join(systemd_quote(part) for part in exec_args)
    working_directory = systemd_quote(script_dir())
    log_path = user_config_dir() / SERVICE_LOG_NAME

    return f"""[Unit]
Description={SERVICE_DISPLAY_NAME}
Documentation=https://github.com/Filipluke/PhotoSyncTool
After=default.target

[Service]
Type=simple
WorkingDirectory={working_directory}
ExecStart={exec_start}
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target

# Logs are also written by the app to:
# {log_path}
"""


def run_systemctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    if shutil.which("systemctl") is None:
        raise SystemExit("systemctl is required for Linux systemd user service commands.")
    return subprocess.run(["systemctl", "--user", *args], text=True, check=check)


def handle_systemd_service_command(command: str, config_path: Optional[Path] = None) -> None:
    if not sys.platform.startswith("linux"):
        raise SystemExit("systemd service commands are supported on Linux only.")

    unit_path = systemd_unit_path()

    if command == "install":
        unit_path.parent.mkdir(parents=True, exist_ok=True)
        unit_path.write_text(build_systemd_unit(config_path), encoding="utf-8")
        run_systemctl("daemon-reload")
        run_systemctl("enable", SYSTEMD_SERVICE_NAME)
        print(f"Installed user systemd service: {unit_path}")
        print(f"Start it with: systemctl --user start {SYSTEMD_SERVICE_NAME}")
        print("For start-after-login without an active session, run: loginctl enable-linger $USER")
        return

    if command == "uninstall":
        run_systemctl("stop", SYSTEMD_SERVICE_NAME, check=False)
        run_systemctl("disable", SYSTEMD_SERVICE_NAME, check=False)
        if unit_path.exists():
            unit_path.unlink()
        run_systemctl("daemon-reload")
        print(f"Removed user systemd service: {SYSTEMD_SERVICE_NAME}")
        return

    if command == "start":
        run_systemctl("start", SYSTEMD_SERVICE_NAME)
        return
    if command == "stop":
        run_systemctl("stop", SYSTEMD_SERVICE_NAME)
        return
    if command == "restart":
        run_systemctl("restart", SYSTEMD_SERVICE_NAME)
        return
    if command == "status":
        result = run_systemctl("status", SYSTEMD_SERVICE_NAME, check=False)
        raise SystemExit(result.returncode)

    raise SystemExit(f"Unsupported Linux service command: {command}")


if win32serviceutil is not None:

    class PhotoManagerWindowsService(win32serviceutil.ServiceFramework):  # type: ignore[misc]
        _svc_name_ = SERVICE_NAME
        _svc_display_name_ = SERVICE_DISPLAY_NAME
        _svc_description_ = SERVICE_DESCRIPTION

        def __init__(self, args):
            win32serviceutil.ServiceFramework.__init__(self, args)
            self.stop_event = win32event.CreateEvent(None, 0, 0, None)
            self.runner: Optional[BackgroundSyncService] = None

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            win32event.SetEvent(self.stop_event)
            if self.runner is not None:
                self.runner.stop()

        def SvcDoRun(self):
            if servicemanager is not None:
                servicemanager.LogInfoMsg(f"{SERVICE_NAME} starting")

            logger = FileLogger(user_config_dir() / SERVICE_LOG_NAME, echo=False)
            cfg = load_runtime_config()
            self.runner = BackgroundSyncService(logger)
            self.runner.start(cfg)

            win32event.WaitForSingleObject(self.stop_event, win32event.INFINITE)

            if self.runner is not None:
                self.runner.stop()
            if servicemanager is not None:
                servicemanager.LogInfoMsg(f"{SERVICE_NAME} stopped")


def handle_windows_service_command(command: str) -> None:
    if win32serviceutil is None:
        raise SystemExit(
            "pywin32 is required for Windows Service commands. Install with: py -m pip install pywin32"
        )

    mapped = "remove" if command == "uninstall" else command
    sys.argv = [sys.argv[0], mapped]
    win32serviceutil.HandleCommandLine(PhotoManagerWindowsService)


def handle_platform_service_command(command: str, config_path: Optional[Path] = None) -> None:
    if sys.platform == "win32":
        handle_windows_service_command(command)
        return
    if sys.platform.startswith("linux"):
        handle_systemd_service_command(command, config_path=config_path)
        return
    raise SystemExit(
        "Platform service commands are supported on Windows Services and Linux systemd user services. "
        "Use `photo-manager-service run` for foreground background sync on this platform."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Photo Manager Pro headless/background service.")
    parser.add_argument(
        "command",
        nargs="?",
        default="run",
        choices=["run", "once", "install", "uninstall", "start", "stop", "restart", "status", "debug"],
        help=(
            "run=foreground watcher, once=batch sync, install/start/stop=Windows Service "
            "or Linux systemd user-service commands"
        ),
    )
    parser.add_argument("--config", type=str, default="", help="Optional path to photo_manager_config.json")
    args = parser.parse_args()

    config_path = Path(args.config).expanduser().resolve() if args.config else None

    if args.command == "run":
        run_foreground(config_path=config_path, echo=True)
        return
    if args.command == "once":
        run_once(config_path=config_path, echo=True)
        return

    handle_platform_service_command(args.command, config_path=config_path)


if __name__ == "__main__":
    main()
