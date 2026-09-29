"""Build an allowlisted private ZIP locally; no Docker build and no model calls."""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[3]
CHROME_VERSION = '154.0.8037.57'


def source_files(root):
    names = ['requirements.txt', 'requirements-browser.txt', 'requirements-agent.txt', 'constraints.txt',
             'webapp/requirements.txt', 'webapp/data/products.json', 'webapp/data/catalog.sqlite3',
             'webapp/portfolio/products.json', 'webapp/portfolio/requirements.txt']
    for pattern in ('app/**/*.py', 'webapp/*.py', 'webapp/portfolio/*.py', 'webapp/templates/*.html',
                    'webapp/static/*.css', 'webapp/static/*.js', 'webapp/static/products/*.png'):
        names.extend(p.relative_to(root).as_posix() for p in root.glob(pattern) if p.is_file())
    files = []
    for name in sorted(set(names)):
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError('Missing or unsafe source file: ' + name)
        files.append((name, path))
    return files


def download_browser(folder):
    manifest = {'version': CHROME_VERSION, 'sha256': {}}
    for name in ('chrome', 'chromedriver'):
        target = folder / (name + '.zip')
        url = f'https://storage.googleapis.com/chrome-for-testing-public/{CHROME_VERSION}/linux64/{name}-linux64.zip'
        # Build-time TLS download from Google's fixed version, then pin the entire
        # resulting package by SHA-256 in Terraform. Runtime downloads only ADLS.
        with urlopen(url, timeout=60) as response, target.open('wb') as output:
            if response.geturl() != url:
                raise ValueError('Unexpected browser redirect')
            total = 0
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > 350 * 1024 * 1024:
                    raise ValueError('Browser archive too large')
                output.write(chunk)
        with zipfile.ZipFile(target) as archive:
            required = f'{name}-linux64/{name}'
            if required not in archive.namelist() or archive.testzip():
                raise ValueError('Invalid browser archive')
        with target.open('rb') as stream:
            manifest['sha256'][name] = hashlib.file_digest(stream, 'sha256').hexdigest()
    (folder / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')


def build(output):
    output.mkdir(parents=True, exist_ok=True)
    browser = output / 'browser'
    browser.mkdir(exist_ok=True)
    download_browser(browser)
    target = output / 'package.zip'
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, path in source_files(ROOT):
            archive.write(path, name)
        for path in sorted(browser.iterdir()):
            archive.write(path, 'browser/' + path.name, compress_type=zipfile.ZIP_STORED)
    with target.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if target.stat().st_size > 512 * 1024 * 1024:
        raise ValueError('Package exceeds bootstrap limit')
    manifest = {'sha256': digest, 'path': str(target.resolve()), 'chrome_version': CHROME_VERSION}
    (output / 'package.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps(manifest))


if __name__ == '__main__':
    cli = argparse.ArgumentParser()
    cli.add_argument('--output', type=Path, required=True)
    build(cli.parse_args().output)
