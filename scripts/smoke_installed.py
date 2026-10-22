#!/usr/bin/env python3
"""Run installed console scripts from an empty cwd, never import the checkout."""
import os
from pathlib import Path
import subprocess
import tempfile
from importlib import metadata


def main():
    distribution = metadata.distribution('photosync-tool')
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen')
    env.pop('PYTHONPATH', None)
    with tempfile.TemporaryDirectory(prefix='photosync-installed-') as tmp:
        for entry in distribution.entry_points:
            if entry.group == 'console_scripts':
                subprocess.run([entry.name, '--help'], check=True, cwd=tmp, env=env, timeout=30)
        subprocess.run(['photo-manager-pro', '--smoke-test'], check=True, cwd=tmp, env=env, timeout=60)
    print('PASS: installed entry points and isolated Qt startup')


if __name__ == '__main__':
    main()
