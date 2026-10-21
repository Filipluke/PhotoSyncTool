#!/usr/bin/env python3
"""Build small responsive web assets from the original application screenshots."""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
WIDTHS = (640, 960, 1440)


def build() -> None:
    screenshots = ROOT / "docs" / "screenshots"
    for name in ("gallery", "dashboard", "duplicates"):
        with Image.open(screenshots / f"{name}.png") as original:
            for width in WIDTHS:
                height = round(original.height * width / original.width)
                image = original.convert("RGB").resize((width, height), Image.Resampling.LANCZOS)
                target = screenshots / f"{name}-{width}.webp"
                image.save(target, "WEBP", quality=85, method=6)
                print(f"{target.relative_to(ROOT)}: {target.stat().st_size:,} bytes")
    with Image.open(ROOT / "docs" / "assets" / "icon.png") as original:
        original.resize((32, 32), Image.Resampling.LANCZOS).save(
            ROOT / "docs" / "assets" / "favicon.png", optimize=True
        )


if __name__ == "__main__":
    build()
