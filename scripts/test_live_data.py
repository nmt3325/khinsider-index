import copy
import json

import pytest

import live_data
from live_test_helpers import START, catalogue, record, write_rows


@pytest.mark.parametrize('slug', ['café', 'caf%C3%A9', '日本語', 'wándàn', '100%', 'a%2520b'])
def test_canonical_slug_is_idempotent(slug):
    value = live_data.canonical_slug(slug)
    assert live_data.canonical_slug(value) == value
    assert '/' not in value


@pytest.mark.parametrize('slug', ['', None, '.', '..', 'a/b', 'a%2Fb', 'a\\b', 'a%0Ab'])
def test_unsafe_slug_is_rejected(slug):
    with pytest.raises(live_data.DataError):
        live_data.canonical_slug(slug)


def test_catalogue_cannot_certify_a_partial_page_set(tmp_path):
    path = tmp_path / 'catalogue.json'
    catalogue(tmp_path)
    before = path.read_bytes()
    with pytest.raises(live_data.IncompleteData):
        live_data.write_catalogue(tmp_path / 'album-list.ndjson', path, 2, 2, START)
    assert path.read_bytes() == before


def test_advertised_count_and_fingerprint_are_checked(tmp_path):
    data = catalogue(tmp_path)
    with pytest.raises(live_data.IncompleteData):
        live_data.write_catalogue(tmp_path / 'album-list.ndjson', tmp_path / 'bad.json', 1, 2, START)
    data['albums'][0]['title'] = 'tampered'
    with pytest.raises(live_data.DataError, match='fingerprint'):
        live_data.read_catalogue(data)


@pytest.mark.parametrize('data', [{'entries': {'A': 'https://example.org/a'}}, {'albums': [{'slug': 'alpha'}]}])
def test_old_index_and_old_library_are_not_catalogues(data):
    with pytest.raises(live_data.DataError, match='legacy indexes'):
        live_data.read_catalogue(data)


@pytest.mark.parametrize('mutate', [
    lambda r: r.pop('data_source'),
    lambda r: r.update(tracks_complete=False),
    lambda r: r.update(track_count=2),
    lambda r: r.update(tracks=[]),
    lambda r: r['tracks'][0].update(title=''),
    lambda r: r['tracks'][0].update(num=True),
    lambda r: r.update(crawled_at='yesterday'),
])
def test_invalid_metadata_is_never_a_complete_album(mutate):
    value = record()
    mutate(value)
    with pytest.raises(live_data.DataError):
        live_data.validate_record(value)


def test_aliases_select_latest_complete_observation(tmp_path):
    catalogue(tmp_path, ['café'])
    old = record('caf%C3%A9', title='Old', when='2026-09-05T01:00:00Z')
    new = record('café', title='New', count=2)
    write_rows(tmp_path / 'album-meta.ndjson', [old, new])
    _, selected, gone, pending, summary = live_data.require_complete(tmp_path / 'catalogue.json', tmp_path / 'album-meta.ndjson')
    assert list(selected) == ['caf%C3%A9']
    assert selected['caf%C3%A9']['_line'] == 2
    assert summary['tracks'] == 2 and not gone and not pending


def test_later_incomplete_record_cannot_fall_back_to_older_tracks(tmp_path):
    catalogue(tmp_path)
    broken = copy.deepcopy(record())
    broken['tracks'].append({})
    write_rows(tmp_path / 'album-meta.ndjson', [record(), broken])
    with pytest.raises(live_data.DataError):
        live_data.require_complete(tmp_path / 'catalogue.json', tmp_path / 'album-meta.ndjson')


def test_fresh_404_is_explicit_not_silently_fetched(tmp_path):
    catalogue(tmp_path, ['alpha', 'gone'])
    gone = {'data_source': live_data.SOURCE, 'slug': 'gone', 'status': 'gone',
            'http_status': 404, 'crawled_at': '2026-09-05T13:00:00Z'}
    write_rows(tmp_path / 'album-meta.ndjson', [record(), gone])
    summary = live_data.require_complete(tmp_path / 'catalogue.json', tmp_path / 'album-meta.ndjson')[-1]
    assert (summary['total'], summary['fetched'], summary['unavailable'], summary['pending']) == (2, 1, 1, 0)
    gone['crawled_at'] = '2026-09-04T00:00:00Z'
    write_rows(tmp_path / 'album-meta.ndjson', [record(), gone])
    assert live_data.inspect(tmp_path / 'catalogue.json', tmp_path / 'album-meta.ndjson')[-1]['pending'] == 1


def test_recent_change_requires_later_metadata(tmp_path):
    catalogue(tmp_path)
    write_rows(tmp_path / 'album-meta.ndjson', [record(when='2026-09-05T01:00:00Z')])
    state = tmp_path / 'recent-state.json'
    state.write_text(json.dumps({'pending': {'event': {'slug': 'alpha', 'discovered_at': '2026-09-05T02:00:00Z'}}}))
    with pytest.raises(live_data.IncompleteData):
        live_data.require_complete(tmp_path / 'catalogue.json', tmp_path / 'album-meta.ndjson', state)
    write_rows(tmp_path / 'album-meta.ndjson', [record()])
    assert live_data.require_complete(tmp_path / 'catalogue.json', tmp_path / 'album-meta.ndjson', state)[-1]['complete']
