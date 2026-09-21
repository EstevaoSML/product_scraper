"""Publish exactly the images retained by a successful container CI run."""
import json
from pathlib import Path
import re
import subprocess
import sys


def images_from_report(report):
    if report.get('passed') is not True:
        raise ValueError('Container CI must pass before publishing')
    project = report.get('project', '')
    if not re.fullmatch(r'scraper-ci-[a-f0-9]{12}', project):
        raise ValueError('Invalid CI project')
    expected = {'api': project + ':local', 'browser': project + '-browser:local'}
    if report.get('images') != expected:
        raise ValueError('Report does not identify the tested images')
    return expected


def publish(registry, tag, report):
    if not re.fullmatch(r'[a-z0-9]+\.azurecr\.io', registry) or not re.fullmatch(r'ci-[0-9]+-[a-f0-9]{40}', tag):
        raise ValueError('Invalid registry or immutable release tag')
    for kind, image in images_from_report(report).items():
        repository = 'html-scraper' if kind == 'api' else 'html-scraper-browser'
        target = f'{registry}/{repository}:{tag}'
        subprocess.run(['docker', 'tag', image, target], check=True, timeout=30)
        subprocess.run(['docker', 'push', target], check=True, timeout=600)
        subprocess.run(['az', 'acr', 'repository', 'update', '--name', registry.split('.')[0],
                        '--image', f'{repository}:{tag}', '--write-enabled', 'false', '--delete-enabled', 'false',
                        '--output', 'none'], check=True, timeout=60)


if __name__ == '__main__':
    publish(sys.argv[1], sys.argv[2], json.loads(Path('reports/container-result.json').read_text(encoding='utf-8')))
