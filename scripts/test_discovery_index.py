import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import album_list
import crawl_facets
import crawl_index_pages


FIXTURES = pathlib.Path(__file__).resolve().parent / 'testdata'


def row_html(slug, title, platforms, album_type, year):
    links = ', '.join('<a>%s</a>' % p for p in platforms)
    return (
        '<tr>'
        '<td class="albumIcon"><a href="/game-soundtracks/album/%s"></a></td>'
        '<td><a href="/game-soundtracks/album/%s">%s</a></td>'
        '<td>%s</td><td>%s</td><td>%s</td></tr>'
    ) % (slug, slug, title, links, album_type, year)


def list_html(rows, page=1, pages=1, total=None, body_tag=False):
    pager = ''.join('<a href="?page=%d">%d</a>' % (p, p) for p in range(2, pages + 1))
    found = '' if total is None else '<p>Found %d albums!</p>' % total
    table_rows = '<tr><th></th><th>Album</th><th>Platform</th><th>Type</th><th>Year</th></tr>%s' % ''.join(
        row_html(*row) for row in rows
    )
    if body_tag:
        table_rows = '<tbody>%s</tbody>' % table_rows
    return (
        '<html><body>%s<div class="pagination">%s</div>'
        '<table class="albumList">%s</table></body></html>'
    ) % (found, pager, table_rows)


def live_paths(tmp_path):
    return (
        tmp_path / 'album-list.ndjson',
        tmp_path / 'album-list.pages',
        tmp_path / 'album-list-failures.log',
    )


def seed_live_files(tmp_path):
    live_out, live_state, live_fail = live_paths(tmp_path)
    live_out.write_text('{"keep": true}\n', encoding='utf-8')
    live_state.write_text('99\n', encoding='utf-8')
    live_fail.write_text('old\n', encoding='utf-8')
    return live_out, live_state, live_fail


def test_parse_album_list_matches_listing_baseline():
    html = (FIXTURES / 'listing.html').read_text(encoding='utf-8')
    baseline = json.loads((FIXTURES / 'listing.baseline.json').read_text(encoding='utf-8'))
    assert list(album_list.parse_album_list(html)) == baseline


def test_parse_album_list_matches_recent_baseline():
    html = (FIXTURES / 'recent-list.html').read_text(encoding='utf-8')
    baseline = json.loads((FIXTURES / 'recent-list.baseline.json').read_text(encoding='utf-8'))
    assert list(album_list.parse_album_list(html)) == baseline


def test_parse_album_list_reads_tbody_rows():
    rows, pages, total = album_list.parse_album_list(
        list_html([('alpha', 'Alpha', ['Windows'], 'Soundtrack', 2024)], total=1, body_tag=True)
    )
    assert pages == 1
    assert total == 1
    assert rows == [{
        'slug': 'alpha',
        'title': 'Alpha',
        'platforms': ['Windows'],
        'album_type': 'Soundtrack',
        'year': 2024,
    }]


def test_fresh_subset_keeps_live_files_until_full_snapshot(monkeypatch, tmp_path):
    live_out, live_state, live_fail = seed_live_files(tmp_path)
    page1 = list_html([
        ('alpha', 'Alpha', ['Windows'], 'Soundtrack', 2024),
    ], pages=2, total=2)

    def fake_fetch(_url, **_kwargs):
        return page1, 'ok'

    monkeypatch.setattr(album_list, 'fetch', fake_fetch)
    monkeypatch.setattr(
        sys,
        'argv',
        ['crawl_index_pages.py', '--fresh', '--pages', '1', '--out', str(live_out), '--state', str(live_state), '--failures', str(live_fail)],
    )
    assert crawl_index_pages.main() == 1
    assert live_out.read_text(encoding='utf-8') == '{"keep": true}\n'
    assert live_state.read_text(encoding='utf-8') == '99\n'
    assert (tmp_path / 'album-list-staging.pages').read_text(encoding='utf-8') == '1\n'
    assert 'alpha' in (tmp_path / 'album-list-staging.ndjson').read_text(encoding='utf-8')


def test_fresh_snapshot_promotes_after_full_set_completed(monkeypatch, tmp_path):
    live_out, live_state, live_fail = seed_live_files(tmp_path)
    page1 = list_html([
        ('alpha', 'Alpha', ['Windows'], 'Soundtrack', 2024),
    ], pages=2, total=2)
    page2 = list_html([
        ('beta', 'Beta', ['Switch'], 'Gamerip', 2025),
    ], page=2, pages=2, total=2)

    def fake_fetch(url, **_kwargs):
        if 'page=2' in url:
            return page2, 'ok'
        return page1, 'ok'

    monkeypatch.setattr(album_list, 'fetch', fake_fetch)
    monkeypatch.setattr(
        sys,
        'argv',
        ['crawl_index_pages.py', '--fresh', '--pages', '1', '--out', str(live_out), '--state', str(live_state), '--failures', str(live_fail)],
    )
    assert crawl_index_pages.main() == 1
    monkeypatch.setattr(
        sys,
        'argv',
        ['crawl_index_pages.py', '--fresh', '--pages', '2', '--out', str(live_out), '--state', str(live_state), '--failures', str(live_fail)],
    )
    assert crawl_index_pages.main() == 0
    live_text = live_out.read_text(encoding='utf-8')
    assert 'alpha' in live_text and 'beta' in live_text
    assert live_state.read_text(encoding='utf-8').split() == ['1', '2']
    assert not (tmp_path / 'album-list-staging.ndjson').exists()
    assert not (tmp_path / 'album-list-staging.pages').exists()
    assert not (tmp_path / 'album-list-staging.context.json').exists()


def test_fresh_failure_keeps_live_files_until_complete(monkeypatch, tmp_path):
    live_out, live_state, live_fail = seed_live_files(tmp_path)
    page1 = list_html([
        ('alpha', 'Alpha', ['Windows'], 'Soundtrack', 2024),
    ], pages=2, total=2)
    page2 = list_html([
        ('beta', 'Beta', ['Switch'], 'Gamerip', 2025),
    ], page=2, pages=2, total=2)
    calls = []

    def fake_fetch(url, **_kwargs):
        calls.append(url)
        if 'page=2' in url:
            if calls.count(url) == 1:
                return None, 'http 503'
            return page2, 'ok'
        return page1, 'ok'

    monkeypatch.setattr(album_list, 'fetch', fake_fetch)
    monkeypatch.setattr(
        sys,
        'argv',
        ['crawl_index_pages.py', '--fresh', '--out', str(live_out), '--state', str(live_state), '--failures', str(live_fail)],
    )
    assert crawl_index_pages.main() == 1
    assert live_out.read_text(encoding='utf-8') == '{"keep": true}\n'
    assert live_state.read_text(encoding='utf-8') == '99\n'
    assert (tmp_path / 'album-list-staging.pages').read_text(encoding='utf-8') == '1\n'
    assert 'alpha' in (tmp_path / 'album-list-staging.ndjson').read_text(encoding='utf-8')

    monkeypatch.setattr(
        sys,
        'argv',
        ['crawl_index_pages.py', '--fresh', '--out', str(live_out), '--state', str(live_state), '--failures', str(live_fail)],
    )
    assert crawl_index_pages.main() == 0
    live_text = live_out.read_text(encoding='utf-8')
    assert 'alpha' in live_text and 'beta' in live_text
    assert live_state.read_text(encoding='utf-8').split() == ['1', '2']
    assert not (tmp_path / 'album-list-staging.ndjson').exists()
    assert not (tmp_path / 'album-list-staging.pages').exists()


def test_fresh_max_pages_keeps_live_until_full_snapshot(monkeypatch, tmp_path):
    live_out, live_state, live_fail = seed_live_files(tmp_path)
    page1 = list_html([
        ('alpha', 'Alpha', ['Windows'], 'Soundtrack', 2024),
    ], pages=2, total=2)
    page2 = list_html([
        ('beta', 'Beta', ['Switch'], 'Gamerip', 2025),
    ], page=2, pages=2, total=2)

    def fake_fetch(url, **_kwargs):
        if 'page=2' in url:
            return page2, 'ok'
        return page1, 'ok'

    monkeypatch.setattr(album_list, 'fetch', fake_fetch)
    monkeypatch.setattr(
        sys,
        'argv',
        ['crawl_index_pages.py', '--fresh', '--max-pages', '1', '--out', str(live_out), '--state', str(live_state), '--failures', str(live_fail)],
    )
    assert crawl_index_pages.main() == 1
    assert live_out.read_text(encoding='utf-8') == '{"keep": true}\n'
    assert (tmp_path / 'album-list-staging.pages').read_text(encoding='utf-8') == '1\n'

    monkeypatch.setattr(
        sys,
        'argv',
        ['crawl_index_pages.py', '--fresh', '--out', str(live_out), '--state', str(live_state), '--failures', str(live_fail)],
    )
    assert crawl_index_pages.main() == 0
    live_text = live_out.read_text(encoding='utf-8')
    assert 'alpha' in live_text and 'beta' in live_text


def test_staging_paths_are_separate_per_output_basename(tmp_path):
    base = tmp_path
    one = crawl_index_pages.staging_paths(str(base / 'one.ndjson'))
    two = crawl_index_pages.staging_paths(str(base / 'two.ndjson'))
    assert one[0].endswith('one.ndjson-staging.ndjson')
    assert two[0].endswith('two.ndjson-staging.ndjson')
    assert one != two


def test_fresh_source_change_preserves_incompatible_staging(monkeypatch, tmp_path):
    live_out, live_state, live_fail = seed_live_files(tmp_path)
    stale_out = tmp_path / 'album-list-staging.ndjson'
    stale_state = tmp_path / 'album-list-staging.pages'
    stale_fail = tmp_path / 'album-list-staging-failures.log'
    stale_marker = tmp_path / 'album-list-staging.context.json'
    stale_out.write_text('{"slug": "stale"}\n', encoding='utf-8')
    stale_state.write_text('9\n', encoding='utf-8')
    stale_fail.write_text('stale\n', encoding='utf-8')
    stale_marker.write_text('{"path": "/old-source"}\n', encoding='utf-8')
    page1 = list_html([
        ('alpha', 'Alpha', ['Windows'], 'Soundtrack', 2024),
    ], pages=2, total=2)

    def fake_fetch(_url, **_kwargs):
        return page1, 'ok'

    monkeypatch.setattr(album_list, 'fetch', fake_fetch)
    monkeypatch.setattr(
        sys,
        'argv',
        ['crawl_index_pages.py', '--fresh', '--pages', '1', '--path', '/new-source', '--out', str(live_out), '--state', str(live_state), '--failures', str(live_fail)],
    )
    with pytest.raises(SystemExit, match='staging context'):
        crawl_index_pages.main()
    assert 'stale' in stale_out.read_text(encoding='utf-8')
    assert live_out.read_text(encoding='utf-8') == '{"keep": true}\n'
    assert stale_state.read_text(encoding='utf-8') == '9\n'
    marker = json.loads(stale_marker.read_text(encoding='utf-8'))
    assert marker == {'path': '/old-source'}


def test_missing_table_200_is_not_empty_success_for_facet(monkeypatch, tmp_path):
    out = tmp_path / 'facet-publisher.ndjson'
    state = tmp_path / 'facet-publisher.entities'
    stats = tmp_path / 'facet-publisher-stats.ndjson'
    failures = tmp_path / 'facet-publisher-failures.log'
    index_html = '<html><body><a href="/game-soundtracks/publisher/nintendo">Nintendo</a> (12)</body></html>'
    malformed = '<html><body><p>Found 12 albums!</p></body></html>'

    def fake_fetch(url, **_kwargs):
        if 'album-publishers' in url:
            return index_html, 'ok'
        return malformed, 'ok'

    monkeypatch.setattr(album_list, 'fetch', fake_fetch)
    monkeypatch.setattr(sys, 'argv', [
        'crawl_facets.py', '--kind', 'publisher', '--out', str(out), '--state', str(state),
        '--stats', str(stats), '--failures', str(failures),
    ])
    assert crawl_facets.main() == 1
    assert not state.exists() or not state.read_text(encoding='utf-8').strip()
    assert 'no-list-table' in failures.read_text(encoding='utf-8')


def test_staging_paths_do_not_collide_for_different_extensions(tmp_path):
    paths = [crawl_index_pages.staging_paths(str(tmp_path / name))
             for name in ('one.ndjson', 'one.json', 'one.pages', 'one')]
    assert len({path for group in paths for path in group}) == 16


@pytest.mark.parametrize('damage', ['missing-output', 'missing-rows', 'invalid-json'])
def test_inconsistent_restored_staging_preserves_last_good(monkeypatch, tmp_path, damage):
    out, state, failures = seed_live_files(tmp_path)
    stage_out, stage_state, _, marker = map(pathlib.Path, crawl_index_pages.staging_paths(str(out)))
    stage_state.write_text('1\n')
    marker.write_text(json.dumps({'path': album_list.LIST_PATH}))
    if damage == 'missing-rows':
        stage_out.write_text(json.dumps({'page': 2, 'slug': 'wrong-page'}) + '\n')
    elif damage == 'invalid-json':
        stage_out.write_text('{broken')
    monkeypatch.setattr(sys, 'argv', ['crawl_index_pages.py', '--fresh', '--out', str(out),
                                     '--state', str(state), '--failures', str(failures)])
    with pytest.raises(SystemExit, match='staging checkpoint'):
        crawl_index_pages.main()
    assert out.read_text() == '{"keep": true}\n'
    assert state.read_text() == '99\n'
    assert stage_state.read_text() == '1\n'


def test_empty_full_listing_keeps_last_good(monkeypatch, tmp_path):
    out, state, failures = seed_live_files(tmp_path)
    monkeypatch.setattr(album_list, 'fetch', lambda *a, **kw: (list_html([], total=0), 'ok'))
    monkeypatch.setattr(sys, 'argv', ['crawl_index_pages.py', '--fresh', '--out', str(out),
                                     '--state', str(state), '--failures', str(failures)])
    with pytest.raises(SystemExit, match='empty-list-table'):
        crawl_index_pages.main()
    assert out.read_text() == '{"keep": true}\n'
    assert state.read_text() == '99\n'
