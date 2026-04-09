#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys


def main() -> None:
    try:
        from photo_manager_qt import main as qt_main
    except Exception as exc:
        print("Cannot start modern GUI.")
        print("Install dependencies with:")
        print("  py -m pip install PySide6 watchdog send2trash pillow opencv-python")
        print(f"Details: {exc}")
        sys.exit(1)
    qt_main()


if __name__ == "__main__":
    main()
