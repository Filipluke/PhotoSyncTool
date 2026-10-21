## Photo Manager Pro v0.2.1

This patch fixes photo import collisions, repeat imports, and source cleanup after failed watcher copies. Imports now verify complete SHA256 hashes, publish complete temporary copies without overwriting an existing file, and save verification receipts in the local SQLite library index. Retrying an interrupted import rechecks existing bytes and resumes at the file level.

The project website now has real application screenshots, separate Windows and Python installation instructions, and clearer alpha status. The release workflow tests Windows and Linux before building and publishing the same tagged version to PyPI and GitHub.

### Install

- Windows: download `PhotoManagerPro.exe` and compare its SHA256 with `SHA256SUMS.txt`.
- Python 3.10+: in a virtual environment, run `python -m pip install --upgrade photosync-tool==0.2.1`, then `photo-manager-pro`.
- Linux needs a graphical desktop and Qt runtime libraries. See the README for setup.

### Scope and limitations

- Alpha software: keep a separate backup and try a small disposable library first.
- The Windows executable is unsigned. Automated startup checks do not replace an interactive test on your own machine.
- Verified atomic publication on Linux requires a filesystem supporting hard links and directory fsync (for example ext4). Unsupported targets fail the import and retain the source. Windows uses non-overwriting rename.
- Interrupted files are copied again; this release does not implement byte-level resume. Abandoned `.photosync-*.part` files from a forcibly killed process may remain and are ignored by the importer.
- Repeat detection checks matching destination names and their collision suffixes, not a global search of all library content.
- Phone Connect and Google Drive remain optional. Physical phone transfers, Drive OAuth, and background-service installation need separate real-device testing.
