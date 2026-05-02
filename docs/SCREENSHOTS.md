# Screenshot Guide

Screenshots are useful for the README and for GitHub Releases. Keep them small, current, and focused on real workflows.

Recommended files:

- `docs/screenshots/main.png` - main sync configuration and controls.
- `docs/screenshots/dashboard.png` - Dashboard tab after rebuilding the index.
- `docs/screenshots/gallery.png` - Gallery tab with thumbnails visible.
- `docs/screenshots/duplicates.png` - Duplicate Review tab with sample duplicate candidates.
- `docs/screenshots/delete-queue.png` - Safe Delete Queue with queued items.

The GitHub Pages starter site currently links directly to:

- `docs/screenshots/dashboard.png`
- `docs/screenshots/gallery.png`
- `docs/screenshots/duplicates.png`

Add those three first so the landing page stops showing browser image placeholders.

Suggested capture flow:

1. Create a small demo photo root outside the repository.
2. Add a few copied sample images and videos with safe, non-private content.
3. Run the app, point it at the demo root, and rebuild the index.
4. Capture the app window at about `1440x900`.
5. Save screenshots as PNG files under `docs/screenshots/`.
6. Add only the best 2-3 screenshots to the README so it stays readable.

Example README snippet:

```markdown
## Screenshots

![Dashboard](docs/screenshots/dashboard.png)
![Gallery](docs/screenshots/gallery.png)
```

Do not commit screenshots containing private photos, real paths, or personal data.
