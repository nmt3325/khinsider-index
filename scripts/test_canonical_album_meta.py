from pathlib import Path

from bs4 import BeautifulSoup

import album_meta

FIXTURES = Path(__file__).resolve().parent / 'testdata'


def load_fixture(name):
    return (FIXTURES / name).read_text(encoding='utf-8')


def test_album_record_contains_complete_tracks_and_player_urls():
    html = load_fixture('seed-2016.html')
    soup = BeautifulSoup(html, 'html.parser')
    rec = album_meta.album_record('seed-2016', soup, html=html)
    assert rec['tracks_complete'] is True
    assert rec['track_count'] == 4
    assert rec['tracks'][0]['songid'] == '3068831'
    assert rec['tracks'][0]['mp3_url'].endswith('/01.%20SkyDrive%21%20%5BALR%20Remix%5D.mp3')
    assert rec['tracks'][0]['title'] == 'SkyDrive! [ALR Remix]'


def test_missing_songlist_is_retryable_parse_error():
    html = load_fixture('seed-2016.html').replace('<table id="songlist">', '<table id="songlist-gone">')
    soup = BeautifulSoup(html, 'html.parser')
    try:
        album_meta.album_record('seed-2016', soup, html=html)
    except album_meta.SonglistError as exc:
        assert 'songlist' in str(exc)
    else:
        raise AssertionError('expected SonglistError')


def test_parse_songlist_multiple_discs():
    html = '''
    <table id="songlist">
      <tr id="songlist_header"><th>&nbsp;</th><th>#</th><th>CD</th><th>Song Name</th><th>MP3</th><th>FLAC</th></tr>
      <tr>
        <td></td><td>1.</td><td>1</td>
        <td><a href="/game-soundtracks/album/x/01.%2520Alpha.mp3">Alpha</a></td>
        <td><a href="/game-soundtracks/album/x/01.%2520Alpha.mp3">1 MB</a></td>
        <td><a href="/game-soundtracks/album/x/01.%2520Alpha.mp3">2 MB</a></td>
        <td><div class="playlistAddTo" songid="100"></div></td>
      </tr>
      <tr>
        <td></td><td>1.</td><td>2</td>
        <td><a href="/game-soundtracks/album/x/02.%2520Beta.mp3">Beta</a></td>
        <td><a href="/game-soundtracks/album/x/02.%2520Beta.mp3">1 MB</a></td>
        <td><a href="/game-soundtracks/album/x/02.%2520Beta.mp3">2 MB</a></td>
        <td><div class="playlistAddTo" songid="101"></div></td>
      </tr>
    </table>
    '''
    soup = BeautifulSoup(html, 'html.parser')
    tracks = album_meta.parse_songlist(soup, 'x', player_urls={'100': 'https://a.vgmtreasurechest.com/soundtracks/x/hash/01.%20Alpha.mp3',
                                                               '101': 'https://a.vgmtreasurechest.com/soundtracks/x/hash/02.%20Beta.mp3'})
    assert [(t['disc'], t['num'], t['songid']) for t in tracks] == [(1, 1, '100'), (2, 1, '101')]
