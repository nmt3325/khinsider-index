import pytest

import live_data
from live_test_helpers import catalogue, record, write_rows
from metadata_progress import measure


def test_progress_requires_complete_tracks_not_listing_fields(tmp_path):
    catalogue(tmp_path, ['alpha', 'beta'])
    result = measure(tmp_path / 'catalogue.json', tmp_path / 'absent.ndjson')
    assert result['pending'] == 2 and not result['complete']
    write_rows(tmp_path / 'album-meta.ndjson', [record()])
    result = measure(tmp_path / 'catalogue.json', tmp_path / 'album-meta.ndjson')
    assert (result['fetched'], result['pending'], result['tracks']) == (1, 1, 1)
    assert result['total'] == result['fetched'] + result['unavailable'] + result['pending']


def test_legacy_summary_cannot_count_as_fetched(tmp_path):
    catalogue(tmp_path)
    write_rows(tmp_path / 'album-meta.ndjson', [{'slug': 'alpha', 'publishers': [], 'date_added': None}])
    with pytest.raises(live_data.DataError):
        measure(tmp_path / 'catalogue.json', tmp_path / 'album-meta.ndjson')


def test_complete_page_with_empty_optional_fields_is_valid(tmp_path):
    catalogue(tmp_path)
    write_rows(tmp_path / 'album-meta.ndjson', [dict(record(), publishers=[], date_added=None)])
    assert measure(tmp_path / 'catalogue.json', tmp_path / 'album-meta.ndjson')['complete']


def test_outside_catalogue_is_never_added(tmp_path):
    catalogue(tmp_path)
    write_rows(tmp_path / 'album-meta.ndjson', [record(), record('outside')])
    result = measure(tmp_path / 'catalogue.json', tmp_path / 'album-meta.ndjson')
    assert result['total'] == result['fetched'] == 1


def test_failure_log_is_not_evidence_of_removal(tmp_path):
    catalogue(tmp_path)
    (tmp_path / 'album-meta-failures.log').write_text('alpha\tgone\told failure\n')
    result = measure(tmp_path / 'catalogue.json', tmp_path / 'absent.ndjson')
    assert result['unavailable'] == 0 and result['pending'] == 1


def test_corrupt_metadata_is_not_hidden_by_counts(tmp_path):
    catalogue(tmp_path)
    (tmp_path / 'album-meta.ndjson').write_text('{broken\n')
    with pytest.raises(live_data.DataError):
        measure(tmp_path / 'catalogue.json', tmp_path / 'album-meta.ndjson')
