import gzip
import io
import json
import tarfile

import pytest

import live_data
import live_pipeline
from live_test_helpers import START, catalogue, ready, record, write_rows


@pytest.mark.parametrize('key,value', [('crawled_at', None), ('crawled_at', 'bad'), ('page', 0), ('page', True)])
def test_every_catalogue_row_is_validated_on_read_and_write(tmp_path, key, value):
    original = catalogue(tmp_path)
    original['albums'][0][key] = value
    with pytest.raises(live_data.DataError):
        live_data.read_catalogue(original)
    write_rows(tmp_path / 'bad.ndjson', original['albums'])
    with pytest.raises(live_data.DataError):
        live_data.write_catalogue(tmp_path / 'bad.ndjson', tmp_path / 'bad.json', 1, 1, START)
    assert not (tmp_path / 'bad.json').exists()


def test_resumed_listing_retains_earliest_observation_time(tmp_path):
    catalogue(tmp_path)
    value = live_data.write_catalogue(tmp_path / 'album-list.ndjson', tmp_path / 'catalogue.json',
                                     1, 1, '2026-09-05T13:00:00Z')
    assert value['started_at'] == START


def test_empty_checkpoint_descriptor_is_not_a_successful_restore(tmp_path):
    path = tmp_path / 'checkpoint.tar.gz'
    raw = json.dumps({'data_source': live_data.SOURCE, 'schema_version': 2, 'files': {}}).encode()
    with tarfile.open(path, 'w:gz') as archive:
        info = tarfile.TarInfo('checkpoint.json')
        info.size = len(raw)
        archive.addfile(info, io.BytesIO(raw))
    with pytest.raises(live_data.DataError):
        live_pipeline.unpack_checkpoint(path, tmp_path / 'restored')
    assert not (tmp_path / 'restored').exists()


def test_oversized_checkpoint_preserves_existing_archive(monkeypatch, tmp_path):
    state = ready(tmp_path / 'state')
    archive = tmp_path / 'checkpoint.tar.gz'
    archive.write_bytes(b'last good')
    monkeypatch.setattr(live_pipeline, 'MAX_CHECKPOINT_BYTES', 1)
    with pytest.raises(live_data.DataError, match='size limit'):
        live_pipeline.pack_checkpoint(state, archive)
    assert archive.read_bytes() == b'last good'


@pytest.mark.parametrize('kind', ['manifest', 'library-marker', 'library-gzip'])
def test_artifact_files_must_match_the_validated_generation(monkeypatch, tmp_path, kind):
    state = ready(tmp_path / 'state')
    output = tmp_path / 'output'
    manifest = live_pipeline.build_outputs(state, output)
    if kind == 'manifest':
        value = dict(manifest, complete=False)
        live_data.atomic_json(output / 'songs-index.json', value)
    elif kind == 'library-marker':
        value = json.loads((output / 'library.json').read_text())
        value['data_source'] = 'legacy'
        live_data.atomic_json(output / 'library.json', value)
        (output / 'library.json.gz').write_bytes(gzip.compress((output / 'library.json').read_bytes()))
    else:
        (output / 'library.json.gz').write_bytes(gzip.compress(b'wrong payload'))
    monkeypatch.setattr(live_pipeline, 'gh', lambda *a, **k: pytest.fail('invalid artifacts were published'))
    with pytest.raises(live_data.DataError):
        live_pipeline.publish(state, output, 'owner/repo', manifest)


def test_failed_acknowledgement_does_not_proceed_to_publication(monkeypatch, tmp_path):
    state = ready(tmp_path / 'state')
    (state / 'album-meta.ndjson').unlink()

    def fake_script(name, args):
        if name == 'crawl_album_meta.py':
            write_rows(state / 'album-meta.ndjson', [record()])
        if '--ack-only' in args:
            return 1
        return 0

    monkeypatch.setattr(live_pipeline, 'script', fake_script)
    monkeypatch.setattr(live_pipeline, 'publish', lambda *a: pytest.fail('failed acknowledgement was ignored'))
    with pytest.raises(live_data.DataError, match='acknowledgement'):
        live_pipeline.run(state, 'owner/repo', minutes=1, do_publish=True)
