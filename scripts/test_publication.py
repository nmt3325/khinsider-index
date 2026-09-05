import copy
import json

import pytest

import build_library
import live_data
import publication
from live_test_helpers import catalogue, record, write_rows


def build(root):
    build_library.main(['--catalogue', str(root / 'catalogue.json'), '--meta', str(root / 'album-meta.ndjson'),
                        '--out', str(root / 'library.json'), '--gzip'])
    return json.loads((root / 'library.json').read_text())


def test_library_uses_only_complete_live_album_pages(tmp_path):
    catalogue(tmp_path, ['alpha', 'beta'])
    write_rows(tmp_path / 'album-meta.ndjson', [record('alpha'), record('beta')])
    payload = build(tmp_path)
    assert payload['album_count'] == payload['metadata_count'] == 2
    assert payload['complete'] and payload['legacy_inputs'] == []
    assert payload['coverage']['pending'] == 0
    assert payload['coverage']['sources']['album_list']['pages_swept'] == 1
    assert all('tracks' not in album for album in payload['albums'])
    assert payload['albums'][0]['platforms'] == ['Windows']
    assert payload['albums'][0]['publishers'] == []  # Do not fabricate absent optional fields.


def test_missing_metadata_does_not_overwrite_last_good_library(tmp_path):
    catalogue(tmp_path)
    write_rows(tmp_path / 'album-meta.ndjson', [record()])
    build(tmp_path)
    before = (tmp_path / 'library.json').read_bytes()
    catalogue(tmp_path, ['alpha', 'beta'])
    with pytest.raises(live_data.IncompleteData):
        build(tmp_path)
    assert (tmp_path / 'library.json').read_bytes() == before


def test_metadata_origin_is_mandatory_even_if_track_array_exists(tmp_path):
    catalogue(tmp_path)
    value = record()
    del value['data_source']
    write_rows(tmp_path / 'album-meta.ndjson', [value])
    with pytest.raises(live_data.DataError):
        build(tmp_path)


def test_library_signature_ignores_generation_timestamps(tmp_path):
    catalogue(tmp_path)
    write_rows(tmp_path / 'album-meta.ndjson', [record()])
    payload = build(tmp_path)
    other = copy.deepcopy(payload)
    other['generated_at'] = '2040-01-01T00:00:00Z'
    path = tmp_path / 'other.json'
    path.write_text(json.dumps(other))
    assert not publication.compare_library(str(tmp_path / 'library.json'), str(path))['changed']
    other['albums'][0]['title'] = 'Changed album title'
    path.write_text(json.dumps(other))
    assert publication.compare_library(str(tmp_path / 'library.json'), str(path))['changed']


def test_gzip_is_filename_and_timestamp_independent(tmp_path):
    first, second = tmp_path / 'first.gz', tmp_path / 'second.gz'
    build_library.write_gzip(first, '{"albums":[]}')
    build_library.write_gzip(second, '{"albums":[]}')
    assert first.read_bytes() == second.read_bytes()


def test_song_manifest_only_meaningful_hash_changes_trigger_publication(tmp_path):
    first, second = tmp_path / 'first.json', tmp_path / 'second.json'
    value = {'schema_version': 1, 'sha256': 'a' * 64, 'content_sha256': 'b' * 64, 'generated': 'old'}
    first.write_text(json.dumps(value))
    value['generated'] = 'new'
    second.write_text(json.dumps(value))
    assert not publication.compare_song_manifest(str(first), str(second))['changed']
    value['content_sha256'] = 'c' * 64
    second.write_text(json.dumps(value))
    assert publication.compare_song_manifest(str(first), str(second))['changed']


def test_schema_change_is_not_a_timestamp_only_change(tmp_path):
    first, second = tmp_path / 'first.json', tmp_path / 'second.json'
    first.write_text(json.dumps({'schema_version': 1, 'sha256': 'a' * 64}))
    second.write_text(json.dumps({'schema_version': 2, 'sha256': 'a' * 64}))
    assert publication.compare_song_manifest(str(first), str(second))['changed']
