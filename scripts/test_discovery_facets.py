import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import album_list
import crawl_facets


def row_html(slug, title, platforms, album_type, year):
    links = ', '.join('<a>%s</a>' % p for p in platforms)
    return (
        '<tr>'
        '<td class="albumIcon"><a href="/game-soundtracks/album/%s"></a></td>'
        '<td><a href="/game-soundtracks/album/%s">%s</a></td>'
        '<td>%s</td><td>%s</td><td>%s</td></tr>'
    ) % (slug, slug, title, links, album_type, year)


def list_html(rows, page=1, pages=1, total=None):
    pager = ''.join('<a href="?page=%d">%d</a>' % (p, p) for p in range(2, pages + 1))
    found = '' if total is None else '<p>Found %d albums!</p>' % total
    return (
        '<html><body>%s<div class="pagination">%s</div>'
        '<table class="albumList">'
        '<tr><th></th><th>Album</th><th>Platform</th><th>Type</th><th>Year</th></tr>%s'
        '</table></body></html>'
    ) % (found, pager, ''.join(row_html(*row) for row in rows))


def index_html(kind, rows):
    prefix = '/game-soundtracks/%s/' % kind
    return '<html><body>%s</body></html>' % ''.join(
        '<a href="%s%s">%s</a> (%d)' % (prefix, key, name, count)
        for key, name, count in rows
    )


def read_ndjson(path):
    if not path.exists() or not path.read_text(encoding='utf-8').strip():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def test_fresh_retry_keeps_live_until_complete(monkeypatch, tmp_path):
    live_out = tmp_path / 'facet-publisher.ndjson'
    live_state = tmp_path / 'facet-publisher.entities'
    live_stats = tmp_path / 'facet-publisher-stats.ndjson'
    live_fail = tmp_path / 'facet-publisher-failures.log'
    live_out.write_text('{"keep": true}\n', encoding='utf-8')
    live_state.write_text('legacy\n', encoding='utf-8')
    live_stats.write_text('{"legacy": true}\n', encoding='utf-8')
    live_fail.write_text('old\n', encoding='utf-8')

    idx = index_html('publisher', [
        ('alpha-co', 'Alpha Co', 20),
        ('beta-co', 'Beta Co', 12),
    ])
    alpha_page = list_html([
        ('alpha', 'Alpha', ['Windows'], 'Soundtrack', 2024),
    ])
    beta_page_1 = list_html([
        ('beta', 'Beta', ['Switch'], 'Gamerip', 2025),
    ], pages=2, total=2)
    beta_page_2 = list_html([
        ('beta-2', 'Beta 2', ['PS5'], 'Soundtrack', 2026),
    ], page=2, pages=2, total=2)

    calls = {}

    def fake_fetch(url, **_kwargs):
        calls[url] = calls.get(url, 0) + 1
        if 'album-publishers' in url:
            return idx, 'ok'
        if 'publisher/alpha-co' in url:
            return alpha_page, 'ok'
        if 'publisher/beta-co?page=2' in url and calls[url] == 1:
            return None, 'http 503'
        if 'publisher/beta-co?page=2' in url:
            return beta_page_2, 'ok'
        if 'publisher/beta-co' in url:
            return beta_page_1, 'ok'
        raise AssertionError(url)

    monkeypatch.setattr(album_list, 'fetch', fake_fetch)
    monkeypatch.setattr(sys, 'argv', [
        'crawl_facets.py', '--kind', 'publisher', '--fresh',
        '--out', str(live_out), '--state', str(live_state),
        '--stats', str(live_stats), '--failures', str(live_fail),
    ])
    assert crawl_facets.main() == 1
    assert live_out.read_text(encoding='utf-8') == '{"keep": true}\n'
    assert live_state.read_text(encoding='utf-8') == 'legacy\n'
    assert live_stats.read_text(encoding='utf-8') == '{"legacy": true}\n'

    stage_out = tmp_path / 'facet-publisher-staging.ndjson'
    stage_state = tmp_path / 'facet-publisher-staging.entities'
    stage_stats = tmp_path / 'facet-publisher-staging-stats.ndjson'
    assert [row['key'] for row in read_ndjson(stage_out)] == ['alpha-co']
    assert stage_state.read_text(encoding='utf-8').split() == ['alpha-co']
    assert [row['key'] for row in read_ndjson(stage_stats)] == ['alpha-co']

    monkeypatch.setattr(sys, 'argv', [
        'crawl_facets.py', '--kind', 'publisher', '--fresh',
        '--out', str(live_out), '--state', str(live_state),
        '--stats', str(live_stats), '--failures', str(live_fail),
    ])
    assert crawl_facets.main() == 0
    assert [row['key'] for row in read_ndjson(live_out)] == ['alpha-co', 'beta-co', 'beta-co']
    assert live_state.read_text(encoding='utf-8').split() == ['alpha-co', 'beta-co']
    assert [row['key'] for row in read_ndjson(live_stats)] == ['alpha-co', 'beta-co']
    assert not stage_out.exists()
    assert not stage_state.exists()
    assert not stage_stats.exists()


def test_fresh_limit_keeps_live_and_returns_nonzero(monkeypatch, tmp_path):
    live_out = tmp_path / 'facet-publisher.ndjson'
    live_state = tmp_path / 'facet-publisher.entities'
    live_stats = tmp_path / 'facet-publisher-stats.ndjson'
    live_fail = tmp_path / 'facet-publisher-failures.log'
    live_out.write_text('{"keep": true}\n', encoding='utf-8')
    live_state.write_text('legacy\n', encoding='utf-8')
    live_stats.write_text('{"legacy": true}\n', encoding='utf-8')
    live_fail.write_text('old\n', encoding='utf-8')

    idx = index_html('publisher', [
        ('alpha-co', 'Alpha Co', 20),
        ('beta-co', 'Beta Co', 12),
    ])
    page = list_html([
        ('alpha', 'Alpha', ['Windows'], 'Soundtrack', 2024),
    ])

    def fake_fetch(url, **_kwargs):
        if 'album-publishers' in url:
            return idx, 'ok'
        return page, 'ok'

    monkeypatch.setattr(album_list, 'fetch', fake_fetch)
    monkeypatch.setattr(sys, 'argv', [
        'crawl_facets.py', '--kind', 'publisher', '--fresh', '--limit', '1',
        '--out', str(live_out), '--state', str(live_state),
        '--stats', str(live_stats), '--failures', str(live_fail),
    ])
    assert crawl_facets.main() == 1
    assert live_out.read_text(encoding='utf-8') == '{"keep": true}\n'
    assert live_state.read_text(encoding='utf-8') == 'legacy\n'
    assert live_stats.read_text(encoding='utf-8') == '{"legacy": true}\n'
    assert (tmp_path / 'facet-publisher-staging.entities').read_text(encoding='utf-8').split() == ['alpha-co']


def test_fresh_deadline_keeps_live_and_staging(monkeypatch, tmp_path):
    live_out = tmp_path / 'facet-publisher.ndjson'
    live_state = tmp_path / 'facet-publisher.entities'
    live_stats = tmp_path / 'facet-publisher-stats.ndjson'
    live_fail = tmp_path / 'facet-publisher-failures.log'
    live_out.write_text('{"keep": true}\n', encoding='utf-8')
    live_state.write_text('legacy\n', encoding='utf-8')
    live_stats.write_text('{"legacy": true}\n', encoding='utf-8')
    live_fail.write_text('old\n', encoding='utf-8')

    idx = index_html('publisher', [
        ('alpha-co', 'Alpha Co', 20),
        ('beta-co', 'Beta Co', 12),
    ])
    page = list_html([
        ('alpha', 'Alpha', ['Windows'], 'Soundtrack', 2024),
    ])
    now = {'value': 0.0}

    def fake_fetch(url, **_kwargs):
        if 'album-publishers' in url:
            now['value'] = 0.0
            return idx, 'ok'
        now['value'] += 61.0
        return page, 'ok'

    monkeypatch.setattr(album_list, 'fetch', fake_fetch)
    monkeypatch.setattr(crawl_facets.time, 'time', lambda: now['value'])
    monkeypatch.setattr(sys, 'argv', [
        'crawl_facets.py', '--kind', 'publisher', '--fresh', '--deadline-minutes', '1',
        '--out', str(live_out), '--state', str(live_state),
        '--stats', str(live_stats), '--failures', str(live_fail),
    ])
    assert crawl_facets.main() == 1
    assert live_out.read_text(encoding='utf-8') == '{"keep": true}\n'
    assert live_state.read_text(encoding='utf-8') == 'legacy\n'
    assert live_stats.read_text(encoding='utf-8') == '{"legacy": true}\n'
    assert (tmp_path / 'facet-publisher-staging.entities').read_text(encoding='utf-8').split() == ['alpha-co']
    assert [row['key'] for row in read_ndjson(tmp_path / 'facet-publisher-staging-stats.ndjson')] == ['alpha-co']


def test_fresh_invalid_index_keeps_live(monkeypatch, tmp_path):
    live_out = tmp_path / 'facet-publisher.ndjson'
    live_state = tmp_path / 'facet-publisher.entities'
    live_stats = tmp_path / 'facet-publisher-stats.ndjson'
    live_fail = tmp_path / 'facet-publisher-failures.log'
    live_out.write_text('{"keep": true}\n', encoding='utf-8')
    live_state.write_text('legacy\n', encoding='utf-8')
    live_stats.write_text('{"legacy": true}\n', encoding='utf-8')
    live_fail.write_text('old\n', encoding='utf-8')

    monkeypatch.setattr(album_list, 'fetch', lambda url, **kwargs: ('<html><body><p>no entities</p></body></html>', 'ok'))
    monkeypatch.setattr(sys, 'argv', [
        'crawl_facets.py', '--kind', 'publisher', '--fresh',
        '--out', str(live_out), '--state', str(live_state),
        '--stats', str(live_stats), '--failures', str(live_fail),
    ])
    assert crawl_facets.main() == 1
    assert live_out.read_text(encoding='utf-8') == '{"keep": true}\n'
    assert live_state.read_text(encoding='utf-8') == 'legacy\n'
    assert live_stats.read_text(encoding='utf-8') == '{"legacy": true}\n'
    assert not (tmp_path / 'facet-publisher-staging.ndjson').exists()


def test_confirmed_404_still_promotes_successfully(monkeypatch, tmp_path):
    live_out = tmp_path / 'facet-developer.ndjson'
    live_state = tmp_path / 'facet-developer.entities'
    live_stats = tmp_path / 'facet-developer-stats.ndjson'
    live_fail = tmp_path / 'facet-developer-failures.log'

    idx = index_html('developer', [
        ('gone-studio', 'Gone Studio', 11),
        ('live-studio', 'Live Studio', 12),
    ])
    live_page = list_html([
        ('gamma', 'Gamma', ['PC'], 'Soundtrack', 2024),
    ])

    def fake_fetch(url, **_kwargs):
        if 'album-developers' in url:
            return idx, 'ok'
        if 'developer/gone-studio' in url:
            return None, 'gone'
        if 'developer/live-studio' in url:
            return live_page, 'ok'
        raise AssertionError(url)

    monkeypatch.setattr(album_list, 'fetch', fake_fetch)
    monkeypatch.setattr(sys, 'argv', [
        'crawl_facets.py', '--kind', 'developer', '--fresh',
        '--out', str(live_out), '--state', str(live_state),
        '--stats', str(live_stats), '--failures', str(live_fail),
    ])
    assert crawl_facets.main() == 0
    assert [row['key'] for row in read_ndjson(live_out)] == ['live-studio']
    assert live_state.read_text(encoding='utf-8').split() == ['live-studio', 'gone-studio']
    stats_rows = read_ndjson(live_stats)
    assert [row['key'] for row in stats_rows] == ['live-studio', 'gone-studio']
    assert stats_rows[1]['found'] == 0
