from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / '.github/workflows'


def load(name):
    return yaml.safe_load((WORKFLOWS / name).read_text())


def test_only_one_shared_writer_and_restore_before_mutation():
    workflow = load('live-data.yaml')
    job = workflow['jobs']['collect']
    assert job['concurrency'] == {'group': 'khinsider-metadata', 'cancel-in-progress': False}
    assert job['timeout-minutes'] <= 330
    steps = job['steps']
    restore = next(i for i, step in enumerate(steps) if step.get('id') == 'restore')
    collect = next(i for i, step in enumerate(steps) if 'args=(run' in step.get('run', ''))
    assert restore < collect
    checkpoint = next(step for step in steps if 'live_pipeline.py save' in step.get('run', ''))
    assert "always() && steps.restore.outcome == 'success'" in checkpoint['if']
    assert any(step.get('uses') == 'actions/upload-artifact@v4' for step in steps)


def test_checkout_does_not_supply_the_legacy_index():
    workflow = load('live-data.yaml')
    checkout = workflow['jobs']['collect']['steps'][0]['with']
    assert checkout['sparse-checkout-cone-mode'] is False
    assert 'index.json' not in checkout['sparse-checkout']
    text = (WORKFLOWS / 'live-data.yaml').read_text()
    for forbidden in ('crawl-data', 'song-urls-', 'songs_cached', 'crawl_state_snapshot'):
        assert forbidden not in text


def test_every_serving_workflow_uses_the_same_complete_only_engine():
    for name, mode in [('album-meta.yaml', 'refresh'), ('album-meta-residual.yaml', 'backfill'), ('song-index.yaml', 'build')]:
        jobs = load(name)['jobs']
        assert len(jobs) == 1
        job = next(iter(jobs.values()))
        assert job['uses'] == './.github/workflows/live-data.yaml'
        assert job['with']['mode'] == mode
        assert 'concurrency' not in load(name)  # No caller/callee lock deadlock.


def test_archiving_no_longer_crawls_from_the_old_index():
    text = (WORKFLOWS / 'wayback-archive.yaml').read_text()
    assert 'index.json' not in text and 'download crawl-data' not in text
    assert 'python wayback/crawl_songs.py' not in text
    assert "steps.modern.outputs.ready == 'true'" in text
    assert "always() && steps.restore.outcome == 'success'" in text


def test_deleted_workflows_are_not_reintroduced():
    for name in ('crawl.yaml', 'index.yaml', 'release.yaml'):
        assert not (WORKFLOWS / name).exists()
