#!/usr/bin/env python3
"""Refuse immutable-version drift; skip upload only when every byte already exists."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen


def publication_required(dist: Path, version: str) -> bool:
    files = sorted([*dist.glob('*.whl'), *dist.glob('*.tar.gz')])
    if len(files) != 2 or not any(p.suffix == '.whl' for p in files):
        raise ValueError('Expected exactly one wheel and one source distribution')
    local = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    try:
        with urlopen(f'https://pypi.org/pypi/photosync-tool/{version}/json', timeout=30) as response:
            remote = json.load(response)
    except HTTPError as exc:
        if exc.code == 404:
            return True
        raise
    published = {item['filename']: item['digests']['sha256'] for item in remote['urls']}
    if published != local:
        raise ValueError(f'PyPI {version} already exists with different artifacts. '
                         'Use the original artifacts to recover, or prepare a new version; never overwrite a tag.')
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist', type=Path, default=Path('dist'))
    parser.add_argument('--version', required=True)
    args = parser.parse_args()
    required = publication_required(args.dist, args.version)
    print('Publish new version' if required else 'Exact artifacts already on PyPI; upload skipped')
    if output := os.environ.get('GITHUB_OUTPUT'):
        with open(output, 'a', encoding='utf-8') as stream:
            stream.write(f'publish={str(required).lower()}\n')


if __name__ == '__main__':
    main()
