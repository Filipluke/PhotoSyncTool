#!/usr/bin/env python3
"""Build small responsive web assets from the original application screenshots."""
import base64
from io import BytesIO
from pathlib import Path
import re

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
WIDTHS = (640, 960, 1440)


def embed_hero(screenshots: Path) -> None:
    """Deliver the first preview with the HTML, without another network request."""
    with Image.open(screenshots / "gallery.png") as original:
        if original.width * 900 != original.height * 1440:
            raise ValueError("Website captures must have the canonical 1440:900 aspect ratio")
        image = original.convert("RGB").resize((960, 600), Image.Resampling.LANCZOS)
        for quality in (72, 68, 64, 60):
            output = BytesIO()
            image.save(output, "WEBP", quality=quality, method=6)
            payload = output.getvalue()
            if len(payload) <= 48_000:
                break
    if len(payload) > 48_000:
        raise ValueError(f"Hero image exceeds the 48 KB transfer budget: {len(payload)}")
    uri = "data:image/webp;base64," + base64.b64encode(payload).decode("ascii")
    page = ROOT / "docs" / "index.html"
    content, count = re.subn(r'(<img id="hero-screenshot" src=")[^"]*(")',
                             lambda match: match[1] + uri + match[2], page.read_text())
    if count != 1:
        raise ValueError("Expected exactly one hero screenshot")
    page.write_text(content)
    print(f"Inline hero: {len(payload):,} bytes, zero image requests")


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
    embed_hero(screenshots)
    with Image.open(ROOT / "docs" / "assets" / "icon.png") as original:
        original.resize((32, 32), Image.Resampling.LANCZOS).save(
            ROOT / "docs" / "assets" / "favicon.png", optimize=True
        )


if __name__ == "__main__":
    build()
