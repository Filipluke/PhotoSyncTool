from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image


@dataclass
class DemoLibrary:
    root: Path
    source: Path
    library_photo: Path
    source_photo: Path
    duplicate_one: Path
    duplicate_two: Path
    temp_download: Path


def create_tiny_image(path: Path, *, color: tuple[int, int, int] = (32, 96, 160)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (16, 12), color)
    image.save(path)
    return path


def create_demo_library(base: Path) -> DemoLibrary:
    root = base / "library"
    source = base / "source"
    source.mkdir(parents=True)
    root.mkdir(parents=True)

    source_photo = create_tiny_image(source / "IMG_20260506_120000.jpg", color=(30, 120, 190))
    library_photo = create_tiny_image(root / "2026" / "library_photo.jpg", color=(210, 150, 40))

    duplicate_one = root / "2026" / "duplicate.jpg"
    duplicate_two = root / "2026" / "duplicate__1.jpg"
    duplicate_one.write_bytes(b"same duplicate bytes")
    duplicate_two.write_bytes(b"same duplicate bytes")

    temp_download = source / "partial.jpg.crdownload"
    temp_download.write_bytes(b"incomplete")

    return DemoLibrary(
        root=root,
        source=source,
        library_photo=library_photo,
        source_photo=source_photo,
        duplicate_one=duplicate_one,
        duplicate_two=duplicate_two,
        temp_download=temp_download,
    )
