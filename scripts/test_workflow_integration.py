"""Workflow behavior is tested through its shared Python entry point."""
import json

import pytest

import live_data
import live_pipeline
from live_test_helpers import ready


def test_build_only_cannot_publish_partial_metadata(monkeypatch, tmp_path):
    state = ready(tmp_path / 'state')
    (state / 'album-meta.ndjson').unlink()
    monkeypatch.setattr(live_pipeline, 'gh', lambda *a, **k: pytest.fail('partial publication'))
    assert live_pipeline.main(['run', '--mode', 'build', '--state-dir', str(state), '--publish']) == 0
    assert not (tmp_path / 'live-output').exists()
    assert json.loads((state / 'progress.json').read_text())['pending'] == 1


def test_partial_recent_discovery_preserves_a_publishable_accumulated_snapshot(monkeypatch, tmp_path):
    state = ready(tmp_path / 'state')
    live_data.atomic_json(state / 'discovery.json', {'data_source': live_data.SOURCE,
                                                  'listing_complete': True, 'recent_complete': False})
    published = []
    monkeypatch.setattr(live_pipeline, 'publish', lambda *a: published.append(a[-1]) or True)
    assert live_pipeline.run(state, 'owner/repo', mode='build', do_publish=True)
    assert published[0]['songs'] == 1
    summary = json.loads((state / 'progress.json').read_text())
    assert summary['snapshot_complete'] and not summary['discovery_complete']


@pytest.mark.parametrize('minutes', ['0', '-1', '241'])
def test_invalid_workflow_budgets_are_rejected(minutes):
    with pytest.raises(SystemExit):
        live_pipeline.main(['run', '--minutes', minutes])


def test_status_preserves_the_run_publication_result(monkeypatch, tmp_path):
    state = ready(tmp_path / 'state')
    live_data.atomic_json(state / 'progress.json', {'data_source': live_data.SOURCE, 'published': True})
    summary = tmp_path / 'summary.md'
    monkeypatch.setenv('GITHUB_STEP_SUMMARY', str(summary))
    live_pipeline.main(['status', '--state-dir', str(state)])
    assert 'published: **True**' in summary.read_text()
