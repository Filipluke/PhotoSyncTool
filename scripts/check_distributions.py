#!/usr/bin/env python3
"""Inspect actual wheel/sdist resources before publication."""
from pathlib import Path
import argparse
import tarfile
import zipfile

REQUIRED = {
    'photo_manager_qt.py', 'photo_manager_import.py', 'photo_manager_theme.py',
    'photosync_tool_assets/photo_manager_icon.png', 'photosync_tool_assets/photo_manager_icon.ico',
    'photosync_tool_assets/fonts/IBMPlexSans.ttf',
    'photosync_tool_assets/fonts/IBMPlexMono-Regular.ttf', 'photosync_tool_assets/fonts/OFL.txt',
}


def check(dist):
    wheels, sources = list(dist.glob('*.whl')), list(dist.glob('*.tar.gz'))
    assert len(wheels) == len(sources) == 1, 'Use a clean output directory with one wheel and one sdist'
    with zipfile.ZipFile(wheels[0]) as wheel:
        missing = REQUIRED - set(wheel.namelist())
        assert not missing, f'Wheel missing resources: {missing}'
        entry = next(n for n in wheel.namelist() if n.endswith('.dist-info/entry_points.txt'))
        entries = wheel.read(entry).decode()
        for name in ('photo-manager-pro', 'photo-manager-service', 'photo-manager-index',
                     'photo-manager-drive', 'photo-blur-tool', 'photo-sorter'):
            assert f'{name} = ' in entries, f'Missing entry point: {name}'
    with tarfile.open(sources[0]) as source:
        names = {n.partition('/')[2] for n in source.getnames()}
        assert not (REQUIRED - names), f'Sdist missing resources: {REQUIRED - names}'
        assert {'DESIGN.md', 'AGENTS.md'} <= names
    print('PASS: wheel and source resources, font license, icons and six entry points')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dist', type=Path)
    check(parser.parse_args().dist)
