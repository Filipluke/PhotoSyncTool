#!/usr/bin/env python3
"""Render the static site in an isolated headless Chromium profile.

Checks real layout at 100% scale, image loading, screenshot switching and failed
image requests. Requires Chromium or Google Chrome; no Python test dependencies.
"""
from __future__ import annotations

import hashlib
import html
from html.parser import HTMLParser
import http.server
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1] / "docs"
HARNESS = r'''<!doctype html><meta charset="utf-8">
<style>body{margin:0}iframe{border:0;display:block;width:1366px;height:900px}</style>
<pre id="results">RUNNING</pre><iframe id="site"></iframe><script>
const frame = document.getElementById('site');
const scale = Number(new URLSearchParams(location.search).get("scale") || 1);
const results = {deviceScale: scale, viewports: [], screenshots: [], fallbacks: []};
const assert = (condition, message) => { if (!condition) throw Error(message); };
const wait = async (check, label) => {
  for (let count=0; count<100; count++) {
    if (check()) return;
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  throw Error('Timeout: '+label);
};
const open = async prefix => {
  frame.src = prefix+'/index.html';
  await wait(() => frame.contentWindow.location.pathname === prefix+'/index.html' &&
    frame.contentDocument?.title.startsWith('Photo Manager') &&
    frame.contentDocument.readyState === 'complete', 'page load');
  return frame.contentDocument;
};
const ready = doc => [...doc.images].every(img => img.complete && img.naturalWidth > 0);
(async () => { try {
  let doc = await open('');
  assert(frame.contentWindow.devicePixelRatio === scale, 'Unexpected device scale');
  for (const img of doc.images) img.loading = 'eager';
  await wait(() => ready(doc), 'initial images');
  await doc.fonts.ready;
  assert([...doc.fonts].filter(font => font.status === 'loaded').length === 3, 'Bundled fonts must load');
  const hero = doc.getElementById('hero-screenshot');
  assert(hero.currentSrc.startsWith('data:image/webp;base64,'), 'Hero must not wait for an image request');
  assert(!hero.closest('picture').querySelector('source'), 'Hero must not select an external source');
  assert(hero.naturalWidth === 960, 'Full hero preview is present');
  results.hero = {separateImageRequests: 0, width: hero.naturalWidth};
  for (const width of [320,360,375,390,414,600,760,768,820,980,1024,1280,1366,1440,1920,2560,3440,3840]) {
    frame.style.width = width+'px';
    // Force a layout after resizing; compare against clientWidth, excluding the scrollbar.
    const client = doc.documentElement.clientWidth;
    assert(doc.documentElement.scrollWidth === client, 'Horizontal scrolling at '+width);
    const outside = [...doc.body.querySelectorAll('*')].filter(el => {
      const bounds = el.getBoundingClientRect();
      return bounds.width > 0 && (bounds.right > client+1 || bounds.left < -1);
    });
    assert(!outside.length, 'Element outside viewport at '+width+': '+outside[0]?.className);
    results.viewports.push({width, client, scrollWidth:doc.documentElement.scrollWidth});
  }
  frame.style.width = '1366px';
  for (const view of ['dashboard','duplicates','gallery']) {
    doc.querySelector('[data-view="'+view+'"]').click();
    const img = doc.getElementById('app-screenshot');
    await wait(() => img.complete && img.naturalWidth>0 && img.currentSrc.includes(view+'-'), view);
    assert(img.currentSrc.endsWith('.webp'), 'Expected optimized preview');
    assert(doc.querySelector('[data-view="'+view+'"]').getAttribute('aria-pressed')==='true', 'Selected view');
    assert(!img.closest('picture').hidden, 'Visible preview');
    results.screenshots.push(view);
  }
  doc = await open('/no-webp');
  for (const img of doc.images) img.loading = 'eager';
  await wait(() => ready(doc) && doc.getElementById('app-screenshot').currentSrc.endsWith('.png'), 'PNG fallback');
  results.fallbacks.push('WebP failure -> PNG');
  doc = await open('/no-images');
  for (const img of doc.images) img.loading = 'eager';
  await wait(() => [...doc.querySelectorAll('.showcase .image-error')].every(el=>!el.hidden), 'visible error');
  assert([...doc.querySelectorAll('.showcase picture')].every(el=>el.hidden), 'Hide broken images');
  assert([...doc.querySelectorAll('.showcase .image-error a')].every(el=>el.href.endsWith('.png')), 'Original image link');
  results.fallbacks.push('Remote images fail -> readable error and original link');
  const offlineHero = doc.getElementById('hero-screenshot');
  assert(offlineHero.complete && offlineHero.naturalWidth === 960 && !offlineHero.closest('picture').hidden,
    'Hero must remain visible even when every image request fails');
  results.hero.visibleWithImageNetworkBlocked = true;
  frame.setAttribute('sandbox', 'allow-same-origin');
  doc = await open('/no-js');
  for (const img of doc.images) img.loading = 'eager';
  await wait(() => ready(doc), 'images without JavaScript');
  assert(!doc.documentElement.classList.contains('js'), 'Scripts must be disabled for this check');
  assert(doc.querySelector('#download a[href$="PhotoManagerPro.exe"]'), 'Windows download without JavaScript');
  assert(doc.querySelector('#install-command').textContent.includes('photo-manager-pro'), 'Python instructions without JavaScript');
  assert(doc.querySelector('noscript a[href$="dashboard.png"]'), 'Alternate screenshots without JavaScript');
  results.noJavaScript = 'PASS';
  results.status = 'PASS';
} catch(error) { results.status='FAIL'; results.error=String(error); }
document.getElementById('results').textContent=JSON.stringify(results);
})();
</script>'''


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, *_args):
        pass

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/__check.html':
            payload = HARNESS.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        for prefix in ('/no-webp', '/no-images', '/no-js'):
            if path.startswith(prefix + '/'):
                if prefix != '/no-js' and '/screenshots/' in path and (path.endswith('.webp') or prefix == '/no-images'):
                    self.send_error(404, 'Simulated image failure')
                    return
                self.path = self.path[len(prefix):]
                break
        super().do_GET()


def check_assets() -> None:
    """Fail before rendering if a critical asset is missing, corrupt or stale."""
    from PIL import Image
    from urllib.parse import unquote
    class References(HTMLParser):
        def __init__(self):
            super().__init__()
            self.paths = set()
            self.ids = set()
            self.anchors = set()
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if 'id' in attrs:
                self.ids.add(attrs['id'])
            for key in ('src', 'href'):
                value = attrs.get(key, '')
                url = urlsplit(value)
                if value.startswith('#') and value != '#':
                    self.anchors.add(value[1:])
                elif url.path and not url.scheme and not url.netloc:
                    self.paths.add(unquote(url.path))
    page = References()
    page.feed((ROOT / 'index.html').read_text(encoding='utf-8'))
    assert page.anchors <= page.ids, f'Broken anchors: {page.anchors - page.ids}'
    for name in page.paths:
        assert (ROOT / name).is_file(), f'Missing referenced asset: {name}'
    for name in ('gallery', 'dashboard', 'duplicates'):
        for suffix in ('.png', '-640.webp', '-960.webp', '-1440.webp'):
            path = ROOT / 'screenshots' / (name + suffix)
            with Image.open(path) as image:
                image.verify()
    for font in ('IBMPlexSans.ttf', 'IBMPlexMono-Regular.ttf', 'IBMPlexSerif-Regular.ttf', 'OFL.txt'):
        web = ROOT / 'assets' / 'fonts' / font
        bundled = ROOT.parent / 'photosync_tool_assets' / 'fonts' / font
        assert web.read_bytes() == bundled.read_bytes(), f'Font/license drift: {font}'
    manifest = json.loads((ROOT / 'screenshots' / 'manifest.json').read_text())
    assert manifest['sources'], 'Screenshot provenance is empty'
    for name in ('photo_manager_qt.py', 'photo_manager_theme.py', 'scripts/capture_demo_screenshots.py'):
        digest = hashlib.sha256((ROOT.parent / name).read_bytes()).hexdigest()
        assert manifest['sources'].get(name) == digest, f'Stale screenshot source: {name}; recapture first'


def main() -> None:
    check_assets()
    browser = os.environ.get('SITE_BROWSER') or next(
        (path for name in ('google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser')
         if (path := shutil.which(name))), None
    )
    if not browser:
        raise SystemExit('FAIL: install Chromium or Google Chrome to check the site')
    scale = os.environ.get('SITE_SCALE', '1')
    if scale not in ('1', '2'):
        raise SystemExit('SITE_SCALE must be 1 or 2')
    with http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory(prefix='photosync-site-check-') as profile:
                result = subprocess.run(
                    [browser, '--headless', '--disable-gpu', '--disable-features=WebContentsForceDark', '--no-first-run',
                     f'--user-data-dir={profile}', f'--force-device-scale-factor={scale}',
                     '--window-size=1440,1000', '--virtual-time-budget=30000', '--dump-dom',
                     f'http://127.0.0.1:{server.server_port}/__check.html?scale={scale}'],
                    capture_output=True, text=True, timeout=60, check=False,
                )
            if result.returncode:
                print(result.stderr[-4000:])
                raise SystemExit(f'FAIL: browser exited with {result.returncode}')
            match = re.search(r'<pre id="results">(.*?)</pre>', result.stdout, re.S)
            if not match or match[1] == 'RUNNING':
                raise SystemExit('FAIL: browser did not finish the checks')
            report = json.loads(html.unescape(match[1]))
            print(json.dumps(report, indent=2))
            if report.get('status') != 'PASS':
                raise SystemExit(1)
        finally:
            server.shutdown()


if __name__ == '__main__':
    main()
