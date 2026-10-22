# PhotoSyncTool contributor rules

Read DESIGN.md before changing UI or the website. Preserve the shared IBM Plex,
warm neutral, graphite, forest and copper design system and bundled font licenses.
Use native Qt widgets and existing static HTML/CSS/JS. No new frontend framework.

Preserve atomic no-overwrite imports, full SHA256 verification, durable receipts,
restartable retries, source retention on any failure, explicit cleanup confirmation,
single-instance behavior, tray lifecycle and all configuration capabilities.
Do not rewrite synchronization, indexing, cloud or deletion logic for appearance.
Never run tests on personal photo libraries or real cloud accounts. Isolate current
and legacy configuration before constructing test windows; disable autostart.

SQLite initialization must tolerate concurrent processes as well as threads. Retry
only transient lock failures within a bounded deadline; never suppress exceptions,
weaken assertions, disable tests or add unconditional sleeps to make CI pass.

Before editing, record Git HEAD/branch/status and preserve existing work in backups.
Keep changes scoped. Validate with Ruff, pytest, repeated concurrency tests, real Qt
screenshots at compact/large sizes and 2× DPI, website checks at 1×/2×, build and
twine, plus installed entry points outside the checkout. Report skips honestly.
Include fonts/icons in wheels, sdists and executables. Keep version checks aligned.

Never reuse an existing release tag or publish to PyPI/GitHub Releases without
explicit user authorization. Preserve Trusted Publishing OIDC, minimal workflow
permissions, immutable artifact hashes and release concurrency. No new credentials.
Code/website changes are not a release. Label previews of unreleased UI accurately.
Do not commit secrets, personal paths, local reports or disposable test libraries.
