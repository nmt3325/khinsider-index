"""Incremental discovery, complete accumulated releases, and failure retention."""
import gzip
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import album_meta
import crawl_album_meta
import live_data
import live_pipeline as pipeline
from live_test_helpers import ready, record, write_rows

EVENT = '2026-09-08T00:00:00Z'
AFTER = '2026-09-08T00:00:01Z'


def event(state, slug, when=EVENT):
    path = state / 'recent-state.json'
    recent = json.loads(path.read_text())
    row = {'slug': slug, 'title': slug, 'listed_at': when[:10], 'discovered_at': when}
    recent['pending'][when + '\t' + slug] = dict(row, row=row)
    live_data.atomic_json(path, recent)
    with (state / 'recent-albums.ndjson').open('a') as stream:
        stream.write(json.dumps(row) + '\n')


def snapshot(state):
    return live_data.inspect_cumulative(state / 'catalogue.json', state / 'album-meta.ndjson',
                                        state / 'recent-state.json')


def test_missing_new_album_does_not_remove_accumulated_outside_seed(tmp_path):
    state = ready(tmp_path / 'state')
    write_rows(state / 'album-meta.ndjson', [record(), record('outside')])
    event(state, 'new')
    catalogue, selected, gone, pending, summary = snapshot(state)
    assert set(selected) == {'alpha', 'outside'}
    assert pending == ['new'] and gone == []
    assert len(catalogue['albums']) == 3
    assert summary['tracks'] == 2 and summary['snapshot_complete']
    assert not summary['complete'] and summary['missing_albums'] == 1
    manifest = pipeline.build_outputs(state, tmp_path / 'output')
    library = json.loads((tmp_path / 'output/library.json').read_text())
    assert {row['slug'] for row in library['albums']} == {'alpha', 'outside'}
    assert library['complete'] and not library['crawl_complete']
    assert library['coverage']['pending'] == 1
    assert manifest['songs'] == 2 and manifest['pending_albums'] == 1
    assert manifest['completeness_scope'] == 'cumulative_snapshot'
    with gzip.open(tmp_path / 'output/songs.tsv.gz', 'rt') as stream:
        assert {line.split('\t')[0] for line in stream} == {'alpha', 'outside'}


def test_failed_refresh_keeps_last_good_and_is_not_retried_in_a_loop(monkeypatch, tmp_path):
    state = ready(tmp_path / 'state')
    before = (state / 'album-meta.ndjson').read_bytes()
    event(state, 'alpha')
    calls = []

    def script(name, args):
        assert name != 'crawl_index_pages.py'
        if name == 'crawl_album_meta.py':
            calls.append(name)
            with (state / 'album-meta-failures.log').open('a') as stream:
                stream.write(f'alpha\tHTTP 503\talpha\t{AFTER}\n')
        return 0

    monkeypatch.setattr(pipeline, 'script', script)
    pipeline.run(state, 'owner/repo', mode='refresh', minutes=0.1)
    assert calls == ['crawl_album_meta.py']
    assert (state / 'album-meta.ndjson').read_bytes() == before
    summary = snapshot(state)[-1]
    assert summary['retained_last_good'] == 1 and summary['fetched'] == 1
    assert json.loads((tmp_path / 'live-output/songs-index.json').read_text())['songs'] == 1
    retry = pipeline.read_retry_state(json.loads((state / 'discovery.json').read_text()))
    assert retry['alpha']['attempts'] == 1
    assert pipeline.due_targets(state, ['alpha'], retry, set(), now=live_data.stamp(AFTER)) == []


def test_new_discovery_is_fetched_immediately_without_a_full_listing(monkeypatch, tmp_path):
    state = ready(tmp_path / 'state')
    seed = (state / 'catalogue.json').read_bytes()
    queues = []

    def script(name, args):
        assert name != 'crawl_index_pages.py'
        if name == 'crawl_recent.py' and '--ack-only' not in args:
            event(state, 'new')
        if name == 'crawl_album_meta.py':
            queue = Path(args[args.index('--slugs-file') + 1]).read_text().splitlines()
            queues.append(queue)
            with (state / 'album-meta.ndjson').open('a') as stream:
                stream.write(json.dumps(record('new', when=AFTER)) + '\n')
        return 0

    monkeypatch.setattr(pipeline, 'script', script)
    pipeline.run(state, 'owner/repo', mode='incremental', minutes=0.1)
    assert queues == [['new']]
    assert (state / 'catalogue.json').read_bytes() == seed
    assert snapshot(state)[-1]['fetched'] == 2
    assert json.loads((tmp_path / 'live-output/songs-index.json').read_text())['songs'] == 2


def test_redirected_metadata_is_not_duplicated_or_published_under_the_dead_alias(tmp_path):
    state = ready(tmp_path / 'state', ['old', 'canonical'])
    redirected = record('canonical', when=AFTER)
    redirected.update(slug='old', resolved_slug='canonical')
    write_rows(state / 'album-meta.ndjson', [record('canonical'), redirected])
    catalogue, selected, _, pending, summary = snapshot(state)
    assert set(selected) == {'canonical'} and pending == []
    assert catalogue['aliases'] == {'old': 'canonical'}
    assert summary['alias_count'] == 1 and summary['total'] == 1
    pipeline.build_outputs(state, tmp_path / 'output')
    with gzip.open(tmp_path / 'output/songs.tsv.gz', 'rt') as stream:
        rows = stream.readlines()
    assert len(rows) == 1 and rows[0].startswith('canonical\t')


def test_newer_observed_terminal_url_supersedes_a_stale_redirect(tmp_path):
    state = ready(tmp_path / 'state', ['alpha', 'beta'])
    first = record('beta', when=EVENT)
    first.update(slug='alpha', resolved_slug='beta')
    second = record('alpha', when=AFTER)
    second.update(slug='beta', resolved_slug='alpha')
    write_rows(state / 'album-meta.ndjson', [first, second])
    result = snapshot(state)
    assert result[0]['aliases'] == {'beta': 'alpha'}
    assert set(result[1]) == {'alpha'}
    pipeline.build_outputs(state, tmp_path / 'output')


def test_zero_disc_is_preserved_in_the_full_tsv(tmp_path):
    state = ready(tmp_path / 'state')
    item = record()
    item['tracks'][0]['disc'] = 0
    live_data.validate_record(item)
    write_rows(state / 'album-meta.ndjson', [item])
    pipeline.build_outputs(state, tmp_path / 'output')
    with gzip.open(tmp_path / 'output/songs.tsv.gz', 'rt') as stream:
        assert stream.readline().split('\t')[1] == '0'


@pytest.mark.parametrize('field,value', [('disc', -1), ('disc', True), ('num', 0), ('num', False)])
def test_number_validation_is_not_disabled(field, value):
    item = record()
    item['tracks'][0][field] = value
    with pytest.raises(live_data.DataError):
        live_data.validate_record(item)


def test_published_album_cannot_disappear_from_the_next_full_snapshot(tmp_path):
    state = ready(tmp_path / 'state')
    live_data.atomic_json(state / 'last-published.json', {
        'data_source': live_data.SOURCE, 'tag': 'local-test', 'published_albums': ['alpha', 'lost'],
    })
    with pytest.raises(live_data.DataError, match='previously published'):
        pipeline.progress(state)


def test_missing_published_seed_never_starts_a_new_full_sweep(monkeypatch, tmp_path):
    state = ready(tmp_path / 'state')
    (state / 'catalogue.json').unlink()
    live_data.atomic_json(state / 'last-published.json', {'data_source': live_data.SOURCE, 'tag': 'local-test'})
    monkeypatch.setattr(pipeline, 'script', lambda *a: pytest.fail('unexpected bootstrap'))
    with pytest.raises(live_data.DataError, match='lost its seed'):
        pipeline.run(state, 'owner/repo')


def test_retry_backoff_is_bounded_and_new_changes_take_priority(tmp_path):
    state = ready(tmp_path / 'state')
    retry = {}
    log = state / 'album-meta-failures.log'
    for attempt in range(8):
        offset = pipeline.file_size(log)
        with log.open('a') as stream:
            stream.write(f'alpha\tHTTP 503\talpha\t{EVENT}\n')
        assert pipeline.collect_attempts(state, {'album-meta-failures.log': offset}, retry) == {'alpha'}
    delay = live_data.stamp(retry['alpha']['next_retry_at']) - live_data.stamp(EVENT)
    assert delay == 24 * 3600
    assert pipeline.due_targets(state, ['alpha', 'bulk'], retry, set(), now=live_data.stamp(AFTER)) == ['bulk']
    event(state, 'alpha', AFTER)
    assert pipeline.due_targets(state, ['bulk', 'alpha'], retry, set(), now=live_data.stamp(AFTER))[0] == 'alpha'
    assert pipeline.due_targets(state, ['alpha'], retry, {'alpha'}, now=live_data.stamp(AFTER)) == []


def test_retry_state_roundtrips_with_the_existing_checkpoint(tmp_path):
    state = ready(tmp_path / 'state')
    discovery = json.loads((state / 'discovery.json').read_text())
    discovery['retry'] = {'alpha': {'attempts': 1, 'last_attempt': EVENT, 'next_retry_at': AFTER}}
    live_data.atomic_json(state / 'discovery.json', discovery)
    pipeline.pack_checkpoint(state, tmp_path / 'checkpoint.tar.gz')
    pipeline.unpack_checkpoint(tmp_path / 'checkpoint.tar.gz', tmp_path / 'restored')
    assert json.loads((tmp_path / 'restored/discovery.json').read_text())['retry'] == discovery['retry']


def test_unchanged_receipt_skips_both_building_and_uploading(monkeypatch, tmp_path):
    state = ready(tmp_path / 'state')
    summary = snapshot(state)[-1]
    live_data.atomic_json(state / 'last-published.json', {
        'data_source': live_data.SOURCE, 'tag': 'local-test', 'published_albums': ['alpha'],
        'input_signature': {'metadata_sha256': live_data.digest_file(state / 'album-meta.ndjson'),
                            'catalogue_id': summary['catalogue_id']},
    })
    monkeypatch.setattr(pipeline, 'release_info', lambda *a: {'isDraft': False})
    monkeypatch.setattr(pipeline, 'build_outputs', lambda *a: pytest.fail('unnecessary full rebuild'))
    monkeypatch.setattr(pipeline, 'gh', lambda *a: pytest.fail('unnecessary remote mutation'))
    assert not pipeline.run(state, 'owner/repo', mode='build', do_publish=True)
    assert json.loads((state / 'progress.json').read_text())['phase'] == 'unchanged'


@pytest.mark.parametrize('previous,new,blocked', [(100, 20, True), (100, 101, False)])
def test_initial_cutover_checks_only_the_previous_manifest_count(monkeypatch, tmp_path, previous, new, blocked):
    state = ready(tmp_path / 'state')
    monkeypatch.setattr(pipeline, 'release_info', lambda *a: {'assets': [{'name': 'songs-index.json'}]})
    calls = []

    def gh(repo, *args):
        calls.append(args)
        path = Path(args[args.index('--dir') + 1]) / 'songs-index.json'
        path.write_text(json.dumps({'songs': previous}))

    monkeypatch.setattr(pipeline, 'gh', gh)
    if blocked:
        with pytest.raises(live_data.IncompleteData, match='smaller'):
            pipeline.verify_initial_size(state, 'owner/repo', {'songs': new})
    else:
        pipeline.verify_initial_size(state, 'owner/repo', {'songs': new})
    assert len(calls) == 1
    assert calls[0][:4] == ('download', 'song-index', '--pattern', 'songs-index.json')


@pytest.mark.parametrize('title,challenge', [
    ('Toho just a moment! (2009) MP3 - Download Soundtracks for FREE!', False),
    ('Just a moment...', True), ('Attention Required! | Cloudflare', True),
])
def test_cloudflare_detection_does_not_reject_a_real_album_title(monkeypatch, title, challenge):
    response = SimpleNamespace(status_code=200, text=f'<html><title>{title}</title></html>')
    monkeypatch.setattr(crawl_album_meta, 'session', lambda: SimpleNamespace(get=lambda *a, **k: response))
    monkeypatch.setattr(crawl_album_meta.time, 'sleep', lambda *a: None)
    result = crawl_album_meta.fetch('toho-just-a-moment-2009', retries=1, delay=0, jitter=0)
    assert (result[0] is None) is challenge
    assert result[1] == ('cloudflare' if challenge else 200)


@pytest.mark.parametrize('basename', ['very-long-name.mp', 'very-long-name-without-extension', 'a; b.mp3'])
def test_observed_songid_rows_do_not_require_an_intact_filename_extension(basename):
    from bs4 import BeautifulSoup
    html = f"""<div id="pageContent"><h2>Album</h2><table id="songlist">
    <tr id="songlist_header"><th></th><th>#</th><th>Song Name</th><th>MP3</th></tr>
    <tr><td></td><td>1.</td><td><a href="/game-soundtracks/album/alpha/{basename}">Song</a></td>
    <td>1:00</td><td>1 MB</td><td><div class="playlistAddTo" songid="123"></div></td></tr>
    </table></div>"""
    tracks = album_meta.parse_songlist(BeautifulSoup(html, 'html.parser'), 'alpha')
    assert len(tracks) == 1 and tracks[0]['basename'] == basename
    assert tracks[0]['songid'] == '123' and tracks[0]['title'] == 'Song'


def test_unchanged_content_remembers_new_observation_inputs(monkeypatch, tmp_path):
    state = ready(tmp_path / 'state')
    output = tmp_path / 'output'
    manifest = pipeline.build_outputs(state, output)
    receipt = {'data_source': live_data.SOURCE, 'tag': 'local-test',
               'signature': pipeline.publication_signature(manifest),
               'published_albums': ['alpha'], 'published_at': EVENT,
               'input_signature': {'metadata_sha256': '0' * 64,
                                   'catalogue_id': manifest['catalogue_id']}}
    live_data.atomic_json(state / 'last-published.json', receipt)
    monkeypatch.setattr(pipeline, 'release_info', lambda *a: {'isDraft': False})
    monkeypatch.setattr(pipeline, 'gh', lambda *a: pytest.fail('unexpected release mutation'))
    assert not pipeline.publish(state, output, 'owner/repo', manifest)
    saved = json.loads((state / 'last-published.json').read_text())
    assert saved['input_signature']['metadata_sha256'] == manifest['metadata_sha256']
    assert saved['published_at'] == EVENT
    monkeypatch.setattr(pipeline, 'build_outputs', lambda *a: pytest.fail('unnecessary repeated rebuild'))
    assert not pipeline.run(state, 'owner/repo', mode='build', do_publish=True)


@pytest.mark.parametrize('final_url,valid', [
    ('https://downloads.khinsider.com/game-soundtracks/album/canonical', True),
    ('https://downloads.khinsider.com/', False),
    ('https://example.com/game-soundtracks/album/canonical', False),
])
def test_crawler_uses_only_a_trusted_final_album_identity(monkeypatch, tmp_path, final_url, valid):
    html = """<div id="pageContent"><h2>Canonical Album</h2><table id="songlist">
    <tr id="songlist_header"><th></th><th>#</th><th>Song Name</th><th>MP3</th></tr>
    <tr><td></td><td>1.</td><td><a href="/game-soundtracks/album/canonical/song.mp3">Song</a></td>
    <td>1:00</td><td>1 MB</td><td><div class="playlistAddTo" songid="123"></div></td></tr>
    </table></div>"""
    monkeypatch.setattr(crawl_album_meta, 'fetch', lambda *a, **k: (html, 200, final_url))
    queue, metadata, failures = (tmp_path / name for name in ('queue.txt', 'metadata.ndjson', 'failures.log'))
    queue.write_text('old-alias\n')
    crawl_album_meta.main(['--slugs-file', str(queue), '--out', str(metadata),
                          '--failures', str(failures), '--refresh', '--workers', '1',
                          '--delay', '0', '--jitter', '0', '--retries', '1'])
    records = [json.loads(line) for line in metadata.read_text().splitlines()]
    if valid:
        assert len(records) == 1
        assert records[0]['slug'] == 'old-alias'
        assert records[0]['resolved_slug'] == 'canonical'
        assert records[0]['tracks'][0]['title'] == 'Song'
        live_data.validate_record(records[0])
    else:
        assert records == []
        assert 'redirected outside an album page' in failures.read_text()


def test_recent_discovery_does_not_erase_known_seed_metadata(tmp_path):
    state = ready(tmp_path / 'state')
    event(state, 'alpha')
    catalogue, _, _, _, summary = snapshot(state)
    album = next(row for row in catalogue['albums'] if row['slug'] == 'alpha')
    assert album['year'] == 2026 and album['platforms'] == ['Windows']
    assert summary['retained_last_good'] == 1
