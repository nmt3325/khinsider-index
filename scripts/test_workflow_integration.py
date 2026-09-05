"""Exercise workflow shell wiring without GitHub or KHInsider requests."""
import gzip
import json
import os
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent


def workflow(name):
    return yaml.load((ROOT / '.github/workflows' / name).read_text(), Loader=yaml.BaseLoader)


def step(name, title):
    return next(s for job in workflow(name)['jobs'].values() for s in job['steps'] if s.get('name') == title)


def execute(script, path, stubs, extra=None):
    bin_dir = path / 'bin'
    bin_dir.mkdir(exist_ok=True)
    for name, body in stubs.items():
        file = bin_dir / name
        file.write_text('#!/bin/sh\nset -eu\n' + body)
        file.chmod(0o755)
    result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', script], cwd=path,
                            env={**os.environ, 'PATH': f"{bin_dir}:{os.environ['PATH']}",
                                 'GITHUB_OUTPUT': str(path / 'outputs'),
                                 'GITHUB_STEP_SUMMARY': str(path / 'summary'), **(extra or {})},
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    return result


@pytest.mark.parametrize('with_library', [False, True])
def test_optional_library_argument(tmp_path, with_library):
    work = tmp_path / 'work'
    work.mkdir()
    (work / 'album-meta.ndjson.gz').touch()
    if with_library:
        (work / 'library.json').write_text('{}')
    execute(step('song-index.yaml', 'Build song index')['run'], tmp_path, {'python': '''
printf '%s\\n' "$@" > arguments
printf 'stub' > work/songs.tsv.gz
printf '{}' > work/songs-index.json
'''})
    args = (tmp_path / 'arguments').read_text().splitlines()
    assert ('--library' in args) is with_library
    assert '--metadata' in args


def test_checkpoint_preserves_staging_and_removes_only_obsolete_assets(tmp_path):
    (tmp_path / 'album-list.ndjson').write_text('last-good\n')
    (tmp_path / 'album-list-staging.pages').write_text('1\n')
    (tmp_path / 'album-list-staging.pages.gz').write_bytes(gzip.compress(b'old', mtime=0))
    (tmp_path / 'facet-publisher-staging.ndjson').write_text('partial\n')
    script = step('album-meta.yaml', 'Refresh the crawl-data snapshot')['run']
    execute(script, tmp_path, {'gh': '''
printf '%s\\n' "$*" >> gh-calls
case "$*" in
  *'--json assets'*) printf '%s\\n' album-list-staging.pages.gz facet-developer-staging.ndjson.gz unrelated.gz;;
esac
'''})
    calls = (tmp_path / 'gh-calls').read_text()
    upload = next(line for line in calls.splitlines() if 'release upload' in line)
    assert 'album-list-staging.pages.gz' in upload
    assert 'facet-publisher-staging.ndjson.gz' in upload
    assert '.gz.gz' not in upload
    deletes = [line for line in calls.splitlines() if 'delete-asset' in line]
    assert deletes == ['release delete-asset crawl-data facet-developer-staging.ndjson.gz --yes']
    assert gzip.decompress((tmp_path / 'album-list.ndjson.gz').read_bytes()) == b'last-good\n'


def test_manual_limits_and_resume_mode(tmp_path):
    doc = workflow('album-meta.yaml')
    inputs = doc['on']['workflow_dispatch']['inputs']
    for name in ('list_pages', 'publisher_limit', 'developer_limit'):
        assert inputs[name]['default'] == '0'
    assert inputs['full_reconcile']['default'] == 'true'
    script = step('album-meta.yaml', 'Decide whether this run must reconcile the full baseline')['run']
    script = script.replace('${{ steps.restore.outputs.bootstrap }}', 'false')
    script = script.replace('${{ github.event_name }}', 'schedule')
    for file in ('album-list.ndjson', 'facet-publisher.ndjson', 'facet-developer.ndjson',
                 'published-library.json', 'album-meta.ndjson',
                 'facet-publisher-stats.ndjson', 'facet-developer-stats.ndjson'):
        (tmp_path / file).write_text(json.dumps({'valid': True}))
    (tmp_path / 'album-list-staging.pages').write_text('1\n')
    execute(script, tmp_path, {'date': "printf '2\\n'\n"},
            {'FORCE_FULL': 'false', 'LIST_PAGES': '0', 'PUBLISHER_LIMIT': '0', 'DEVELOPER_LIMIT': '0'})
    output = (tmp_path / 'outputs').read_text()
    assert 'full=true' in output
    assert 'reason=resume' in output
