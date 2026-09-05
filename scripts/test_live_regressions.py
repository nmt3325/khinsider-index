import json
from pathlib import Path
import sys

from bs4 import BeautifulSoup
import pytest

import album_list
import album_meta
import crawl_album_meta
import crawl_recent
import khinsider_player
import live_data
from test_discovery_recent import recent_html


def test_ascii_word_boundaries_match_the_javascript_packer():
    assert khinsider_player._WORD.findall('xü y日本語') == ['x', 'y']


def test_songlist_cannot_drop_a_row_when_player_urls_are_unavailable():
    html = (Path(__file__).parent / 'testdata/seed-2016.html').read_text()
    soup = BeautifulSoup(html, 'html.parser')
    row = soup.select_one('.playlistAddTo[songid]').find_parent('tr')
    for anchor in row.find_all('a', href=True):
        if '/game-soundtracks/album/' in anchor['href']:
            anchor['href'] = '/wrong/path.mp3'
    with pytest.raises(album_meta.SonglistError):
        album_meta.parse_songlist(soup, 'seed-2016', player_urls={})


def test_encoded_slug_request_does_not_double_escape(monkeypatch):
    seen = []

    class Session:
        def get(self, url, **kwargs):
            seen.append(url)
            return type('Response', (), {'status_code': 200, 'text': '<html>album</html>'})()

    monkeypatch.setattr(crawl_album_meta, 'session', lambda: Session())
    monkeypatch.setattr(crawl_album_meta.time, 'sleep', lambda _: None)
    crawl_album_meta.fetch('caf%C3%A9', retries=1, delay=0, jitter=0)
    assert seen and seen[0].endswith('/caf%C3%A9') and '%25C3' not in seen[0]


def test_long_recent_history_resumes_after_the_cap(monkeypatch, tmp_path):
    first = recent_html([('September 5th, 2026', [('alpha', 'A', [], 'Gamerip', 2026)])], pages=2)
    second = recent_html([('September 4th, 2026', [('beta', 'B', [], 'Gamerip', 2026)])], pages=2)
    calls = []

    def fetch(url, **kwargs):
        calls.append(url)
        return (second if 'page=2' in url else first), 'ok'

    monkeypatch.setattr(album_list, 'fetch', fetch)
    state = tmp_path / 'recent-state.json'
    args = ['recent', '--state', str(state), '--out', str(tmp_path / 'recent.ndjson'),
            '--queue', str(tmp_path / 'queue.txt'), '--metadata', str(tmp_path / 'meta.ndjson'), '--max-pages', '1']
    monkeypatch.setattr(sys, 'argv', args)
    assert crawl_recent.main() == 1
    assert json.loads(state.read_text())['cursor']['page'] == 2
    assert crawl_recent.main() == 0
    result = json.loads(state.read_text())
    assert len(calls) == 2 and 'page=2' in calls[1]
    assert result['watermark'] == '2026-09-05' and result['cursor'] is None
    assert len(result['pending']) == 2


def test_legacy_metadata_cannot_acknowledge_a_recent_update(tmp_path):
    path = tmp_path / 'meta.ndjson'
    path.write_text(json.dumps({'slug': 'alpha', 'crawled_at': '2026-09-05T14:00:00Z'}) + '\n')
    with pytest.raises(live_data.DataError):
        crawl_recent.load_meta_crawled(path)
