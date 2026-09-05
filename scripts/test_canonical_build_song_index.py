import gzip
import json

import pytest

import build_song_index
import live_data
from live_test_helpers import catalogue, record, write_rows


def build(root, output='songs.tsv.gz'):
    return build_song_index.main(['--catalogue', str(root / 'catalogue.json'),
                                 '--metadata', str(root / 'album-meta.ndjson'),
                                 '--out', str(root / output), '--manifest', str(root / 'songs-index.json')])


def lines(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        return stream.read().splitlines()


def test_builds_without_any_legacy_file_and_is_deterministic(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    catalogue(tmp_path)
    write_rows(tmp_path / 'album-meta.ndjson', [record(title='New\tTitle')])
    first = build(tmp_path)
    second = build(tmp_path, 'again.tsv.gz')
    assert lines(tmp_path / 'songs.tsv.gz') == ['alpha\t1\t1\tNew Title']
    assert (tmp_path / 'songs.tsv.gz').read_bytes() == (tmp_path / 'again.tsv.gz').read_bytes()
    assert first['sha256'] == second['sha256']
    assert first['schema_version'] == 1 and first['dataset_schema_version'] == 2
    assert first['legacy_inputs'] == [] and first['complete'] is True


def test_legacy_files_cannot_supply_missing_albums(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    catalogue(tmp_path, ['alpha', 'beta'])
    write_rows(tmp_path / 'album-meta.ndjson', [record()])
    for name in ('index.json', 'songs_cached.jsonl.gz', 'crawl_state_snapshot.tar.gz'):
        (tmp_path / name).write_text('legacy material must never be opened')
    with pytest.raises(live_data.IncompleteData):
        build(tmp_path)
    assert not (tmp_path / 'songs.tsv.gz').exists()


@pytest.mark.parametrize('flag', ['--cached', '--snapshot', '--allow-partial'])
def test_legacy_fallback_flags_have_been_removed(flag):
    with pytest.raises(SystemExit):
        build_song_index.main([flag, 'legacy'])


def test_equal_track_labels_are_not_collapsed(tmp_path):
    catalogue(tmp_path)
    value = record(count=2)
    value['tracks'][1]['num'] = 1
    write_rows(tmp_path / 'album-meta.ndjson', [value])
    result = build(tmp_path)
    assert result['songs'] == 2
    assert lines(tmp_path / 'songs.tsv.gz') == ['alpha\t1\t1\tTrack'] * 2


def test_newest_alias_replaces_tracks_without_a_union(tmp_path):
    catalogue(tmp_path, ['café'])
    write_rows(tmp_path / 'album-meta.ndjson', [
        record('caf%C3%A9', title='Old', when='2026-09-05T01:00:00Z', count=3),
        record('café', title='Current'),
    ])
    result = build(tmp_path)
    assert result['songs'] == 1 and result['albums'] == 1
    assert lines(tmp_path / 'songs.tsv.gz') == ['caf%C3%A9\t1\t1\tCurrent']


@pytest.mark.parametrize('payload', ['{broken\n', '"not an object"\n', json.dumps({'slug': 'alpha'}) + '\n'])
def test_invalid_input_preserves_last_good_outputs(tmp_path, payload):
    catalogue(tmp_path)
    write_rows(tmp_path / 'album-meta.ndjson', [record()])
    build(tmp_path)
    before = [(tmp_path / name).read_bytes() for name in ('songs.tsv.gz', 'songs-index.json')]
    (tmp_path / 'album-meta.ndjson').write_text(payload)
    with pytest.raises(live_data.DataError):
        build(tmp_path)
    assert [(tmp_path / name).read_bytes() for name in ('songs.tsv.gz', 'songs-index.json')] == before


def test_unavailable_albums_are_in_the_manifest(tmp_path):
    catalogue(tmp_path, ['alpha', 'gone'])
    write_rows(tmp_path / 'album-meta.ndjson', [record(), {
        'slug': 'gone', 'status': 'gone', 'http_status': 404,
        'data_source': live_data.SOURCE, 'crawled_at': '2026-09-05T13:00:00Z',
    }])
    result = build(tmp_path)
    assert result['unavailable_albums'] == ['gone']
    assert result['listed_albums'] == 2 and result['albums'] == 1
