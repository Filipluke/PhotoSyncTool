# Demo cat photographs

These photographs are bundled only for the disposable demonstration library used
by `scripts/capture_demo_screenshots.py`. The website displays real captures of
the application using these photos. No images are fetched from third-party sites
when visitors open the page or when the screenshot build runs.

| Local file | Photographer | Source | License |
| --- | --- | --- | --- |
| white-cat.webp | ABCDéLTDP | [White sleepy cat](https://commons.wikimedia.org/wiki/File:White_sleepy_cat.jpg) | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| calico-cat.webp | Andras Ostrom | [Calico wabe cat](https://commons.wikimedia.org/wiki/File:Calico_wabe_cat.jpg) | Public domain, released by the author (PD-self) |
| tabby-cat.webp | Dlls | [Cat public domain dedication image 0008](https://commons.wikimedia.org/wiki/File:Cat_public_domain_dedication_image_0008.jpg) | Public domain, released by the author (PD-self) |
| nopple-cat.webp | Nopple | [Nopple cat](https://commons.wikimedia.org/wiki/File:Nopple_cat.jpg) | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |

Sources and licenses checked on 2026-10-21. Downloaded Wikimedia thumbnails were
converted to WebP, resized where needed, and saved without EXIF metadata. The
originals and their source descriptions are available through the links above.

To regenerate the website assets with the app's Qt dependencies installed:

```sh
QT_QPA_PLATFORM=offscreen python scripts/capture_demo_screenshots.py
python scripts/build_site_images.py
python scripts/check_site.py
```

The build embeds the main 960×600 preview directly in the HTML with a 48 KB
compressed-image budget. The larger interactive previews remain responsive
image files loaded when needed. GitHub Pages runs these steps before deployment.
