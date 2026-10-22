# Photo Manager Pro design system

## Product
Photographic archive meets precision engineering. The application is a workspace
for files, verification and deliberate cleanup. Photographs and actionable state
take precedence over decoration. Keep the existing Qt workflows and safety checks.

## Tokens
The desktop source is `photo_manager_theme.py`; web equivalents are in
`docs/assets/site.css`. Dark desktop / light website values:

| Role | Desktop | Website |
|---|---|---|
| Background | #202320 | #f4f1e9 |
| Surface | #292d29 | #fffdf7 |
| Foreground | #f0eee6 | #242b26 |
| Secondary text | #b7beb4 | #59645c |
| Border | #4b534b | #cbd0c5 |
| Accent / selection | #355d49 | #294c3b |
| Focus / copper | #d6a478 | #87532f |
| Warning | #e4bb7e | #785124 |
| Success | #b2d1b8 | #315b3e |
| Destructive | #edb0a7 on #66342f | #8d352d |

## Typography and licensing
Bundle IBM Plex Sans (UI), IBM Plex Mono Regular (paths, logs, counters), and
IBM Plex Serif Regular (website headings). Original font files come from
google/fonts `ofl/ibmplexsans`, `ofl/ibmplexmono`, `ofl/ibmplexserif`, distributed
under SIL Open Font License 1.1. Include `photosync_tool_assets/fonts/OFL.txt`
and its web copy with every distribution. Never fetch fonts at application start.
Qt registers bundled fonts through QFontDatabase; verify registration in installed
wheel and executable smoke tests. Use regular and semibold only; no synthetic
heavy weights. Qt point sizes respect system DPI; the base UI is 10 pt.

## Components and layout
Use a 4 px spacing unit: 4, 8, 12, 16, 24. Controls use 4 px corners; previews
and tables are square. Native Qt layouts, table views, splitters and scroll areas
carry structure. Stable grouped navigation; the command bar holds import actions.
One compact library summary, one actionable status, and recent events precede
secondary year/folder tables. Do not repeat the same totals in several panels.
Gallery filters and item actions occupy separate rows. A resizable inspector
shows the selected image and metadata. Duplicate comparison labels must remain
visible after images load. Queuing is distinct from moving files to Trash.
All original confirmation dialogs and verification checks remain in place.

## Accessibility
Visible keyboard focus; text plus color for state. Associate form labels and
accessible names with controls. Keep native keyboard/table selection behavior.
Support 1120×720 and 1920×1080 logical desktop sizes and Qt 2× scaling; long
forms scroll vertically. Website works from 320 px to 3840 px, keyboard-only,
without JavaScript, and with reduced motion. No essential hover-only controls.
Use readable contrast, no decorative animation and no truncated critical actions.

## Prohibited patterns
No navy/cyan dashboard, purple gradients, glows, glass panels, oversized rounded
cards, floating badges, fake statistics, simulated product screenshots, generic
SaaS slogans or repeated uppercase eyebrow headings. No frontend framework.

## Visual verification
`python scripts/capture_demo_screenshots.py` creates real Qt captures using a
temporary, explicitly labelled public-domain demo library. Never capture private
photos, credentials or configuration. `python scripts/build_site_images.py`
derives responsive previews from these captures. Capture scripts must isolate
both current and legacy configuration and disable autostart before construction.
Run GUI smoke tests at compact/large sizes and `QT_SCALE_FACTOR=2`.
Inspect gallery, dashboard, duplicates, cleanup, settings and diagnostics captures.
Run `python scripts/check_site.py` and repeat with `SITE_SCALE=2`; missing
critical assets must fail the build even if browser fallback is graceful.
Capture before/after images for substantial changes, and record platforms actually
tested. Screenshots on the website must disclose when they show unreleased code.
