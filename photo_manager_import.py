"""Verified, restartable imports shared by the desktop, service and CLI."""
from __future__ import annotations

import os
import shutil
import stat
import tempfile
from contextlib import closing
from pathlib import Path

from sort_photos_script import sha256, safe_destination_path


def _flush_directory(directory: Path) -> None:
    if os.name == 'posix':
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def copy_verified(src: Path, requested: Path, root: Path) -> tuple[Path, str, str]:
    """Publish complete bytes without replacing an existing file, then commit a receipt.

    Retrying rechecks the actual destination bytes, including when publication
    succeeded but the process stopped before writing its SQLite receipt.
    """
    from photo_manager_index import connect, default_index_path, normalize_path, utc_now

    digest = sha256(src)
    size = src.stat().st_size
    requested.parent.mkdir(parents=True, exist_ok=True)
    destination = requested
    temporary = None
    status = 'already_present'
    try:
        while True:
            if destination.resolve() == src.resolve():
                raise ValueError('Source and destination must be different files')
            if os.path.lexists(destination):
                if destination.is_file() and destination.stat().st_size == size and sha256(destination) == digest:
                    break
                destination = safe_destination_path(requested.parent, src.name, source=src)
                continue
            if temporary is None:
                fd, name = tempfile.mkstemp(prefix='.photosync-', suffix='.part', dir=requested.parent)
                os.close(fd)
                temporary = Path(name)
                shutil.copyfile(src, temporary)
                if sha256(temporary) != digest or sha256(src) != digest:
                    raise OSError('Source changed or copied bytes failed SHA256 verification')
                with temporary.open('r+b') as handle:
                    shutil.copystat(src, temporary)
                    os.fsync(handle.fileno())
            try:
                # Both operations fail on an existing destination; never os.replace.
                if os.name == 'nt':
                    os.rename(temporary, destination)
                else:
                    os.link(temporary, destination)
                _flush_directory(destination.parent)
                status = 'verified'
                break
            except FileExistsError:
                # Another importer published between planning and publication.
                continue
        if sha256(src) != digest or sha256(destination) != digest:
            raise OSError('File changed before import confirmation')
        with closing(connect(default_index_path(root))) as conn:
            conn.execute(
                '''INSERT INTO import_receipts (src, dst, sha256, size_bytes, verified_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(dst) DO UPDATE SET src=excluded.src, sha256=excluded.sha256,
                       size_bytes=excluded.size_bytes, verified_at=excluded.verified_at''',
                (normalize_path(src), normalize_path(destination), digest, size, utc_now()),
            )
            conn.commit()
        return destination, status, digest
    finally:
        if temporary is not None:
            if os.name == 'nt' and temporary.exists():
                # A source's read-only attribute may have been copied to our
                # unpublished temporary file. Never change the published file.
                temporary.chmod(temporary.stat().st_mode | stat.S_IWRITE)
            temporary.unlink(missing_ok=True)


def originals_verified(actions) -> bool:
    """Recheck every destination immediately before allowing source cleanup."""
    from sort_photos_script import verify_copy
    try:
        return bool(actions) and all(
            action.dsts and all(
                action.src.resolve() != dst.resolve()
                and verify_copy(action.src, dst, full_hash=True)[0]
                for dst in action.dsts
            )
            for action in actions
        )
    except OSError:
        return False
