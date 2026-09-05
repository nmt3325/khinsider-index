"""Small explicit live-v2 fixtures for the offline regression suite."""
import json
from pathlib import Path

import live_data

START = '2026-09-05T00:00:00Z'
OBSERVED = '2026-09-05T12:00:11Z'


def write_rows(path, rows):
    Path(path).write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')


def record(slug='alpha', title='Track', when=OBSERVED, count=1):
    return {
        'slug': slug, 'title': 'Album ' + slug, 'crawled_at': when,
        'data_source': live_data.SOURCE, 'status': 'ok', 'http_status': 200,
        'tracks_complete': True, 'track_count': count,
        'tracks': [{'title': title, 'basename': f'{i}.mp3', 'songid': str(i), 'num': i, 'disc': 1}
                   for i in range(1, count + 1)],
    }


def catalogue(directory, slugs=('alpha',), started=START):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    rows = directory / 'album-list.ndjson'
    write_rows(rows, [{'slug': slug, 'title': 'Album ' + slug, 'page': 1, 'crawled_at': started,
                       'platforms': ['Windows'], 'year': 2026, 'album_type': 'Gamerip'} for slug in slugs])
    return live_data.write_catalogue(rows, directory / 'catalogue.json', 1, len(slugs), started)


def ready(directory, slugs=('alpha',)):
    directory = Path(directory)
    catalogue(directory, slugs)
    write_rows(directory / 'album-meta.ndjson', [record(slug) for slug in slugs])
    live_data.atomic_json(directory / 'recent-state.json', {'version': 1, 'watermark': '2026-09-05',
                                                          'seen': {}, 'pending': {}})
    live_data.atomic_json(directory / 'discovery.json', {'data_source': live_data.SOURCE,
                                                       'listing_complete': True, 'recent_complete': True})
    return directory
