import io
import json
from pathlib import Path
from types import SimpleNamespace
import tarfile

import pytest

import live_data
import live_pipeline as pipeline
from live_test_helpers import catalogue, ready, record, write_rows


def test_checkpoint_roundtrip_does_not_include_legacy_files(tmp_path):
    source = ready(tmp_path / 'source')
    (source / 'index.json').write_text('not an input')
    archive = tmp_path / 'checkpoint.tar.gz'
    pipeline.pack_checkpoint(source, archive)
    with tarfile.open(archive) as stream:
        assert 'index.json' not in stream.getnames()
        descriptor = json.load(stream.extractfile('checkpoint.json'))
        assert descriptor['data_source'] == live_data.SOURCE
    target = tmp_path / 'restored'
    pipeline.unpack_checkpoint(archive, target)
    assert (target / 'album-meta.ndjson').read_bytes() == (source / 'album-meta.ndjson').read_bytes()
    assert pipeline.progress(target)[1]['ready_for_publish']


def test_restore_cannot_overwrite_existing_state(tmp_path):
    source = ready(tmp_path / 'source')
    archive = tmp_path / 'checkpoint.tar.gz'
    pipeline.pack_checkpoint(source, archive)
    before = (source / 'album-meta.ndjson').read_bytes()
    with pytest.raises(live_data.DataError, match='nonempty'):
        pipeline.unpack_checkpoint(archive, source)
    assert (source / 'album-meta.ndjson').read_bytes() == before


@pytest.mark.parametrize('kind', ['foreign', 'hash', 'path', 'symlink'])
def test_checkpoint_rejects_foreign_corrupt_and_unsafe_archives(tmp_path, kind):
    archive = tmp_path / 'bad.tar.gz'
    descriptor = {'data_source': live_data.SOURCE, 'schema_version': 2, 'files': {}}
    if kind == 'foreign':
        descriptor['data_source'] = 'old-crawl'
    if kind == 'hash':
        descriptor['files']['album-meta.ndjson'] = {'sha256': '0' * 64, 'size': 2}
    with tarfile.open(archive, 'w:gz') as stream:
        raw = json.dumps(descriptor).encode()
        info = tarfile.TarInfo('checkpoint.json')
        info.size = len(raw)
        stream.addfile(info, io.BytesIO(raw))
        if kind == 'hash':
            info = tarfile.TarInfo('album-meta.ndjson')
            info.size = 2
            stream.addfile(info, io.BytesIO(b'{}'))
        elif kind in ('path', 'symlink'):
            info = tarfile.TarInfo('../escape' if kind == 'path' else 'album-meta.ndjson')
            if kind == 'symlink':
                info.type, info.linkname = tarfile.SYMTYPE, '../escape'
            stream.addfile(info)
    with pytest.raises(live_data.DataError):
        pipeline.unpack_checkpoint(archive, tmp_path / 'target')
    assert not (tmp_path / 'escape').exists()
    assert not (tmp_path / 'target').exists()


@pytest.mark.parametrize('error', ['gh: HTTP 503', 'HTTP 403', 'authentication failed', 'connection reset'])
def test_restore_errors_do_not_become_successful_bootstrap(monkeypatch, tmp_path, error):
    monkeypatch.setattr(pipeline, 'gh', lambda *a, **k: SimpleNamespace(returncode=1, stderr=error))
    with pytest.raises(live_data.DataError):
        pipeline.restore(tmp_path / 'state', 'owner/repo')
    assert not (tmp_path / 'state').exists()


def test_only_missing_release_is_a_bootstrap(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, 'gh', lambda *a, **k: SimpleNamespace(returncode=1, stderr='gh: Not Found (HTTP 404)'))
    pipeline.restore(tmp_path / 'state', 'owner/repo')
    assert list((tmp_path / 'state').iterdir()) == []


def test_missing_asset_on_existing_release_is_not_a_bootstrap(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, 'release_info', lambda *a: {'body': pipeline.MARKER, 'assets': []})
    with pytest.raises(live_data.DataError, match='missing its checkpoint'):
        pipeline.restore(tmp_path / 'state', 'owner/repo')


def test_partial_listing_cannot_build_or_publish(monkeypatch, tmp_path):
    state = tmp_path / 'state'
    called = []
    monkeypatch.setattr(pipeline, 'script', lambda name, args: called.append(name) or 1)
    monkeypatch.setattr(pipeline, 'publish', lambda *a: pytest.fail('partial data was published'))
    assert not pipeline.run(state, 'owner/repo', minutes=0.1, do_publish=True)
    assert called == ['crawl_index_pages.py']
    summary = json.loads((state / 'progress.json').read_text())
    assert summary['phase'] == 'listing' and not summary['ready_for_publish']
    assert not (tmp_path / 'live-output').exists()


def test_cold_start_collects_both_outputs_without_legacy_files(monkeypatch, tmp_path):
    state = tmp_path / 'state'
    calls = []

    def fake_script(name, args):
        calls.append((name, list(map(str, args))))
        if name == 'crawl_index_pages.py':
            catalogue(state, ['alpha', 'beta'])
        elif name == 'crawl_album_meta.py':
            queue = Path(args[args.index('--slugs-file') + 1])
            assert set(queue.read_text().splitlines()) == {'alpha', 'beta'}
            write_rows(state / 'album-meta.ndjson', [record('alpha'), record('beta')])
        return 0

    monkeypatch.setattr(pipeline, 'script', fake_script)
    assert not pipeline.run(state, 'owner/repo', minutes=0.1)
    assert pipeline.progress(state)[1]['ready_for_publish']
    library = json.loads((tmp_path / 'live-output/library.json').read_text())
    songs = json.loads((tmp_path / 'live-output/songs-index.json').read_text())
    assert library['album_count'] == songs['albums'] == songs['songs'] == 2
    assert library['legacy_inputs'] == songs['legacy_inputs'] == []
    assert library['catalogue_id'] == songs['catalogue_id']
    assert not (state / 'index.json').exists()
    assert calls[0][0] == 'crawl_index_pages.py'


def test_resume_only_fetches_missing_modern_records(monkeypatch, tmp_path):
    state = ready(tmp_path / 'state', ['alpha', 'beta'])
    write_rows(state / 'album-meta.ndjson', [record('alpha')])
    crawls = []

    def fake_script(name, args):
        assert name != 'crawl_index_pages.py'
        if name == 'crawl_album_meta.py':
            queue = Path(args[args.index('--slugs-file') + 1])
            crawls.extend(queue.read_text().splitlines())
            write_rows(state / 'album-meta.ndjson', [record('alpha'), record('beta')])
        return 0

    monkeypatch.setattr(pipeline, 'script', fake_script)
    pipeline.run(state, 'owner/repo', minutes=0.1)
    assert crawls == ['beta']


def test_no_progress_stops_retry_loop(monkeypatch, tmp_path):
    state = ready(tmp_path / 'state')
    (state / 'album-meta.ndjson').unlink()
    names = []
    monkeypatch.setattr(pipeline, 'script', lambda name, args: names.append(name) or 0)
    pipeline.run(state, 'owner/repo', minutes=1)
    assert names.count('crawl_album_meta.py') == 1
    assert not (tmp_path / 'live-output').exists()


def test_output_tampering_blocks_every_remote_mutation(monkeypatch, tmp_path):
    state = ready(tmp_path / 'state')
    output = tmp_path / 'output'
    manifest = pipeline.build_outputs(state, output)
    (output / 'songs.tsv.gz').write_bytes(b'corrupt')
    monkeypatch.setattr(pipeline, 'gh', lambda *a, **k: pytest.fail('unexpected remote mutation'))
    with pytest.raises(live_data.DataError, match='do not match'):
        pipeline.publish(state, output, 'owner/repo', manifest)


def test_publication_upload_failure_does_not_promote_library(monkeypatch, tmp_path):
    state = ready(tmp_path / 'state')
    output = tmp_path / 'output'
    manifest = pipeline.build_outputs(state, output)
    calls = []
    monkeypatch.setattr(pipeline, 'release_info', lambda *a: None)

    def fake_gh(repo, *args, **kwargs):
        calls.append(tuple(map(str, args)))
        if args[0:2] == ('upload', 'song-index') and str(args[2]).endswith('songs-index.json'):
            raise live_data.DataError('HTTP 503')
        return SimpleNamespace(returncode=0, stdout='', stderr='')

    monkeypatch.setattr(pipeline, 'gh', fake_gh)
    with pytest.raises(live_data.DataError, match='503'):
        pipeline.publish(state, output, 'owner/repo', manifest)
    assert not any('--latest' in call for call in calls)
    assert not (state / 'last-published.json').exists()


def test_unchanged_complete_generation_does_not_upload_again(monkeypatch, tmp_path):
    state = ready(tmp_path / 'state')
    output = tmp_path / 'output'
    manifest = pipeline.build_outputs(state, output)
    live_data.atomic_json(state / 'last-published.json', {
        'data_source': live_data.SOURCE, 'tag': 'library-live-v2-test',
        'signature': pipeline.publication_signature(manifest),
    })
    monkeypatch.setattr(pipeline, 'release_info', lambda *a: {'isDraft': False})
    monkeypatch.setattr(pipeline, 'gh', lambda *a, **k: pytest.fail('unchanged dataset was uploaded'))
    assert pipeline.publish(state, output, 'owner/repo', manifest) is False
