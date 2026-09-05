import direct_links


def test_process_album_uses_metadata_player_urls_without_fetching_song_pages():
    rec = {
        'slug': 'seed-2016',
        'tracks_complete': True,
        'tracks': [
            {'basename': '01.%20SkyDrive%21%20%5BALR%20Remix%5D.mp3', 'formats': ['mp3'],
             'mp3_url': 'https://lambda.vgmtreasurechest.com/soundtracks/seed-2016/wczouhcn/01.%20SkyDrive%21%20%5BALR%20Remix%5D.mp3'},
            {'basename': '02.%20Eternal%20Dream%20%5BTracy%20Remix%5D.mp3', 'formats': ['mp3'],
             'mp3_url': 'https://lambda.vgmtreasurechest.com/soundtracks/seed-2016/lomukqyu/02.%20Eternal%20Dream%20%5BTracy%20Remix%5D.mp3'},
        ]
    }

    class Dummy:
        def get(self, url, timeout=30):
            raise AssertionError('song page fetch was not expected: %s' % url)

    queue, summary = direct_links.process_album(Dummy(), 'seed-2016', rec)
    assert len(queue) == 2
    assert summary['used_metadata'] is True
    assert summary['fetched_song_pages'] == 0


def test_process_album_falls_back_to_real_song_page_for_flac_and_unsupported_player():
    page_html = '''
    <html><body>
      <a href="https://lambda.vgmtreasurechest.com/soundtracks/seed-2016/hash/01.%20Track.mp3">mp3</a>
      <a href="https://lambda.vgmtreasurechest.com/soundtracks/seed-2016/hash/01.%20Track.flac">flac</a>
    </body></html>
    '''

    class Dummy:
        def get(self, url, timeout=30):
            class Resp:
                status_code = 200
                text = page_html
            return Resp()

    queue, summary = direct_links.process_album(Dummy(), 'seed-2016', {
        'slug': 'seed-2016',
        'tracks_complete': True,
        'tracks': [{'basename': '01.%20Track.mp3', 'formats': ['mp3', 'flac'], 'mp3_url': None}],
    })
    assert queue == [
        'https://lambda.vgmtreasurechest.com/soundtracks/seed-2016/hash/01.%20Track.mp3',
        'https://lambda.vgmtreasurechest.com/soundtracks/seed-2016/hash/01.%20Track.flac',
    ]
    assert summary['fetched_song_pages'] == 1


def test_old_schema_done_records_are_ignored(tmp_path):
    path = tmp_path / 'direct-links.jsonl'
    path.write_text(
        '{"album": "old", "status": "ok"}\n'
        '{"album": "new", "schema_version": 2, "status": "ok"}\n',
        encoding='utf-8',
    )
    assert direct_links.load_done_from_output(str(path)) == {'new'}
