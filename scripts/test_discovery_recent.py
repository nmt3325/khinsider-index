import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import album_list
import crawl_recent



def row_html(slug, title, platforms, album_type, year):
    links = ', '.join('<a>%s</a>' % p for p in platforms)
    return (
        '<tr>'
        '<td class="albumIcon"><a href="/game-soundtracks/album/%s"></a></td>'
        '<td><a href="/game-soundtracks/album/%s">%s</a></td>'
        '<td>%s</td><td>%s</td><td>%s</td></tr>'
    ) % (slug, slug, title, links, album_type, year)



def recent_html(section_rows, pages=1):
    blocks = ['<h2>Latest Soundtracks</h2>']
    for heading, rows in section_rows:
        blocks.append('<h3 class="latestSoundtrackHeading">%s</h3><p></p><div class="albumListWrapper"><table class="albumList"><tr><th></th><th>Album</th><th>Platform</th><th>Type</th><th>Year</th></tr>%s</table></div>' % (
            heading,
            ''.join(row_html(*row) for row in rows),
        ))
    pager = '<div class="pagination">%s</div>' % ''.join('<a href="?page=%d">%d</a>' % (p, p) for p in range(2, pages + 1))
    return '<html><body><div id="pageContent"><div>%s%s</div></div></body></html>' % (''.join(blocks), pager)



def test_recent_discovers_multiple_dates_and_replacement(monkeypatch, tmp_path):
    html = recent_html([
        ('September 5th, 2026', [
            ('alpha', 'Alpha New', ['Windows'], 'Soundtrack', 2026),
            ('beta', 'Beta', ['Switch'], 'Gamerip', 2025),
        ]),
        ('September 4th, 2026', [
            ('alpha', 'Alpha Old', ['Windows'], 'Gamerip', 2025),
        ]),
    ])
    monkeypatch.setattr(album_list, 'fetch', lambda url, **kwargs: (html, 'ok'))
    monkeypatch.setattr(album_list, 'utc_now', lambda: '2026-09-05T12:00:10Z')
    state = tmp_path / 'recent-state.json'
    out = tmp_path / 'recent-albums.ndjson'
    queue = tmp_path / 'recent-slugs.txt'
    meta = tmp_path / 'album-meta.ndjson'
    monkeypatch.setattr(sys, 'argv', ['crawl_recent.py', '--state', str(state), '--out', str(out), '--queue', str(queue), '--metadata', str(meta)])
    assert crawl_recent.main() == 0
    data = json.loads(state.read_text(encoding='utf-8'))
    assert data['watermark'] == '2026-09-04'
    assert len(data['pending']) == 3
    assert queue.read_text(encoding='utf-8').splitlines() == ['alpha', 'beta']
    rows = [json.loads(line) for line in out.read_text(encoding='utf-8').splitlines() if line.strip()]
    by_slug = {row['slug']: row for row in rows}
    assert by_slug['alpha']['title'] == 'Alpha New'
    assert by_slug['alpha']['listed_at'] == '2026-09-05'
    assert by_slug['beta']['listed_at'] == '2026-09-05'



def test_ack_only_missing_metadata_keeps_pending_and_avoids_network(monkeypatch, tmp_path):
    state = tmp_path / 'recent-state.json'
    out = tmp_path / 'recent-albums.ndjson'
    queue = tmp_path / 'recent-slugs.txt'
    meta = tmp_path / 'missing-meta.ndjson'
    state.write_text(json.dumps({
        'version': 1,
        'watermark': '2026-09-04',
        'seen': {},
        'pending': {
            '2026-09-05\talpha': {
                'slug': 'alpha',
                'listed_at': '2026-09-05',
                'discovered_at': '2026-09-05T12:00:10Z',
                'row': {'slug': 'alpha', 'title': 'Alpha'},
            }
        },
    }), encoding='utf-8')
    out.write_text('', encoding='utf-8')

    def fail_fetch(*_args, **_kwargs):
        raise AssertionError('network should not be used for --ack-only')

    monkeypatch.setattr(album_list, 'fetch', fail_fetch)
    monkeypatch.setattr(sys, 'argv', ['crawl_recent.py', '--ack-only', '--state', str(state), '--out', str(out), '--queue', str(queue), '--metadata', str(meta)])
    assert crawl_recent.main() == 0
    data = json.loads(state.read_text(encoding='utf-8'))
    assert len(data['pending']) == 1
    assert queue.read_text(encoding='utf-8').splitlines() == ['alpha']



def test_ack_requires_strictly_later_crawled_at(monkeypatch, tmp_path):
    state = tmp_path / 'recent-state.json'
    out = tmp_path / 'recent-albums.ndjson'
    queue = tmp_path / 'recent-slugs.txt'
    meta = tmp_path / 'album-meta.ndjson'
    state.write_text(json.dumps({
        'version': 1,
        'watermark': None,
        'seen': {},
        'pending': {
            '2026-09-05\talpha': {
                'slug': 'alpha',
                'listed_at': '2026-09-05',
                'discovered_at': '2026-09-05T12:00:10Z',
                'row': {'slug': 'alpha', 'title': 'Alpha'},
            }
        },
    }), encoding='utf-8')
    out.write_text('', encoding='utf-8')
    meta.write_text(json.dumps({'slug': 'alpha', 'crawled_at': '2026-09-05T12:00:10Z'}) + '\n', encoding='utf-8')

    monkeypatch.setattr(sys, 'argv', ['crawl_recent.py', '--ack-only', '--state', str(state), '--out', str(out), '--queue', str(queue), '--metadata', str(meta)])
    assert crawl_recent.main() == 0
    data = json.loads(state.read_text(encoding='utf-8'))
    assert len(data['pending']) == 1
    assert not data['seen']

    meta.write_text(json.dumps({'slug': 'alpha', 'crawled_at': '2026-09-05T12:00:11Z'}) + '\n', encoding='utf-8')
    monkeypatch.setattr(sys, 'argv', ['crawl_recent.py', '--ack-only', '--state', str(state), '--out', str(out), '--queue', str(queue), '--metadata', str(meta)])
    assert crawl_recent.main() == 0
    data = json.loads(state.read_text(encoding='utf-8'))
    assert not data['pending']
    assert data['seen']['2026-09-05\talpha']['crawled_at'] == '2026-09-05T12:00:11Z'
    assert queue.read_text(encoding='utf-8') == ''



def test_pagination_cap_does_not_advance_watermark(monkeypatch, tmp_path):
    html = recent_html([
        ('September 5th, 2026', [('alpha', 'Alpha', ['Windows'], 'Soundtrack', 2026)]),
    ], pages=2)
    state = tmp_path / 'recent-state.json'
    out = tmp_path / 'recent-albums.ndjson'
    queue = tmp_path / 'recent-slugs.txt'
    meta = tmp_path / 'album-meta.ndjson'
    state.write_text(json.dumps({'version': 1, 'watermark': '2026-09-01', 'seen': {}, 'pending': {}}), encoding='utf-8')
    monkeypatch.setattr(album_list, 'fetch', lambda url, **kwargs: (html, 'ok'))
    monkeypatch.setattr(album_list, 'utc_now', lambda: '2026-09-05T12:00:10Z')
    monkeypatch.setattr(sys, 'argv', ['crawl_recent.py', '--state', str(state), '--out', str(out), '--queue', str(queue), '--metadata', str(meta), '--max-pages', '1'])
    assert crawl_recent.main() == 1
    data = json.loads(state.read_text(encoding='utf-8'))
    assert data['watermark'] == '2026-09-01'
    assert queue.read_text(encoding='utf-8').splitlines() == ['alpha']
